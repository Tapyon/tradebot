# strategies.py
"""
Breakout 2x1 Strategy (LONG/SHORT)
- Triggers (after the reference minute):
  * LONG  when a 1m candle CLOSES above the blue HIGH
  * SHORT when a 1m candle OPENS  below the blue LOW
- One open position at a time (configurable).
- Entry/Stop/Target computed from a fixed risk step (e.g., 0.001) and 2:1 RR.
- Writes a journal CSV with entries/exits/win/loss.
- Draws violet Entry/Stop/Target lines on the UI only while a position is active.
"""

import csv
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from datetime import datetime, timezone

from core import Candle, Config, CandleStore

# =========================
# REFERENCES — EDIT HERE
# =========================
JOURNAL_FILE            = "strategy_log.csv"
MAX_CONCURRENT_POS      = 1
TRADE_CAPITAL           = Decimal("100")   # how much capital per trade
RISK_REWARD             = Decimal("2")     # 2x1
RISK_STEP_STR           = "0.001"          # per-trade risk step (Δprice). Use cfg.unit if you prefer.
PRIORITIZE_STOP_ON_TIE  = True             # if both target & stop hit same candle, count as stop first
# =========================


@dataclass
class Position:
    side: str                     # "long" | "short"
    entry: Decimal
    stop: Decimal
    target: Decimal
    capital: Decimal
    opened_at: datetime
    exit_price: Optional[Decimal] = None
    closed_at: Optional[datetime] = None
    outcome: Optional[str] = None # "win" | "loss"


class Journal:
    def __init__(self, filepath: str = JOURNAL_FILE, reset: bool = False):
        self.filepath = filepath
        new_file = reset or (not os.path.exists(filepath))
        mode = "w" if reset else "a"
        with open(filepath, mode, newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow([
                    "opened_at","side","entry","stop","target","capital",
                    "closed_at","exit_price","outcome","note"
                ])

    def log_entry(self, p: Position, note: str = ""):
        with open(self.filepath, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                p.opened_at.isoformat(), p.side, str(p.entry), str(p.stop),
                str(p.target), str(p.capital), "", "", "", note
            ])

    def log_exit(self, p: Position, note: str = ""):
        with open(self.filepath, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                p.opened_at.isoformat(), p.side, str(p.entry), str(p.stop),
                str(p.target), str(p.capital),
                p.closed_at.isoformat() if p.closed_at else "",
                str(p.exit_price) if p.exit_price is not None else "",
                p.outcome or "", note
            ])


