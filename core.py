# core.py
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from collections import deque
from typing import Deque, Dict, Optional, List

# ---- Config ----
@dataclass
class Config:
    pair: str = "XRPUSD"
    intervals: Dict[str, int] = None          # {"1m": 1}
    buffer: int = 2000                        # bars per timeframe kept in RAM
    unit: Decimal = Decimal("0.001")          # price step for grid etc.
    tz_offset: int = -6

    def __post_init__(self):
        if self.intervals is None:
            self.intervals = {"1m": 1}

# ---- Domain types ----
@dataclass
class Candle:
    time: datetime
    open: Decimal
    high: Decimal
    low:  Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal      # NEW
    trades: int        # NEW

# ---- Time helper ----
def local_today_to_utc(hour_local: int, minute_local: int, tz_offset_hours: int) -> datetime:
    """Return a UTC datetime for *today* at local HH:MM (fixed tz_offset)."""
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc + timedelta(hours=tz_offset_hours)
    target_local = now_local.replace(hour=hour_local, minute=minute_local, second=0, microsecond=0)
    target_utc = target_local - timedelta(hours=tz_offset_hours)
    return target_utc.replace(tzinfo=timezone.utc)

# ---- In-memory ring buffer per timeframe ----
class CandleSeries:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.times:   Deque[datetime] = deque(maxlen=capacity)
        self.opens:   Deque[Decimal]  = deque(maxlen=capacity)
        self.highs:   Deque[Decimal]  = deque(maxlen=capacity)
        self.lows:    Deque[Decimal]  = deque(maxlen=capacity)
        self.closes:  Deque[Decimal]  = deque(maxlen=capacity)
        self.vols:    Deque[Decimal]  = deque(maxlen=capacity)
        self.vwaps:   Deque[Decimal]  = deque(maxlen=capacity)  # NEW
        self.trades:  Deque[int]      = deque(maxlen=capacity)  # NEW

    def append(self, c: Candle) -> None:
        self.times.append(c.time)
        self.opens.append(c.open)
        self.highs.append(c.high)
        self.lows.append(c.low)
        self.closes.append(c.close)
        self.vols.append(c.volume)
        self.vwaps.append(c.vwap)      # NEW
        self.trades.append(c.trades)   # NEW

    def last_time(self) -> Optional[datetime]:
        return self.times[-1] if self.times else None

    def len(self) -> int:
        return len(self.times)

class CandleStore:
    """Fast RAM 'grid' of candles by timeframe. Strategy/UI read from here."""
    def __init__(self, cfg: Config):
        self.series: Dict[str, CandleSeries] = {
            tf: CandleSeries(cfg.buffer) for tf in cfg.intervals
        }

    def append(self, tf: str, c: Candle) -> None:
        self.series[tf].append(c)

    def last_time(self, tf: str) -> Optional[datetime]:
        return self.series[tf].last_time()

    def last_n_until(self, tf: str, until_utc: datetime, n: int) -> List[Candle]:
        """Return up to N candles with time <= until_utc, oldest→newest."""
        s = self.series[tf]
        if s.len() == 0 or n <= 0:
            return []
        out: List[Candle] = []
        for i in range(s.len() - 1, -1, -1):
            ct = s.times[i]
            if ct <= until_utc:
                out.append(Candle(
                    ct, s.opens[i], s.highs[i], s.lows[i], s.closes[i],
                    s.vols[i], s.vwaps[i], s.trades[i]
                ))
                if len(out) == n:
                    break
        return list(reversed(out))
