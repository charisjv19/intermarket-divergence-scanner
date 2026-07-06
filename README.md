# intermarket-divergence-scanner

# ES/NQ Intermarket Divergence Scanner

A Python-based signal scanner that detects intermarket divergence between ES and NQ futures — identifying liquidity sweeps where one instrument fails to confirm the other's swing extreme, with Fair Value Gap entry confluence and session open bias filtering.

NOTE: Go to Branch 8.1 to see recent version updates to the scanner. The main branch is for whole version updates.

---

## What It Does

The scanner ingests 1-minute bar data for both the MES (ES) and MNQ (NQ) micro futures contracts and systematically identifies moments where the two instruments diverge structurally at swing highs or lows. These divergences indicate that one market swept a prior liquidity level while the other failed to confirm — a signal of potential reversal.

To qualify as a valid signal, the setup must meet all of the following:

- **Intermarket divergence** — ES and NQ form mismatched swing highs or lows (one makes a lower low while the other holds or makes a higher low, or vice versa for shorts)
- **Structural pullback** — a minimum price retracement exists between the two swings, confirming the move was meaningful
- **Swing timestamp alignment** — the two instruments' swings must occur within the same bar to be considered a true divergence
- **Session open bias** — the signal direction must align with the macro bias established by the session open relative to the prior session's range
- **Fair Value Gap confluence** — a price imbalance (gap between the high of one candle and the low of the next) must be present either after the divergence or pre-existing at the signal location

---

## Entry Types

Two entry models are detected:

| Type | Description |
|---|---|
| `FVG_AFTER_SMT` | A Fair Value Gap forms in the bars following the divergence. Entry is placed at the 50% level of the gap on a limit order. |
| `SMT_IN_FVG` | The divergence occurs while price is already inside a pre-existing Fair Value Gap. Entry is at market into the gap. |

---

## Session Coverage

| Session | Window (ET) |
|---|---|
| NY Morning | 8:00 AM – 10:30 AM |
| NY Afternoon | 1:00 PM – 3:00 PM |

Signals outside these windows are excluded. A 30-minute pre-session lookback is used for swing tracking context — swings that form just before a session opens are visible to the scanner but cannot themselves generate signals.

---

## Signal Output

Each detected signal is exported to CSV with the following fields:

- Signal timestamp, date, session
- Direction (LONG / SHORT) and lead instrument (ES or NQ)
- Both swing timestamps and prices for each instrument
- FVG boundaries (low, high, 50% entry level, size in points)
- Entry type classification
- SMT variant — `standard` (clear divergence) or `equal` (one instrument holds flat)
- ES and NQ close price at signal time

Signals are deduplicated by 30-minute bucket to prevent the same setup from being logged multiple times.

---

## Parameters

| Parameter | ES | NQ |
|---|---|---|
| Min structural pullback | 2.0 pts | 8.0 pts |
| FVG size range | 1.0 – 25.0 pts | 3.0 – 150.0 pts |
| FVG lookahead (bars) | 15 | 15 |
| Pre-existing FVG lookback (bars) | 30 | 30 |

---

## Version History

The scanner was built iteratively — each version reflects a specific refinement to detection logic, filtering, or entry classification based on manual review of signal output against chart data.

| Version | Key Changes |
|---|---|
| v2 | Initial build — basic divergence detection with swing timestamps and FVG lookahead |
| v3 | Improved swing matching and session window logic |
| v4 | Structural pullback validation added between swing pairs |
| v5 | FVG location anchor changed from bar close to swept extreme; equal high/low variant added |
| v6 | Afternoon session extended to 13:00 ET; pre-session swing lookback added (30 min before each session) to capture valid swings forming just before the open |
| v7 | Individual structural swing thresholds removed — were incorrectly filtering valid divergences confirmed by chart review (including two missed 3R trades); structural qualification consolidated entirely into the pair-level pullback filter |
| v8 | Two-layer macro filter added (session open bias); eliminated ~50% of noise signals that were firing against the macro direction |
| v8.1 | Session Open Bias filter refined to single layer; `SMT_IN_FVG` pre-existing gap entry type added |

---

## Requirements

```
pandas
numpy
```

Input data is 1-minute OHLC bar CSV files with pre-calculated swing high/low and EMA columns, exported from TradingView or equivalent.

---

## Usage

```python
from smt_scanner_v8_1 import run

results, ts_filtered, macro_filtered = run(
    es_path='path/to/MES_1min.csv',
    nq_path='path/to/MNQ_1min.csv'
)

results.to_csv('signals_output.csv', index=False)
```

