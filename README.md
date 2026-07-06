

# ES/NQ Intermarket Divergence Scanner

A Python-based signal detection system for intraday futures trading on MES/MNQ micro contracts. The scanner identifies structural divergence (SMT — Smart Money Technique) between ES and NQ at swing highs and lows, then finds Fair Value Gap entries aligned with multi-timeframe macro bias.

This is an active research project. The scanner is built iteratively — each version reflects specific refinements to detection logic based on manual blind review of signal output against live chart data.

---

## Project Structure

```
├── scanners/                      # Scanner versions (Python)
│   ├── smt_scanner_v8_2.py        # FVG selection + staleness improvements
│   ├── smt_scanner_v8_3.py        # 15m macro bias engine
│   ├── smt_scanner_v8_4.py        # 5m SMT confluence layer
│   ├── smt_scanner_v8_5.py        # Walk-back wick-break algorithm
│   ├── smt_scanner_v8_6.py        # Multi-sw2 + parallel sw1 detection
│   └── smt_scanner_v8_7.py        # Dedup fix + FVG search correction (current)
├── tools/
│   ├── macro_bias_diagnostic.py   # 15m structural bias classifier
│   └── macro_bias_background.pine # TradingView Pine Script v6 indicator
├── journal/
│   └── trading-journal-v6.html    # Custom HTML trading journal + signal review tool
├── docs/
│   └── SMT_Macro_Bias_Framework.pdf  # Formalized 3-timeframe bias methodology
├── smt_scanner_v2 – v8            # Earlier scanner versions (legacy)
├── CHANGELOG.md
└── README.md
```

---

## What It Does

The scanner reads 1-minute OHLC bar data (with pre-calculated swing markers from TradingView) for both MES and MNQ, then detects moments where the two instruments diverge structurally — one breaks a prior swing level while the other fails to confirm. This divergence suggests the confirming instrument's move was a false break (liquidity sweep), creating a potential reversal entry.

### Signal Qualification (v8.7)

A valid signal requires all of the following:

- **Intermarket divergence** — One instrument's sw2 wick-breaks a prior structural swing (sw1) while the other instrument's parallel sw2 fails to break its parallel sw1
- **Parallel timestamp alignment** — sw1 and sw2 on both instruments must occur at matching timestamps (sw2 within the same minute; sw1 within 2-minute tolerance)
- **Same-day enforcement** — sw1 must be from the same trading day as the signal
- **sw2 staleness** — sw2 must be within the last 120 minutes (1m chart) or 36 bars (5m chart)
- **15m macro bias alignment** — signal direction must agree with the combined ES+NQ structural bias on the 15-minute timeframe
- **Fair Value Gap confluence** — a price imbalance must exist either after the divergence (FVG_AFTER_SMT) or pre-existing at the signal location (SMT_IN_FVG)

### Entry Types

| Type | Description |
|------|-------------|
| `FVG_AFTER_SMT` | FVG forms after the divergence. Limit entry at the 50% level of the gap. |
| `SMT_IN_FVG` | Divergence occurs inside a pre-existing FVG. Market entry into the gap. |

### Multi-Timeframe Integration

The scanner operates across three timeframes:

| Timeframe | Role |
|-----------|------|
| **15m** | Macro bias direction (structural swing sequence + momentum classification) |
| **5m** | SMT confluence layer (confirms or opposes the 1m signal) |
| **1m** | Primary signal detection + FVG entry identification |

---

## How It Works

### v8.2 — FVG Selection + Staleness

v8.2 addressed two problems identified during manual review: the scanner was sometimes picking a later FVG when an earlier valid one existed, and sw1 could be arbitrarily far from sw2 in time, producing structurally meaningless comparisons.

- **Earliest valid FVG**: Instead of returning the first FVG found in the lookahead scan, the scanner now evaluates all candidates and selects the one closest to the SMT confirmation point
- **sw1 staleness limit**: sw1 must be within 60 minutes of sw2 on the 1m chart — prevents comparing structural points across sessions or extended ranging periods
- **SMT_IN_FVG dedup priority**: When both entry types exist for the same divergence, SMT_IN_FVG (market entry) takes precedence over FVG_AFTER_SMT (limit entry)

### v8.3 — 15m Macro Bias Engine

v8.3 replaced the session open midpoint filter (a blunt directional check) with a structural bias classifier operating on 15-minute data. This was the first multi-timeframe integration.

- **Swing sequence analysis**: Resamples 1m data to 15m, runs 4-bar swing detection, classifies higher highs/higher lows as bullish, lower highs/lower lows as bearish
- **Momentum classification**: Compares consecutive impulse leg sizes. If the current leg is ≥80% of the previous, momentum is "strong"; below 80% is "slowing". Recovery above 80% on the next leg resets to strong
- **Per-signal evaluation**: Bias evolves through the session as new 15m bars close, so a morning signal can have a different bias than an afternoon signal
- **Combined bias**: Requires both ES and NQ to agree directionally. One bullish + one bearish = conflicted (signal blocked)
- **States**: BULLISH, BULLISH (SLOWING), BEARISH, BEARISH (SLOWING), TRANSITIONAL, CONFLICTED

