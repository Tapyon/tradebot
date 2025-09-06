<img width="1631" height="1351" alt="image" src="https://github.com/user-attachments/assets/9d68a0e4-af35-4dae-9401-126ee097f56e" />


# Tradebot – Mini 1- Minute Chart

A tiny desktop app that shows a **1-minute mini-chart** (dots + arrows), a **live yellow price dot**, and optional **pattern/strategy logs**.  
Data sources: **closed candles** from Kraken REST (official OHLC) and **live ticks** from Kraken WebSocket (fast).

---

## What’s in this repo
- **tradebot.py** – The main app: wires data, UI, WebSocket live price, patterns, and strategy.
- **data.py** – Kraken REST client (OHLC), CSV recorder, and a small **KrakenWS** WebSocket client.
- **core.py** – Core types (Config, Candle) and the in-memory candle store.
- **ui_chart.py** – The mini chart UI (Tkinter): dots, arrows, blue ref lines, yellow live dot.
- **strategies.py** – Example **Breakout 2×1** strategy + trade journal (CSV).
- **patterns.py** – Small pattern library (gap, range breakout, doji, engulfing, MA crossover).
- **pattern_overlay.py** – Extra overlay drawing helpers (optional).

---

## Requirements
- Windows/macOS/Linux
- **Python 3.10+** (Tkinter is included with the standard Python installer)
- Python packages: `requests`, `websocket-client`,

---

## Install & run (Windows, PowerShell)
```powershell

# 1) Open PowerShell in the project folder
#    C:\Users\franc\tradebot\tradepython\new

# 2) Create & activate venv
python -m venv .venv
.\.venv\Scripts\Activate

# 3) Install deps
python -m pip install --upgrade pip
# if you have requirements.txt:
python -m pip install -r requirements.txt
# otherwise:
python -m pip install requests websocket-client

# 4) Run
python tradebot.py

```

Close with the window **X** or **Ctrl+C** (clean shutdown).

---
---
STRATEGY EXPLANATION ! 
-
As now, tradebot activate the strategies.py , and works for the FIRST 5 MIN CANDLE THEORY ...
this triggers :
- Set blue limits lines on the HIGHEST and LOWEST values from the 5 last 1min candles ( the REF_LOCAL_HOUR and the REF_LOCAL_MINUTE marked the last 1min candle ).
- Set the process to wait for a candle that OPEN or CLOSED over the Blue Limits , then trigger the LONG or SHORT position with a 2x1 ratio and mark that with VIOLET LIMIT LINES. 
- Wait for the CURRENT PRICE - LIVE PRICE - YELLOW DOT touches the VIOLET LIMITS , and trigger TAKE PROFIT or TAKE LOSS
- Endless loop for now need customization
---
---

## Reference – edit these knob

### `tradebot.py`
**Inputs**
- `TIMEZONE_OFFSET = -6` — Local vs UTC offset used for on-screen times and the reference minute.
- `REF_LOCAL_HOUR = 7` — Local hour of the **anchor** minute (for blue lines).
- `REF_LOCAL_MINUTE = 35` — Local minute of the anchor.
- `RESET_STORAGE_ON_START = True` — If `True`, clears the candle CSV on startup.
- `STORAGE_FILE = "candles_1m.csv"` — Where closed 1m candles are saved.
- `UNIT_STR = "0.0025"` — Price step used for grids/risk math (UI uses this if no override).

**Verify (data sanity)**
- `VERIFY_ENABLED = True` — Turn the “last-5 bars” check/patch on or off.
- `VERIFY_EVERY_SEC = 20` — How often the background verify runs.
- `VERIFY_ON_CLOSE_DELAY_SEC = 5` — Delay after a bar closes before verifying.

**Patterns (optional logs)**
- `ENABLE_PATTERNS = True` — Enable pattern detection.
- `PATTERNS_TO_CHECK = ["gap", "range_breakout", "doji", "engulfing", "ma_crossover"]` — Which patterns to scan.
- `PATTERN_LOG_MODE = "all"` — `"all"` logs every detection; `"gap_only"` logs only gaps.

### `ui_chart.py`
- `PRICE_STEP_OVERRIDE: Optional[Decimal] = 0.001` — Grid step for price labels; set to `None` to use `UNIT_STR` from `tradebot.py`.
- `GRID_STEPS = 7` — How many steps **above/below** the last close are drawn (controls vertical range).

---

## Notes
- **Live price** uses **Kraken WebSocket** (fast). Old REST live polling is **disabled**.
- **Closed 1m candles** still come from **Kraken REST OHLC** (exchange-official history).
- The app periodically **verifies** recent candles and writes corrections if needed.
