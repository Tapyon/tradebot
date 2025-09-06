# ui_chart.py  (minimal + tunables)
import tkinter as tk
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Optional
from patterns import PatternRegistry


# =========================
# TUNABLES — EDIT HERE
# =========================
UI_WIDTH  = 980
UI_HEIGHT = 650

# margins around the drawable plot
MARGINS = {"left": 72, "right": 120, "top": 24, "bottom": 32}

# bottom log box (rows/cols) + font
LOG_HEIGHT = 10
LOG_WIDTH  = 92
LOG_FONT   = ("Consolas", 10)

# axis/labels fonts
AXIS_FONT  = ("Consolas", 10)
TIME_FONT  = ("Consolas", 16, "bold")
LIVE_FONT  = ("Consolas", 16, "bold")

# X-axis time-stamps frequency (minutes): 1, 5, 10, ...
X_LABEL_EVERY_MIN = 5

# Price grid: how many levels up/down from center, and step size.
# If PRICE_STEP_OVERRIDE is None, we use cfg.unit from tradebot.
PRICE_STEP_OVERRIDE: Optional[Decimal] = 0.001  # e.g., Decimal("0.001")
GRID_STEPS = 7

# Redraw / clock cadence (ms)
REDRAW_MS = 800
CLOCK_MS  = 1000

# add this new pad for price labels
PRICE_LABEL_PAD = 22

# Dot radii
HIGH_DOT_R = 4
LOW_DOT_R  = 4
LIVE_DOT_R = 4
# =========================
# Position overlay (violet)
POS_COLOR  = "#b36bff"
POS_WIDTH  = 1


# Shift live (yellow) dot a bit right so it never overlaps last closed dot
LIVE_X_OFFSET_PX = 8


# Candle arrows
SHOW_CANDLE_ARROWS = True
ARROW_UP_COLOR     = "#41ff77"
ARROW_DOWN_COLOR   = "#ff4d4d"
ARROW_WIDTH        = 2
ARROW_SHAPE        = (10, 12, 6)  # (length, width, wing)