### v8.4 — 5m SMT Confluence Layer

v8.4 added a second timeframe confirmation: does a 5m structural divergence exist in the same direction as the 1m signal?

- **5m swing detection**: Runs the same 4-bar swing logic on 5m resampled data within a 36-bar (3-hour) lookback window
- **Confluence check**: Each 1m signal is evaluated against the 5m SMT state at the time of the signal
- **Two modes tested**: Strict mode (require agreeing 5m SMT) reduced signals to 6 total — too aggressive. Lenient mode (only block on opposing 5m SMT) preserved signal volume while filtering contradictory setups
- **Output**: `smt5m_status` column — agree, oppose, conflicted, or none

### v8.5 — Walk-Back Wick-Break Algorithm (Complete SMT Rewrite)

v8.5 replaced the "compare last 2 swings" approach with a fundamentally different algorithm that matches how discretionary traders actually read SMT on a chart.

**Core change**: Instead of asking "did the last two swings diverge?", v8.5 asks "did any recent swing break prior structure while the other instrument failed to break its corresponding structure?"

The walk-back algorithm:

1. **Consider the last 5 confirmed swings** on each instrument as potential sw2 anchors (newest first)
2. **For each sw2 candidate**, find the parallel sw2 on the other instrument at the same timestamp
3. **Walk back through up to 20 prior same-day swings** to find all structural points that sw2 wick-broke
4. **For each broken sw1**, find the parallel swing on the other instrument at the same timestamp (±2 min tolerance)
5. **Check if the other instrument's sw2 also broke its parallel sw1** — if NOT, this is a confirmed divergence
6. **Fire the signal** if at least one structural reference has a "failed parallel"

This approach catches divergences that occur several swings back from the current price action — matching how discretionary traders actually read SMT on a chart.

Additional v8.5 changes:
- **Wick break** replaces body break — `sw2_high > sw1_high` (not close > high)
- **sw2 staleness**: 120 minutes on 1m chart, 36 bars on 5m chart
- **Same-day session boundary**: sw1 must be from the same calendar date as the signal

### v8.6 — Multi-sw2 Candidates + Parallel sw1

v8.6 addressed two limitations of v8.5: it only considered the single most recent swing as sw2, and sw1 on the failing instrument wasn't time-aligned with sw1 on the confirming instrument.

- **Multi-sw2 candidates**: Last 5 confirmed swings considered as potential sw2 anchors (newest first). This catches structural breaks that happened a few swings back, not just the absolute latest
- **Parallel sw1 lookup**: sw1 on the failing instrument is now found by matching timestamps with sw1 on the confirming instrument (±2 minute tolerance). Previously used "most recent prior swing" which didn't match the parallel structural comparison a trader actually makes
- **All broken sw1s evaluated**: When sw2 breaks multiple prior structures, the scanner checks all of them. Signal fires if at least one has a "failed parallel"
- **Primary sw1 = oldest failed parallel**: Reports the most structurally significant divergence
- **`confirmations_count`**: Tracks how many structural breaks confirm the divergence. Values of 3+ indicate particularly strong setups
- **`alt_sw1_times`**: Lists additional confirmed sw1 timestamps when confirmations_count > 1
- **Performance caching**: Macro bias cached per 15-min bucket, 5m SMT cached per 5-min bucket — reduced processing from ~60s to ~19s per file

### v8.7 — Dedup + FVG Search Correction

v8.7 fixed two bugs discovered during manual review of Feb 25, 2026 signals:

- **Dedup by sw2 identity**: Previously deduped by 30-minute time bucket, which allowed the same SMT (identical sw1/sw2 pair) to generate multiple signals across bucket boundaries. Now deduplicates by `sw2_conf_time` — each unique SMT fires exactly once
- **FVG search starts from sw2 confirmation bar**: Previously searched from the signal bar (delayed by caching/bucketing), missing FVGs that formed between sw2 confirmation and signal firing. Now starts immediately after sw2
- **ES_FVG_MIN lowered to 0.5pt**: A 0.5pt FVG on Feb 25 would have been a 3R winner. Under review for potential overfit; may revert for out-of-sample testing

### Confirmations Count

When sw2 breaks multiple prior structural references that the other instrument fails to break, the `confirmations_count` field tracks how many independent structural breaks confirm the divergence. Higher counts indicate stronger setups.

### 15m Macro Bias Engine

The macro bias classifier resamples 1m data to 15m, runs 4-bar swing detection, and classifies each instrument's structural state:

