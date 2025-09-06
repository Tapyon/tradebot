# tradebot.py
from core import (
    Config,
    CandleStore,
    local_today_to_utc,
    Candle,
)
from data import KrakenClient, DataFeed, CandleRecorder

from data import KrakenWS

# UI
import signal

import threading
import tkinter as tk
from ui_chart import MiniChart

# NEW: patterns + strategy
from patterns import PatternRegistry
from strategies import BreakoutStrategy, Journal
import strategies as _strat_mod  # debug
print("[debug] strategies file ->", _strat_mod.__file__)
print("[debug] BreakoutStrategy has on_live_price ->", hasattr(BreakoutStrategy, "on_live_price"))

import time
import csv
from decimal import Decimal
from datetime import datetime, timezone, timedelta



# ---- INPUTS ----
TIMEZONE_OFFSET        = -6
REF_LOCAL_HOUR         = 7
REF_LOCAL_MINUTE       = 35
RESET_STORAGE_ON_START = True
STORAGE_FILE           = "candles_1m.csv"
UNIT_STR               = "0.0025"
##  LIVE_PRICE_REFRESH_SEC = 1      #disable for now (rest fetch of live price )

# ---- VERIFY (edit here) ----
VERIFY_ENABLED               = True
VERIFY_EVERY_SEC             = 20   # periodic background verify
VERIFY_ON_CLOSE_DELAY_SEC    = 5    # verify 5s after a bar closes

# ---- PATTERN ENGINE (edit here) ----
ENABLE_PATTERNS    = True
PATTERNS_TO_CHECK  = ["gap", "range_breakout", "doji", "engulfing", "ma_crossover"]
PATTERN_LOG_MODE   = "all"   # "gap_only" or "all"

# Per-pattern knobs
GAP_MIN_PCT        = 0.10         # % vs prev close (0.10 => 0.10%)
RB_LOOKBACK        = 20
RB_DIRECTION       = "either"     # "bullish" | "bearish" | "either"
DOJI_BODY_RATIO    = 0.10
DOJI_MIN_RANGE     = 0.0
ENG_DIRECTION      = "either"
MA_FAST            = 9
MA_SLOW            = 21

# ---- STRATEGIES TOGGLES (edit here) ----
ENABLE_BREAKOUT = True    # set False to disable the Breakout 2x1 strategy