class BreakoutStrategy:
    """
    Minimal strategy object to be called from tradebot's poll loop.
    """
    def __init__(self, cfg: Config, store: CandleStore, ui, journal: Journal, ref_utc: datetime):
        self.cfg = cfg
        self.store = store
        self.ui = ui
        self.journal = journal
        self.ref_utc = ref_utc

        self.blue_high: Optional[Decimal] = None
        self.blue_low: Optional[Decimal] = None
        self.active: Optional[Position] = None

        self.max_positions = MAX_CONCURRENT_POS
        self.risk_step = Decimal(RISK_STEP_STR)  # could also use cfg.unit
        self.rr = RISK_REWARD
        self.capital = TRADE_CAPITAL

    def on_live_price(self, price: Decimal, ts: datetime):
        """
        Called on each yellow-dot (live) tick.
        If a position is active, exit immediately when STOP or TARGET is touched.
        """
        p = self.active
        if not p:
            return

        if p.side == "long":
            if price >= p.target:
                self._exit("win", price=p.target, ts=ts)
                return
            if price <= p.stop:
                self._exit("loss", price=p.stop, ts=ts)
                return
        else:  # short
            if price <= p.target:
                self._exit("win", price=p.target, ts=ts)
                return
            if price >= p.stop:
                self._exit("loss", price=p.stop, ts=ts)
                return


    # call after blue lines are known
    def set_blue(self, high: Decimal, low: Decimal):
        self.blue_high, self.blue_low = high, low

    # called on each NEWLY CLOSED 1m candle

    def on_new_close(self, c: Candle):
        # include the ref-minute bar (>= ref_utc)
        if c.time < self.ref_utc:
            return
        # must have blue lines
        if self.blue_high is None or self.blue_low is None:
            return

        # if already in a trade, manage it (live price will also manage exits)
        if self.active:
            self._manage_position(c)
            return

        # only one at a time (configurable)
        if self.max_positions <= 0:
            return

        # ---- ENTRY RULES (OPEN or CLOSE crossing) ----
        # LONG
        if (c.open > self.blue_high) or (c.close > self.blue_high):
            basis = "OPEN" if c.open > self.blue_high else "CLOSE"
            entry_price = c.open if basis == "OPEN" else c.close
            self._enter(side="long", price=entry_price, ts=c.time, note=f"basis={basis}")
            return

        # SHORT
        if (c.open < self.blue_low) or (c.close < self.blue_low):
            basis = "OPEN" if c.open < self.blue_low else "CLOSE"
            entry_price = c.open if basis == "OPEN" else c.close
            self._enter(side="short", price=entry_price, ts=c.time, note=f"basis={basis}")
            return

    # ---- internals ----

    
    def _enter(self, side: str, price: Decimal, ts: datetime, note: str = ""):
        if self.active:
            return
        if self.max_positions < 1:
            return

        if side == "long":
            entry = price
            stop = entry - self.risk_step
            target = entry + self.rr * self.risk_step
        else:  # short
            entry = price
            stop = entry + self.risk_step
            target = entry - self.rr * self.risk_step

        self.active = Position(
            side=side, entry=entry, stop=stop, target=target,
            capital=self.capital, opened_at=ts
        )

        # UI violet lines
        try:
            if hasattr(self.ui, "set_position_levels"):
                self.ui.set_position_levels(entry, stop, target, side)
        except Exception:
            pass

        # Log (include basis note if provided)
        local_hhmm = (ts + self._tz_delta()).strftime("%H:%M")
        msg = f"ENTER {side.upper()} @ {entry} | stop {stop} | target {target} | cap {self.capital} [{local_hhmm}]"
        if note:
            msg += f" {note}"
        self._uilog(msg)

        self.journal.log_entry(self.active, note=("entered " + note).strip())


    def _manage_position(self, c: Candle):
        p = self.active
        if not p:
            return

        # conservative tie-breaking: check STOP first if configured
        hit_target = False
        hit_stop = False

        if p.side == "long":
            hit_stop = c.low <= p.stop
            hit_target = c.high >= p.target
        else:  # short
            hit_stop = c.high >= p.stop
            hit_target = c.low <= p.target

        exit_now = False
        if PRIORITIZE_STOP_ON_TIE:
            if hit_stop:
                self._exit("loss", price=p.stop, ts=c.time)
                exit_now = True
            elif hit_target:
                self._exit("win", price=p.target, ts=c.time)
                exit_now = True
        else:
            if hit_target:
                self._exit("win", price=p.target, ts=c.time)
                exit_now = True
            elif hit_stop:
                self._exit("loss", price=p.stop, ts=c.time)
                exit_now = True

        if exit_now:
            try:
                if hasattr(self.ui, "clear_position_levels"):
                    self.ui.clear_position_levels()
            except Exception:
                pass

    def _exit(self, outcome: str, price: Decimal, ts: datetime):
        if not self.active:
            return
        self.active.exit_price = price
        self.active.outcome = outcome
        self.active.closed_at = ts
        self.journal.log_exit(self.active, note="exit")
        local_hhmm = (ts + self._tz_delta()).strftime("%H:%M")
        self._uilog(f"EXIT {outcome.upper()} @ {price} [{local_hhmm}]")

        # clear UI overlays
        try:
            if hasattr(self.ui, "clear_position_levels"):
                self.ui.clear_position_levels()
        except Exception:
            pass

        self.active = None

    def _uilog(self, msg: str):
        try:
            self.ui.log(msg)
        except Exception:
            pass

    def _tz_delta(self):
        from datetime import timedelta
        return timedelta(hours=self.cfg.tz_offset)