class MiniChart:
    """
    Minimal chart:
      - margin-safe plot area
      - green/red dots for 1m highs/lows
      - optional blue lines (set_ref_levels)
      - yellow live dot (set_live_price)
      - bottom: left LOG box, right TIMER + LIVE price + ref time

    Reads candles from store.series["1m"] only.
    """

    def __init__(self, root: tk.Tk, store, *args, width=UI_WIDTH, height=UI_HEIGHT):
        """
        Accepts (root, store, cfg) or (root, store, portfolio, cfg).
        The last positional arg is assumed to be cfg.
        """
        self.root = root
        self.store = store
        self.cfg = args[-1]  # cfg is last arg regardless of shape

        # ---- references from tunables ----
        self.width, self.height = width, height
        self.margins = dict(MARGINS)  # copy
        self.log_height = LOG_HEIGHT
        self.log_width  = LOG_WIDTH
        self.log_font   = LOG_FONT
        self.axis_font  = AXIS_FONT
        self.time_font  = TIME_FONT
        self.live_font  = LIVE_FONT
        self.x_label_every_min = X_LABEL_EVERY_MIN
        self.grid_steps = GRID_STEPS
        self.redraw_ms  = REDRAW_MS
        self.clock_ms   = CLOCK_MS
        self.high_dot_r = HIGH_DOT_R
        self.low_dot_r  = LOW_DOT_R
        self.live_dot_r = LIVE_DOT_R

        # price step (Decimal)
        self.price_step: Decimal = (
            PRICE_STEP_OVERRIDE if PRICE_STEP_OVERRIDE is not None else self.cfg.unit
        )

        # anchor / overlays
        self.anchor_utc: Optional[datetime] = None
        self.ref_high: Optional[Decimal] = None
        self.ref_low: Optional[Decimal] = None
        self.live_price: Optional[Decimal] = None

        # active position overlay (violet)
        self.pos_entry = None
        self.pos_stop  = None
        self.pos_target = None
        self.pos_side   = None  # "long" or "short" for optional label


        # ---- UI layout ----
        self.canvas = tk.Canvas(root, width=self.width, height=self.height,
                                bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill="both", expand=False)

        bottom = tk.Frame(root, bg="#111111"); bottom.pack(fill="x")

        # LOG (left)
        self.log_box = tk.Text(
            bottom, height=self.log_height, width=self.log_width,
            bg="#111111", fg="#eaeaea", font=self.log_font
        )
        self.log_box.pack(side="left", fill="x", expand=True, padx=(8,4), pady=(6,6))

        # TIMER + LIVE (right)
        side = tk.Frame(bottom, bg="#111111", bd=1, relief="ridge")
        side.pack(side="right", padx=(4,8), pady=(6,6))

        tk.Label(side, text="TIME", bg="#111111", fg="cyan",
                 font=("Arial", 10, "bold")).pack(padx=8, pady=(6,0))
        self.clock_var = tk.StringVar(value="--:--:--")
        tk.Label(side, textvariable=self.clock_var, bg="#111111", fg="white",
                 font=self.time_font).pack(padx=8)
        self.countdown_var = tk.StringVar(value="60s to close")
        tk.Label(side, textvariable=self.countdown_var, bg="#111111", fg="#cfcfcf",
                 font=("Consolas", 11)).pack(padx=8, pady=(0,8))

        self.ref_time_var = tk.StringVar(value="Ref: --:--")
        tk.Label(side, textvariable=self.ref_time_var, bg="#111111", fg="#a0a0a0",
                 font=("Consolas", 10)).pack(padx=8, pady=(0,6))

        tk.Label(side, text="LIVE", bg="#111111", fg="yellow",
                 font=("Arial", 10, "bold")).pack(padx=8, pady=(0,0))
        self.live_var = tk.StringVar(value="—")
        tk.Label(side, textvariable=self.live_var, bg="#111111", fg="white",
                 font=self.live_font).pack(padx=8, pady=(0,8))

        # One-line inputs summary (from tradebot via set_inputs)
        self.inputs_var = tk.StringVar(value="")
        tk.Label(root, textvariable=self.inputs_var, anchor="w",
                 fg="#cfcfcf", bg="#141414", font=("Consolas", 10)).pack(fill="x")

        # loops
        self._schedule_redraw()
        self._schedule_clock()

    # ---------- Public API ----------
    def set_inputs(self, kv: dict):
        self.inputs_var.set(" | ".join(f"{k}: {v}" for k, v in kv.items()))

    def set_anchor_time(self, dt_utc: datetime):
        self.anchor_utc = dt_utc
        local = (dt_utc + timedelta(hours=self.cfg.tz_offset)).strftime("%H:%M")
        self.ref_time_var.set(f"Ref: {local}")

    def set_ref_levels(self, high: Decimal, low: Decimal):
        self.ref_high, self.ref_low = high, low

    def set_live_price(self, price: Decimal):
        self.live_price = price
        try:
            self.live_var.set(f"{price:.4f}")
        except Exception:
            self.live_var.set(str(price))

    def set_position_levels(self, entry, stop, target, side: str = ""):
        self.pos_entry  = entry
        self.pos_stop   = stop
        self.pos_target = target
        self.pos_side   = side

    def clear_position_levels(self):
        self.pos_entry = self.pos_stop = self.pos_target = self.pos_side = None



    # Optional: change axes refs at runtime (kept minimal)
    def set_axes_refs(self, *, price_step: Optional[Decimal] = None,
                      grid_steps: Optional[int] = None,
                      x_label_every_min: Optional[int] = None):
        if price_step is not None:
            self.price_step = price_step
        if grid_steps is not None:
            self.grid_steps = int(grid_steps)
        if x_label_every_min is not None:
            self.x_label_every_min = int(x_label_every_min)

    def log(self, text: str):
        try:
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
        except tk.TclError:
            pass

    # ---------- Loops ----------
    def _schedule_redraw(self):
        try:
            self.draw()
        except Exception as e:
            self.log(f"draw error: {e}")
        finally:
            self.root.after(self.redraw_ms, self._schedule_redraw)

    def _schedule_clock(self):
        try:
            now_utc = datetime.now(timezone.utc)
            local_now = now_utc + timedelta(hours=self.cfg.tz_offset)
            self.clock_var.set(local_now.strftime("%H:%M:%S"))
            self.countdown_var.set(f"{(60 - now_utc.second) % 60:02d}s to close")
        except Exception as e:
            self.log(f"clock error: {e}")
        finally:
            self.root.after(self.clock_ms, self._schedule_clock)

    # ---------- Data snapshot ----------
    # ---------- Data snapshot ----------
    # ---------- Data snapshot (race-safe) ----------
    def _snapshot_1m(self):
        s = self.store.series["1m"]

        # Bulk-copy first; lists may differ in length for a moment if feed appends mid-copy.
        times  = list(s.times)
        opens  = list(s.opens)
        highs  = list(s.highs)
        lows   = list(s.lows)
        closes = list(s.closes)

        # Normalize to the shortest length so all arrays align.
        n = min(len(times), len(opens), len(highs), len(lows), len(closes))
        if n <= 0:
            return [], [], [], [], []

        times  = times[-n:]
        opens  = [float(x) for x in opens[-n:]]
        highs  = [float(x) for x in highs[-n:]]
        lows   = [float(x) for x in lows[-n:]]
        closes = [float(x) for x in closes[-n:]]

        # Optional anchor window: compute start index from times only, then slice ALL arrays the same.
        start_idx = 0
        if self.anchor_utc is not None:
            cutoff = self.anchor_utc - timedelta(minutes=9)
            for i, ts in enumerate(times):
                if ts >= cutoff:
                    start_idx = i
                    break

        return (
            times[start_idx:],
            opens[start_idx:],
            highs[start_idx:],
            lows[start_idx:],
            closes[start_idx:],
        )

    # ---------- Draw ----------
    # ---------- Draw ----------
    def draw(self):
        self.canvas.delete("all")
        times, opens, highs, lows, closes = self._snapshot_1m()
        if not times:
            return

        ml, mr, mt, mb = (self.margins[k] for k in ("left", "right", "top", "bottom"))
        plot_left, plot_right = ml, self.width - mr
        plot_top, plot_bottom = mt, self.height - mb
        plot_w, plot_h = (plot_right - plot_left), (plot_bottom - plot_top)

        n = len(times)
        # ✅ use n (not n-1) so the rightmost slot is reserved for LIVE
        x_step = plot_w / max(n, 1)

        # price window (auto) using editable price step & grid steps
        unit = float(self.price_step)
        last_close = closes[-1]
        grid_prices = [last_close + i * unit for i in range(-self.grid_steps, self.grid_steps + 1)]
        pmin = min(min(lows), grid_prices[0])
        pmax = max(max(highs), grid_prices[-1])
        if pmax - pmin < unit * 6:
            mid = (pmax + pmin) / 2
            pmin = mid - unit * 6
            pmax = mid + unit * 6

        def y_for(price: float) -> float:
            span = max(pmax - pmin, 1e-9)
            return plot_top + plot_h * (1 - (price - pmin) / span)

        # grid + right labels
        for gp in grid_prices:
            if pmin <= gp <= pmax:
                y = y_for(gp)
                self.canvas.create_line(plot_left, y, plot_right, y, fill="#2f2f2f")
                self.canvas.create_text(
                    plot_right + PRICE_LABEL_PAD, y, text=f"{gp:.4f}",
                    anchor="w", fill="#d0d0d0", font=self.axis_font
                )

        # baseline
        self.canvas.create_line(plot_left, plot_bottom, plot_right, plot_bottom, fill="#2f2f2f")

        # dots + arrows (green OPEN, red CLOSE; arrow shows low↔high with direction)
        hist_n = n  # closed bars only; live stays in its own slot
        for i in range(hist_n):
            x = plot_left + i * x_step

            # arrow first (so dots sit on top)
            if SHOW_CANDLE_ARROWS:
                is_bull = closes[i] > opens[i]
                is_bear = closes[i] < opens[i]
                if is_bull:
                    y0, y1 = y_for(lows[i]), y_for(highs[i])   # low -> high
                    self.canvas.create_line(
                        x, y0, x, y1,
                        fill=ARROW_UP_COLOR, width=ARROW_WIDTH, arrow="last", arrowshape=ARROW_SHAPE
                    )
                elif is_bear:
                    y0, y1 = y_for(highs[i]), y_for(lows[i])   # high -> low
                    self.canvas.create_line(
                        x, y0, x, y1,
                        fill=ARROW_DOWN_COLOR, width=ARROW_WIDTH, arrow="last", arrowshape=ARROW_SHAPE
                    )
                # doji: no arrow

            # open/close dots on top
            yo, yc = y_for(opens[i]), y_for(closes[i])
            rO, rC = self.high_dot_r, self.low_dot_r
            self.canvas.create_oval(x - rO, yo - rO, x + rO, yo + rO, fill="#41ff77", outline="")
            self.canvas.create_oval(x - rC, yc - rC, x + rC, yc + rC, fill="#ff4d4d", outline="")

        # x-labels every N minutes (local)
        last_label_x = -1e9
        for i in range(n):
            tloc = times[i] + timedelta(hours=self.cfg.tz_offset)
            if tloc.minute % self.x_label_every_min == 0:
                x = plot_left + i * x_step
                if x - last_label_x > 40:
                    self.canvas.create_text(
                        x, plot_bottom + 12, text=tloc.strftime("%H:%M"),
                        fill="#cfcfcf", font=("Consolas", 9)
                    )
                    last_label_x = x

        # blue lines
        if self.ref_high is not None:
            ph = float(self.ref_high)
            if pmin <= ph <= pmax:
                self.canvas.create_line(plot_left, y_for(ph), plot_right, y_for(ph), fill="#4aa3ff", width=2)
        if self.ref_low is not None:
            pl = float(self.ref_low)
            if pmin <= pl <= pmax:
                self.canvas.create_line(plot_left, y_for(pl), plot_right, y_for(pl), fill="#4aa3ff", width=2)

        # ✅ yellow live dot in its OWN slot at index n (never overlaps closed dots)
        if self.live_price is not None:
            py = float(self.live_price)
            if pmin <= py <= pmax:
                x_live = plot_left + n * x_step
                x_live = min(max(plot_left + 2, x_live), plot_right - 2)
                r = self.live_dot_r
                self.canvas.create_oval(x_live - r, y_for(py) - r, x_live + r, y_for(py) + r,
                                        fill="yellow", outline="yellow")

        # violet position lines unchanged...

        # position lines (violet) — draw only when a trade is active
        if self.pos_entry is not None and self.pos_stop is not None and self.pos_target is not None:
            pe = float(self.pos_entry); ps = float(self.pos_stop); pt = float(self.pos_target)
            for val, label in ((pe, "ENTRY"), (ps, "STOP"), (pt, "TARGET")):
                if pmin <= val <= pmax:
                    y = y_for(val)
                    self.canvas.create_line(plot_left, y, plot_right, y, fill=POS_COLOR, width=POS_WIDTH)
                    self.canvas.create_text(
                        plot_left - 8, y, text=label, anchor="e", fill=POS_COLOR, font=("Consolas", 9, "bold")
                    )
