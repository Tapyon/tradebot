# data.py
import csv
import os
import time
import requests


from websocket import WebSocketApp
import json
import threading


from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core import Config, Candle, CandleStore

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


TICKER_URL = "https://api.kraken.com/0/public/Ticker"
OHLC_URL   = "https://api.kraken.com/0/public/OHLC"

# ---- Low-level Kraken REST client ----
class KrakenClient:
    def __init__(self, min_interval: float = 2.5, timeout: int = 10):
        self._last_call = 0.0
        self.min_interval = min_interval
        self.timeout = timeout
        self._build_session()

    def _build_session(self):
        self.session = requests.Session()
        retries = Retry(
            total=5,
            connect=3, read=3, status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def reset_session(self):
        try:
            self.session.close()
        except Exception:
            pass
        self._build_session()

    def _rate_limit(self):
        now = time.time()
        wait = self._last_call + self.min_interval - now
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _get(self, url: str, params: dict) -> dict:
        self._rate_limit()
        # tuple timeout: (connect, read)
        r = self.session.get(url, params=params, timeout=(5, self.timeout))
        r.raise_for_status()
        payload = r.json()
        if payload.get("error"):
            raise RuntimeError(", ".join(payload["error"]))
        return payload["result"]

    def get_ohlc(self, pair: str, interval: int, since: Optional[int] = None) -> List[Candle]:
        params = {"pair": pair, "interval": interval}
        if since is not None:
            params["since"] = since
        res = self._get(OHLC_URL, params)
        pair_key = next(iter(res))
        out: List[Candle] = []
        for row in res[pair_key]:
            ts  = int(row[0])
            o   = Decimal(row[1]); h = Decimal(row[2]); l = Decimal(row[3]); c = Decimal(row[4])
            vwap = Decimal(row[5]); vol = Decimal(row[6]); cnt = int(row[7])
            out.append(Candle(
                time=datetime.fromtimestamp(ts, tz=timezone.utc),
                open=o, high=h, low=l, close=c, volume=vol, vwap=vwap, trades=cnt,
            ))
        return out

    def get_last_price(self, pair: str) -> Decimal:
        res = self._get(TICKER_URL, {"pair": pair})
        pair_key = next(iter(res))
        return Decimal(res[pair_key]["c"][0])

# ---- High-level feed: incremental updates to the store ----
class DataFeed:
    def __init__(self, cfg: Config, client: KrakenClient, store: CandleStore):
        self.cfg = cfg
        self.client = client
        self.store = store
        self._last_ts: Dict[str, Optional[int]] = {tf: None for tf in cfg.intervals}

    def poll_once(self) -> None:
        now_min = datetime.now(timezone.utc).replace(second=0, microsecond=0)

        for tf, minutes in self.cfg.intervals.items():
            since = self._last_ts.get(tf)
            candles = self.client.get_ohlc(self.cfg.pair, minutes, since=since)

            # keep only CLOSED, valid candles
            safe = [
                c for c in candles
                if (c.time < now_min) and (c.trades > 0) and (c.volume > 0)
            ]

            last_time = self.store.last_time(tf)
            for c in safe:
                if (last_time is None) or (c.time > last_time):
                    self.store.append(tf, c)
                    last_time = c.time

            if last_time:
                self._last_ts[tf] = int(last_time.timestamp())

class CandleRecorder:
    def __init__(self, filepath: str = "candles_1m.csv", reset: bool = False):
        self.filepath = filepath
        new_file = reset or (not os.path.exists(filepath))
        mode = "w" if reset else "a"
        with open(filepath, mode, newline="") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(["time","open","high","low","close","volume","vwap","trades","label","timeframe"])

    def _write(self, c: Candle, label: str, timeframe: str = "1m"):
        with open(self.filepath, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                c.time.isoformat(), str(c.open), str(c.high), str(c.low), str(c.close),
                str(c.volume), str(c.vwap), int(c.trades), label, timeframe
            ])

    def append(self, candle: Candle, label: str, timeframe: str = "1m") -> None:
        now_min = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        if candle.time >= now_min or candle.trades <= 0 or candle.volume <= 0:
            return  # skip live/incomplete
        self._write(candle, label, timeframe)

    def append_many(self, candles: List[Candle], labels: List[str], timeframe: str = "1m") -> None:
        now_min = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        for c, lab in zip(candles, labels):
            if c.time < now_min and c.trades > 0 and c.volume > 0:
                self._write(c, lab, timeframe)
