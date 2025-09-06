"""
patterns.py
------------
Simple, reusable chart "events" with a tiny registry so strategies can call them by name.
Input candles: list[dict] with keys: time, open, high, low, close, volume.

Usage from other files:
-----------------------
from patterns import PatternRegistry

events = PatternRegistry.evaluate_all(candles, only=[
    "gap", "range_breakout", "doji", "engulfing", "ma_crossover"
])

# or call one by name with custom params:
result = PatternRegistry.call("range_breakout", candles, lookback=20, direction="either")

Each pattern returns a dict like:
{
    "name": "range_breakout",
    "found": True/False,
    "index": int or None,       # index where it was detected (usually last bar)
    "direction": "bullish"/"bearish"/"either"/None,
    "meta": {...}               # extra info (levels, values, etc.)
}
"""

from dataclasses import dataclass
from typing import List, Dict, Callable, Optional, Any


# ---------- Helpers ----------

def _to_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return x  # if it's already numeric-like


def _sma(values: List[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def _last_two_sma(values: List[float], period: int):
    """Return (prev_sma, curr_sma) for given period."""
    if period <= 0 or len(values) < period + 1:
        return (None, None)
    prev = sum(values[-(period+1):-1]) / period
    curr = sum(values[-period:]) / period
    return (prev, curr)


def _close_series(candles: List[Dict]) -> List[float]:
    return [_to_float(c["close"]) for c in candles]


def _high_series(candles: List[Dict]) -> List[float]:
    return [_to_float(c["high"]) for c in candles]


def _low_series(candles: List[Dict]) -> List[float]:
    return [_to_float(c["low"]) for c in candles]


def _open_series(candles: List[Dict]) -> List[float]:
    return [_to_float(c["open"]) for c in candles]


def _body_size(candle: [Dict]) -> float:
    return abs(_to_float(candle["close"]) - _to_float(candle["open"]))


def _range_size(candle: [Dict]) -> float:
    return _to_float(candle["high"]) - _to_float(candle["low"])


# ---------- Registry ----------

class PatternRegistry:
    _reg: Dict[str, Callable] = {}

    @classmethod
    def register(cls, name: str, fn: Callable):
        cls._reg[name] = fn

    @classmethod
    def call(cls, name: str, candles: List[Dict], **params):
        fn = cls._reg.get(name)
        if not fn:
            raise ValueError(f"Pattern '{name}' not found.")
        return fn(candles, **params)

    @classmethod
    def list(cls) -> List[str]:
        return sorted(cls._reg.keys())

    @classmethod
    def evaluate_all(cls, candles: List[Dict], only: Optional[List[str]] = None, **shared_params):
        names = only if only else cls.list()
        results = {}
        for n in names:
            fn = cls._reg[n]
            results[n] = fn(candles, **shared_params)
        return results


def _pattern(name):
    """Decorator to add a function to the registry."""
    def deco(fn):
        PatternRegistry.register(name, fn)
        return fn
    return deco


# ---------- PATTERNS ----------

@_pattern("gap")
def pattern_gap(
    candles: List[Dict],
    min_pct: float = 0.1,
):
    """
    Detects a gap up or down on the latest bar.
    min_pct is % gap vs previous close (e.g., 0.1 => 0.1%).
    """
    if len(candles) < 2:
        return {"name": "gap", "found": False, "index": None, "direction": None, "meta": {}}

    prev = candles[-2]
    curr = candles[-1]
    prev_close = _to_float(prev["close"])
    curr_open = _to_float(curr["open"])
    if prev_close == 0:
        return {"name": "gap", "found": False, "index": None, "direction": None, "meta": {}}

    pct = (curr_open - prev_close) / prev_close * 100.0
    found = abs(pct) >= min_pct
    direction = "bullish" if pct > 0 else ("bearish" if pct < 0 else None)

    return {
        "name": "gap",
        "found": found,
        "index": len(candles) - 1 if found else None,
        "direction": direction if found else None,
        "meta": {"gap_pct": pct, "prev_close": prev_close, "curr_open": curr_open},
    }


@_pattern("range_breakout")
def pattern_range_breakout(
    candles: List[Dict],
    lookback: int = 20,
    direction: str = "either",  # "bullish" | "bearish" | "either"
):
    """
    Latest close breaks the highest high or lowest low of the prior `lookback` candles.
    """
    if len(candles) < lookback + 1:
        return {"name": "range_breakout", "found": False, "index": None, "direction": None, "meta": {}}

    highs = _high_series(candles)
    lows = _low_series(candles)
    closes = _close_series(candles)

    prior_high = max(highs[-(lookback+1):-1])
    prior_low = min(lows[-(lookback+1):-1])
    last_close = closes[-1]

    bull = last_close > prior_high
    bear = last_close < prior_low

    ok = (direction == "bullish" and bull) or \
         (direction == "bearish" and bear) or \
         (direction == "either" and (bull or bear))

    return {
        "name": "range_breakout",
        "found": ok,
        "index": len(candles) - 1 if ok else None,
        "direction": "bullish" if bull else ("bearish" if bear else None),
        "meta": {"prior_high": prior_high, "prior_low": prior_low, "last_close": last_close, "lookback": lookback},
    }


@_pattern("doji")
def pattern_doji(
    candles: List[Dict],
    body_ratio: float = 0.1,  # body <= 10% of total range
    min_range: float = 0.0    # optional absolute min range to avoid micro-ticks
):
    """
    Doji on the latest bar: small real body vs the candle range.
    """
    if not candles:
        return {"name": "doji", "found": False, "index": None, "direction": None, "meta": {}}

    c = candles[-1]
    rng = _range_size(c)
    body = _body_size(c)

    if rng <= 0 or rng < min_range:
        return {"name": "doji", "found": False, "index": None, "direction": None, "meta": {}}

    found = body <= rng * body_ratio
    direction = None  # doji is neutral

    return {
        "name": "doji",
        "found": found,
        "index": len(candles) - 1 if found else None,
        "direction": direction,
        "meta": {"body": body, "range": rng, "body_ratio": body_ratio},
    }


@_pattern("engulfing")
def pattern_engulfing(
    candles: List[Dict],
    direction: str = "either",  # "bullish" | "bearish" | "either"
):
    """
    Engulfing on the latest bar:
      - Bullish: prev red (close<open), curr green (close>open),
                 curr body engulfs prev body range.
      - Bearish: prev green, curr red, curr body engulfs prev body.
    """
    if len(candles) < 2:
        return {"name": "engulfing", "found": False, "index": None, "direction": None, "meta": {}}

    prev, curr = candles[-2], candles[-1]
    po, pc = _to_float(prev["open"]), _to_float(prev["close"])
    co, cc = _to_float(curr["open"]), _to_float(curr["close"])

    prev_green = pc > po
    prev_red = pc < po
    curr_green = cc > co
    curr_red = cc < co

    # define body bounds
    prev_low_body, prev_high_body = sorted([po, pc])
    curr_low_body, curr_high_body = sorted([co, cc])

    bull = prev_red and curr_green and (curr_low_body <= prev_low_body) and (curr_high_body >= prev_high_body)
    bear = prev_green and curr_red and (curr_low_body <= prev_low_body) and (curr_high_body >= prev_high_body)

    ok = (direction == "bullish" and bull) or \
         (direction == "bearish" and bear) or \
         (direction == "either" and (bull or bear))

    return {
        "name": "engulfing",
        "found": ok,
        "index": len(candles) - 1 if ok else None,
        "direction": "bullish" if bull else ("bearish" if bear else None),
        "meta": {"prev_body": (prev_low_body, prev_high_body), "curr_body": (curr_low_body, curr_high_body)},
    }


@_pattern("ma_crossover")
def pattern_ma_crossover(
    candles: List[Dict],
    fast: int = 9,
    slow: int = 21
):
    """
    Detect a fast/slow SMA crossover on the latest bar.
    - Bullish when fast crosses above slow.
    - Bearish when fast crosses below slow.
    """
    closes = _close_series(candles)
    if len(closes) < max(fast, slow) + 1:
        return {"name": "ma_crossover", "found": False, "index": None, "direction": None, "meta": {}}

    prev_fast, curr_fast = _last_two_sma(closes, fast)
    prev_slow, curr_slow = _last_two_sma(closes, slow)

    if None in (prev_fast, curr_fast, prev_slow, curr_slow):
        return {"name": "ma_crossover", "found": False, "index": None, "direction": None, "meta": {}}

    bull = (prev_fast is not None and prev_slow is not None and
            prev_fast <= prev_slow and curr_fast > curr_slow)
    bear = (prev_fast is not None and prev_slow is not None and
            prev_fast >= prev_slow and curr_fast < curr_slow)

    found = bull or bear
    direction = "bullish" if bull else ("bearish" if bear else None)

    return {
        "name": "ma_crossover",
        "found": found,
        "index": len(candles) - 1 if found else None,
        "direction": direction,
        "meta": {
            "prev_fast": prev_fast, "prev_slow": prev_slow,
            "curr_fast": curr_fast, "curr_slow": curr_slow,
            "fast": fast, "slow": slow
        },
    }


# Optional: quick self-test with fake candles
if __name__ == "__main__":
    # Tiny demo dataset (replace with your real 1m candles)
    demo = [
        {"time": 1, "open": 1.00, "high": 1.02, "low": 0.99, "close": 1.01, "volume": 1000},
        {"time": 2, "open": 1.01, "high": 1.03, "low": 1.00, "close": 1.02, "volume": 1200},
        {"time": 3, "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 2000},  # gap up
    ]
    print("Available patterns:", PatternRegistry.list())
    print(PatternRegistry.evaluate_all(demo))