- **Sequence**: HH+HL = bullish, LH+LL = bearish, mixed = transitional
- **Momentum**: Compares impulse leg sizes. ≥80% of previous leg = strong, <80% = slowing
- **Combined**: Both ES and NQ must agree directionally. Disagreement = conflicted.

States: `BULLISH`, `BULLISH (SLOWING)`, `BEARISH`, `BEARISH (SLOWING)`, `TRANSITIONAL`, `CONFLICTED`

---

## Version History

The scanner was built iteratively. Each version reflects a specific refinement based on manual review of signal output against chart data. See [CHANGELOG.md](CHANGELOG.md) for detailed descriptions.

| Version | Key Changes |
|---------|-------------|
| v2 | Initial build — basic divergence detection with swing timestamps and FVG lookahead |
| v3 | Improved swing matching and session window logic |
| v4 | Structural pullback validation between swing pairs |
| v5 | FVG anchor changed from bar close to swept extreme; equal high/low variant added |
| v6 | Afternoon session extended; pre-session swing lookback added |
| v7 | Individual swing thresholds removed after chart review showed valid divergences being filtered |
| v8 | Two-layer macro filter (session open bias); eliminated ~50% of noise signals |
| v8.1 | Session open bias refined; SMT_IN_FVG entry type added |
| **v8.2** | **FVG selection picks earliest valid; sw1 staleness limit (60 min)** |
| **v8.3** | **15m macro bias engine replaces session open midpoint filter** |
| **v8.4** | **5m SMT confluence layer (lenient mode: blocks only on opposing 5m SMT)** |
| **v8.5** | **Complete SMT rewrite: walk-back wick-break algorithm with 20-swing lookback** |
| **v8.6** | **Multi-sw2 candidates (last 5 swings); parallel sw1 timestamp matching; confirmations_count** |
| **v8.7** | **Dedup by sw2 identity (not 30-min bucket); FVG search from sw2 confirmation bar; ES_FVG_MIN lowered to 0.5pt** |

---

## Research Methodology

This project uses an iterative human-in-the-loop validation approach rather than pure backtesting:

1. **Export** 1-minute bar data with swing markers from TradingView
2. **Run** the scanner to generate signal candidates
3. **Blind review** each signal against the chart — mark whether the SMT is valid, the FVG is real, and whether the trade would be taken
4. **Identify bugs and logic gaps** from the review (e.g., FVG on wrong side of structure, sw1 from a different session, duplicate signals)
5. **Fix the specific issue**, version the scanner, and repeat

This process has proven more effective than backtesting alone for catching specification errors — cases where the algorithm technically "works" but is measuring something different from what the trader intends. Manual blind review catches these errors at the conceptual level, not just the statistical level.

### Key Findings

- **December and January are historically poor months** for this strategy (confirmed across three years of observation)
- **Macro alignment is a strong performance differentiator**: macro-aligned trades significantly outperform against-macro trades
- **SMT misidentification is the primary loss driver**: measuring divergence on bodies instead of wicks, using non-structural points, or comparing non-parallel timestamps were the main sources of false signals
- **Structural pullback filter was critical**: without minimum retracement between consecutive swings (ES ≥4pt, NQ ≥15pt), false signal rates were unacceptably high

---

## Tools

### Trading Journal (v6)

A self-contained HTML trading journal with:
- Signal CSV import with auto-populated fields (macro bias, 5m SMT, confirmations count, entry type)
- Signal review workflow with status tracking (pending → reviewed → logged)
- Trade history table with color-coded columns
- Equity curve visualization
- CSV export
- Light/dark theme

### Macro Bias Diagnostic

Standalone Python script that classifies 15m structural bias per session. Reads 1m CSVs, resamples to 15m, runs swing detection, and outputs session-by-session bias classification to CSV.

### Pine Script Indicator

TradingView Pine Script v6 indicator that displays 15m macro bias as colored chart backgrounds. Deep green = strong bullish, light green = bullish slowing, deep red = strong bearish, light red = bearish slowing, yellow = transitional.

---

## Session Coverage

| Session | Window (ET) |
|---------|------------|
| NY Morning | 8:00 AM – 10:30 AM |
| NY Afternoon | 1:00 PM – 3:00 PM |

30-minute pre-session lookback for swing context.

---

## Requirements

```
pandas
numpy
```

Input: 1-minute OHLC bar CSV files with pre-calculated `Swing High`, `Swing Low`, and `EMA` columns, exported from TradingView using the Swing High/Low Marker indicator.

---

## Usage

```python
from scanners.smt_scanner_v8_7 import run

results, ts_filtered, macro_filtered, stale_filtered, smt5m_filtered = run(
    es_path='path/to/MES_1min.csv',
    nq_path='path/to/MNQ_1min.csv'
)

results.to_csv('signals_output.csv', index=False)
```

---

## Status

Active development. Currently validating v8.7 signal output through manual blind review before out-of-sample testing on fresh data (May–June 2026).