class KrakenWS:
    """
    Minimal Kraken public WebSocket client for live price:
      - subscribe to 'trade' (every executed trade)
      - subscribe to 'ticker' (best bid/ask + last)
    Calls your callbacks on each message.
    """

    WS_URL = "wss://ws.kraken.com/"

    def __init__(self, pair: str, on_trade=None, on_ticker=None, on_status=None):
        # Normalize pair to WS style (e.g., "XRPUSD" -> "XRP/USD")
        self.pair_raw = pair
        self.ws_pair = pair if "/" in pair else f"{pair[:-3]}/{pair[-3:]}"
        self.on_trade = on_trade      # fn(price: Decimal, ts: datetime)
        self.on_ticker = on_ticker    # fn(bid: Decimal, ask: Decimal, last: Decimal, ts: datetime)
        self.on_status = on_status    # fn(text: str)
        self._ws = None
        self._thread = None
        self._lock = threading.Lock()
        self._running = False

    # ---- Public API ----
    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    # ---- Internals ----
    def _run(self):
        def _on_open(ws):
            try:
                if self.on_status: self.on_status("ws open")
                # subscribe to trade + ticker for the normalized pair
                sub_trade  = {"event": "subscribe", "pair": [self.ws_pair], "subscription": {"name": "trade"}}
                sub_ticker = {"event": "subscribe", "pair": [self.ws_pair], "subscription": {"name": "ticker"}}
                ws.send(json.dumps(sub_trade))
                ws.send(json.dumps(sub_ticker))
            except Exception:
                pass

        def _on_message(ws, msg):
            try:
                data = json.loads(msg)
            except Exception:
                return

            # system/heartbeat events come as dicts
            if isinstance(data, dict):
                ev = data.get("event")
                if self.on_status and ev:
                    # surface useful status
                    if ev == "subscriptionStatus":
                        chan = (data.get("subscription") or {}).get("name")
                        status = data.get("status")
                        pair = data.get("pair")
                        self.on_status(f"ws {chan} {status} {pair}")
                    else:
                        self.on_status(f"ws {ev}")
                return

            # channel messages: [channelID, payload, channelName, pair]
            if isinstance(data, list) and len(data) >= 4:
                channel = data[2]
                if channel == "trade":
                    # payload: list of trades [[price, volume, time, ...], ...]
                    for t in data[1]:
                        price = Decimal(t[0])
                        ts = datetime.fromtimestamp(float(t[2]), tz=timezone.utc)
                        if self.on_trade:
                            self.on_trade(price, ts)
                elif channel == "ticker":
                    # payload: dict with bid/ask/last arrays
                    p = data[1]
                    bid = Decimal(p["b"][0])
                    ask = Decimal(p["a"][0])
                    last = Decimal(p["c"][0])
                    ts = datetime.now(timezone.utc)
                    if self.on_ticker:
                        self.on_ticker(bid, ask, last, ts)

        def _on_error(ws, err):
            if self.on_status:
                self.on_status(f"ws error: {err}")

        def _on_close(ws, code, reason):
            if self.on_status:
                self.on_status(f"ws closed: {code} {reason}")

        # loop with simple reconnect
        while True:
            with self._lock:
                if not self._running:
                    break
            try:
                self._ws = WebSocketApp(
                    self.WS_URL,
                    on_open=_on_open,
                    on_message=_on_message,
                    on_error=_on_error,
                    on_close=_on_close,
                )
                # keepalive pings help avoid idle disconnects
                self._ws.run_forever(ping_interval=15, ping_timeout=10)
            except Exception as e:
                if self.on_status:
                    self.on_status(f"ws run error: {e}")
            # small backoff before reconnect
            time.sleep(2)