def main():
    # ---------------- config + store ----------------
    cfg = Config(pair="XRPUSD", intervals={"1m": 1}, buffer=1000,
                 unit=Decimal(UNIT_STR), tz_offset=TIMEZONE_OFFSET)
    store = CandleStore(cfg)

    # ---------------- connectivity ----------------
    client = KrakenClient(min_interval=2.5)
    feed = DataFeed(cfg, client, store)

    # ---------------- recorder ----------------
    recorder = CandleRecorder(filepath=STORAGE_FILE, reset=RESET_STORAGE_ON_START)

    # ---------------- anchor time ----------------
    ref_utc = local_today_to_utc(REF_LOCAL_HOUR, REF_LOCAL_MINUTE, cfg.tz_offset)
    print(f"Anchor @ local {REF_LOCAL_HOUR:02d}:{REF_LOCAL_MINUTE:02d} (UTC {ref_utc.strftime('%H:%M')})")

    # clamp only for priming/backfill display; lazy blue uses requested ref_utc
    now_closed = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    eff_ref_utc = min(ref_utc, now_closed)
    print(f"Anchor @ local {REF_LOCAL_HOUR:02d}:{REF_LOCAL_MINUTE:02d} (effective UTC {eff_ref_utc.strftime('%H:%M')})")

    # ---------------- PRIME: last 10 up to eff_ref_utc ----------------
    since_ts = int(eff_ref_utc.timestamp()) - 15 * 60
    primed = [c for c in client.get_ohlc(cfg.pair, 1, since=since_ts) if c.time <= eff_ref_utc][-10:]
    for c in primed:
        store.append("1m", c)

    if primed:
        from itertools import count
        labels = [f"REF_T-{(len(primed)-1 - i)}" for i in range(len(primed))]
        recorder.append_many(primed, labels, timeframe="1m")
        print(f"Primed {len(primed)} candles up to {eff_ref_utc.strftime('%H:%M')} UTC")
    else:
        print("No primed candles found; will start recording from first live close.")

    # ---------------- POST-REF backfill once (eff_ref_utc -> now) ----------------
    post_since = primed[-1].time if primed else eff_ref_utc
    post = [c for c in client.get_ohlc(cfg.pair, 1, since=int(post_since.timestamp())) if c.time > post_since]
    for c in post:
        store.append("1m", c)
    if post:
        from itertools import count
        for idx, c in zip(count(0), post):
            recorder.append(c, f"POST_{idx:04d}", timeframe="1m")

    # continuation + recorder watermark for poll loop
    last_time = store.series["1m"].last_time()
    feed._last_ts["1m"] = int(last_time.timestamp()) if last_time else int(eff_ref_utc.timestamp())
    last_written_time = last_time

    # ---------------- UI ----------------
    root = tk.Tk()
    root.title("Tradebot - Mini Chart 1m")
    ui = MiniChart(root, store, cfg)
    ui.set_anchor_time(eff_ref_utc)

    base_inputs = {
        "Pair": cfg.pair,
        "Unit": str(cfg.unit),
        "TZ": f"{cfg.tz_offset:+d}",
        "Ref": f"{REF_LOCAL_HOUR:02d}:{REF_LOCAL_MINUTE:02d}",
        "Seed": "10@refEff + post->now",
        "Reset": str(RESET_STORAGE_ON_START),
        "Blue(H/L@Ref,5x1m)": "— / — (pending)",
    }
    ui.set_inputs(base_inputs)

     # ---------------- STRATEGY (conditional) ----------------
    strategy = None
    if ENABLE_BREAKOUT:
        journal = Journal(filepath="strategy_log.csv", reset=False)
        strategy = BreakoutStrategy(cfg, store, ui, journal, ref_utc=ref_utc)

    # show status in the header
    base_inputs["Strat:Breakout"] = "ON" if ENABLE_BREAKOUT else "OFF"
    ui.set_inputs(base_inputs)

    # ---------------- LAZY BLUE LINES (requested ref, exact 5 bars) ----------------
    blue_set = False





    # ---------------- WEBSOCKET LIVE PRICE (primary) ----------------

    def _ws_status(msg: str):
        # ignore noisy heartbeats
        if "heartbeat" in msg.lower():
            return
        try:
            ui.log(msg)
        except Exception:
            pass

    def _ws_trade(price, ts):
        def _apply(px=price, t=ts):
            ui.set_live_price(px)
            ui.draw()  # immediate redraw for the yellow dot
            if strategy is not None and hasattr(strategy, "on_live_price"):
                try:
                    strategy.on_live_price(px, t)
                except Exception as err:
                    ui.log(f"strategy live error: {err}")
        root.after(0, _apply)

    def _ws_ticker(bid, ask, last, ts):
        def _apply(px=last, t=ts):
            ui.set_live_price(px)
            ui.draw()  # keep UI fresh even if no trades for a moment
        root.after(0, _apply)

    ws = KrakenWS(cfg.pair, on_trade=_ws_trade, on_ticker=_ws_ticker, on_status=_ws_status)
    ws.start()






    def try_set_blue():
        nonlocal blue_set, base_inputs
        if blue_set:
            return
        # we require exactly the five 1m candles with start times in [ref_utc-5m, ref_utc)
        start = ref_utc - timedelta(minutes=5)
        end_inclusive = ref_utc - timedelta(minutes=1)  # last bar starts at ref_utc-1m
        s = store.series["1m"]
        # collect the window in order
        idxs = []
        for i, ts in enumerate(s.times):
            if start <= ts <= end_inclusive:
                idxs.append(i)
        if len(idxs) == 5 and s.times[idxs[-1]] == end_inclusive:
            highs = [s.highs[i] for i in idxs]
            lows  = [s.lows[i]  for i in idxs]
            bh = max(highs)
            bl = min(lows)
            blue_set = True
            ui.set_ref_levels(bh, bl)

             # IF ITS ON - OFF
            if strategy is not None:
                strategy.set_blue(bh, bl)  
                # let the strategy know the blue levels
            try:
                strategy.set_blue(bh, bl)
            except Exception as e:
                ui.log(f"strategy set_blue error: {e}")


            ui.log(
                "Blue lines set from last 5×1m ending at "
                f"{(ref_utc + timedelta(hours=cfg.tz_offset)).strftime('%H:%M')} "
                f"→ HIGH={bh} LOW={bl}"
            )
            # refresh header to show the values
            base_inputs = dict(base_inputs)
            base_inputs["Blue(H/L@Ref,5x1m)"] = f"{bh} / {bl}"
            ui.set_inputs(base_inputs)

    # ---- verify last 5 closed candles (fetch → compare → patch store → log) ----
    def verify_last5_and_fix():
        try:
            now_min = datetime.now(timezone.utc).replace(second=0, microsecond=0)

            # fetch ~10m, keep only CLOSED & valid rows
            since = int((now_min - timedelta(minutes=10)).timestamp())
            fetched = [
                c for c in client.get_ohlc(cfg.pair, 1, since=since)
                if (c.time < now_min and c.trades > 0 and c.volume > 0)
            ]
            fetched = fetched[-5:]

            s1 = store.series["1m"]
            if s1.len() < 5 or len(fetched) < 5:
                return

            # indices for the last 5 in store
            idxs = list(range(s1.len() - 5, s1.len()))
            mismatches = []

            def _neq(a, b): return abs(float(a) - float(b)) > 1e-9

            for i, idx in enumerate(idxs):
                a = fetched[i]
                if (_neq(s1.times[idx].timestamp(), a.time.timestamp()) or
                    _neq(s1.opens[idx],  a.open) or
                    _neq(s1.highs[idx], a.high) or
                    _neq(s1.lows[idx],  a.low)  or
                    _neq(s1.closes[idx],a.close) or
                    _neq(s1.vols[idx],  a.volume) or
                    (int(s1.trades[idx]) != int(a.trades))):
                    mismatches.append((idx, a))

            if not mismatches:
                root.after(0, ui.log, "verify last5: OK")
                return

            # patch store + write corrections audit
            corr_path = STORAGE_FILE.replace(".csv", "_corrections.csv")
            with open(corr_path, "a", newline="") as f:
                w = csv.writer(f)
                for idx, a in mismatches:
                    s1.opens[idx]  = a.open
                    s1.highs[idx]  = a.high
                    s1.lows[idx]   = a.low
                    s1.closes[idx] = a.close
                    s1.vols[idx]   = a.volume
                    s1.vwaps[idx]  = a.vwap
                    s1.trades[idx] = a.trades
                    s1.times[idx]  = a.time
                    w.writerow([a.time.isoformat(), a.open, a.high, a.low, a.close,
                                a.volume, a.vwap, a.trades, "CORRECTED", "1m"])
            root.after(0, ui.log, f"verify last5: fixed {len(mismatches)}")
        except Exception as e:
            root.after(0, ui.log, f"verify error: {e}")
    
    # ---- Patterns: helpers ----
    def _candles_for_patterns():
        """All CLOSED 1m candles as list[dict] for patterns.py."""
        s1 = store.series["1m"]
        n = s1.len()
        out = []
        for i in range(n):
            out.append({
                "time":   s1.times[i],
                "open":   s1.opens[i],
                "high":   s1.highs[i],
                "low":    s1.lows[i],
                "close":  s1.closes[i],
                "volume": s1.vols[i],
            })
        return out

    def _pattern_params(name: str):
        if name == "gap":
            return {"min_pct": GAP_MIN_PCT}
        if name == "range_breakout":
            return {"lookback": RB_LOOKBACK, "direction": RB_DIRECTION}
        if name == "doji":
            return {"body_ratio": DOJI_BODY_RATIO, "min_range": DOJI_MIN_RANGE}
        if name == "engulfing":
            return {"direction": ENG_DIRECTION}
        if name == "ma_crossover":
            return {"fast": MA_FAST, "slow": MA_SLOW}
        return {}

    def _log_pattern_result(name: str, res: dict, cds: list):
        if not res.get("found"):
            return
        idx = res.get("index")
        # Special: GAP → “between candles X and Y”
        if name == "gap":
            x = (idx - 1) if (idx is not None) else len(cds) - 2
            y = idx if (idx is not None) else len(cds) - 1
            if x is None or y is None or x < 0 or y < 0:
                return
            t_x = cds[x]["time"]; t_y = cds[y]["time"]
            t_xs = (t_x + timedelta(hours=cfg.tz_offset)).strftime("%H:%M")
            t_ys = (t_y + timedelta(hours=cfg.tz_offset)).strftime("%H:%M")
            direction = res.get("direction")
            pct = res.get("meta", {}).get("gap_pct", 0.0)
            ui.log(f"GAP {direction} {pct:.3f}% between candles {x}({t_xs}) and {y}({t_ys})")
            return

        # Others: only log if PATTERN_LOG_MODE == "all"
        if PATTERN_LOG_MODE == "all":
            use_idx = idx if idx is not None else (len(cds) - 1)
            t = cds[use_idx]["time"]
            t_str = (t + timedelta(hours=cfg.tz_offset)).strftime("%H:%M")
            direction = res.get("direction")
            ui.log(f"{name.upper()} detected at {t_str} ({direction})")

    def run_patterns_on_latest():
        if not ENABLE_PATTERNS:
            return
        cds = _candles_for_patterns()
        if len(cds) < 2:
            return
        for name in PATTERNS_TO_CHECK:
            params = _pattern_params(name)
            res = PatternRegistry.call(name, cds, **params)
            _log_pattern_result(name, res, cds)

















    # one attempt at startup (may be pending)
    try_set_blue()

    # ---------------- POLL: append newest closed 1m + log + lazy blue ----------------
    # ---------------- STOP/THREADS ----------------
    STOP_EVENT = threading.Event()

    # POLL: append newest closed 1m + log + lazy blue
    def poll_loop():
        nonlocal last_written_time
        live_label_counter = 0
        while not STOP_EVENT.is_set():
            try:
                feed.poll_once()
                s1 = store.series["1m"]
                if s1.len():
                    ct = s1.times[-1]
                    if (last_written_time is None) or (ct > last_written_time):
                        c = Candle(
                            ct,
                            s1.opens[-1], s1.highs[-1], s1.lows[-1], s1.closes[-1],
                            s1.vols[-1], s1.vwaps[-1], s1.trades[-1]
                        )
                        recorder.append(c, f"LIVE_{live_label_counter:04d}", timeframe="1m")
                        live_label_counter += 1
                        last_written_time = ct
                        local_hhmm = (ct + timedelta(hours=cfg.tz_offset)).strftime("%H:%M")
                        root.after(0, ui.log, f"1m candle {local_hhmm} closed")
                        


                        # verify 5s after this bar closes (checks last 5 closed candles)
                        if VERIFY_ENABLED:
                            threading.Timer(VERIFY_ON_CLOSE_DELAY_SEC, verify_last5_and_fix).start()


                        # (optional) force immediate redraw so the new candle’s arrow shows now
                        root.after(0, ui.draw)

                        # patterns: evaluate on the finished bar
                        root.after(0, run_patterns_on_latest)

                        # strategy: feed newest closed candle (if enabled)
                        if strategy is not None:
                            try:
                                strategy.on_new_close(c)
                            except Exception as err:
                                root.after(0, ui.log, f"strategy close error: {err}")

                        # check blue lines only after new closes
                        if not blue_set:
                            root.after(0, try_set_blue)

                        root.after(0, ui.draw)  # force show arrow/dots now
                        if strategy is not None:
                            try:
                                strategy.on_new_close(c)
                            except Exception as err:
                                root.after(0, ui.log, f"strategy close error: {err}")
                        if not blue_set:
                            root.after(0, try_set_blue)
            except Exception as e:
                print("poll error:", e)
                time.sleep(8)
            else:
                time.sleep(10)


    # Disable ONLY the REST live loop
    if False:
        def live_loop():
            backoff = 0
            while not STOP_EVENT.is_set():
                try:
                    px = client.get_last_price(cfg.pair)
                    ts = datetime.now(timezone.utc)

                    def _apply(px=px, ts=ts):
                        ui.set_live_price(px)
                        if strategy is not None and hasattr(strategy, "on_live_price"):
                            try:
                                strategy.on_live_price(px, ts)
                            except Exception as err:
                                ui.log(f"strategy live error: {err}")

                    root.after(0, _apply)
                    backoff = 0
                    time.sleep(LIVE_PRICE_REFRESH_SEC)

                except Exception as e:
                    ui.log(f"live price error: {e}")
                    try:
                        client.reset_session()  # safe reconnect
                    except Exception:
                        pass
                    backoff = min(backoff + 1, 5)
                    time.sleep(LIVE_PRICE_REFRESH_SEC + 2 * backoff)

    # KEEP verify_loop ACTIVE and OUTSIDE the if False:
    def verify_loop():
        while not STOP_EVENT.is_set() and VERIFY_ENABLED:
            try:
                verify_last5_and_fix()
            except Exception as e:
                root.after(0, ui.log, f"verify loop error: {e}")
            time.sleep(VERIFY_EVERY_SEC)

    # --- start threads ---
    poll_thread   = threading.Thread(target=poll_loop,   daemon=True)
    # live_thread   = threading.Thread(target=live_loop,   daemon=True)  # DISABLED (WS is primary)
    verify_thread = threading.Thread(target=verify_loop, daemon=True)

    poll_thread.start()
    # live_thread.start()   # DISABLED
    verify_thread.start()

    # graceful window close (idempotent)
    closing = False
    def on_close():
        nonlocal closing
        if closing:
            return
        closing = True

        STOP_EVENT.set()
        try:
            # stop websocket (if defined)
            try:
                ws.stop()
            except Exception:
                pass

            # join threads you started
            poll_thread.join(timeout=1.5)
            # live_thread is disabled, so don't join it
            verify_thread.join(timeout=1.5)
        except Exception:
            pass

        # IMPORTANT: quit the Tk loop here, don't destroy yet
        try:
            if root and root.winfo_exists():
                root.quit()
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", on_close)

    # allow Ctrl+C in console to also trigger clean shutdown
    signal.signal(signal.SIGINT, lambda *a: root.after(0, on_close))

    # ---------------- GO ----------------
    try:
        root.mainloop()
    except KeyboardInterrupt:
        # If Ctrl+C slips past, request close once.
        root.after(0, on_close)
    finally:
        # Now the loop is over; it's safe to actually destroy the window once.
        try:
            if root and root.winfo_exists():
                root.destroy()
        except Exception:
            pass

if __name__ == "__main__":
    main()
