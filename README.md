

# ES/NQ Intermarket Divergence Scanner

A Python-based signal detection system for intraday futures trading on MES/MNQ micro contracts. The scanner identifies structural divergence (SMT — Smart Money Technique) between ES and NQ at swing highs and lows, then finds Fair Value Gap entries aligned with multi-timeframe macro bias.

This is an active research project. The scanner is built iteratively — each version reflects specific refinements to detection logic based on manual blind review of signal output against live chart data.

---

## Project Structure

```
├── live_scanners/                 # LIVE — what is running right now
│   ├── smt_scanner_v8_7.py        #   live copy of v8.7 (frozen)
│   ├── smt_scanner_v8_8.py        #   live copy of tagged OOS v8.8
│   ├── smt_scanner_v8_10.py       #   live copy of v8.10 (dual-confirm + 90-min sw1 staleness)
│   ├── runner.py, feed.py, relay.py, v87.py, v88.py, v810.py   # poll TopstepX, score, post to Slack
│   ├── START_HERE.txt             #   step-by-step VS Code setup
│   ├── README.md
│   ├── LIVE_SCANNER_NOTES.md      #   chart-vs-Slack observation log (observation only)
│   └── tests/                     #   live tests, incl. live-copy == root-original check
├── backtesting_toolkit/           # RESEARCH — backtest and review tools
│   ├── projectx_historical_pull.py  # TopstepX 1-min history pull (6-hour paging)
│   ├── data.py                    #   cached pulls + TradingView CSV fallback
│   ├── simulate_fills.py          #   1-min fill simulator
│   ├── structural_tests.py        #   checks that must pass before any backtest
│   ├── pnl_report.py              #   Wilson CI, bootstrap expectancy, profit factor
│   ├── compare_versions.py        #   review_these.csv of differences between versions
│   ├── reconstruct_v87.py, v87_v88_compare.py
│   ├── discretion_review/         #   outcome-blind hand-review pack
│   ├── entry_variants.md          #   entry-price variant results (live-achievable Step 5)
│   ├── README.md
│   └── tests/
├── scanner_versions/              # Every scanner version — the originals
│   ├── smt_scanner_v2.py … smt_scanner_v8_1.py  #   legacy versions (v2–v8.1)
│   ├── smt_scanner_v8_2.py … smt_scanner_v8_7.py
│   ├── smt_scanner_v8_8.py        #   tagged OOS v8.8
│   ├── smt_scanner_v8_9.py        #   NOT run live — its change is omitted going forward (see CHANGE_LOG)
│   └── smt_scanner_v8_10.py
├── tests/                         # v8.8 / v8.9 scanner correctness tests
├── tools/
│   ├── swing_marker_detection.py / .pine        # 4-bar swing markers (Python + Pine)
│   └── macro_bias_diagnostic.py, macro_bias_background.pine   # 15m bias tools
├── SMT_Macro_Bias_Framework.pdf
├── requirements.txt, .env.example, .vscode/, .cursor/rules/   # setup files (used by both folders)
├── CHANGE_LOG
└── README.md
```

**Live copies.** The scanners in `live_scanners/` are exact copies of the
originals in `scanner_versions/`, so the live folder shows what is running. To
change a live version, change the original in `scanner_versions/` and copy it into
`live_scanners/` — `live_scanners/tests/test_live_copies.py` fails if they
differ.

**Setup files stay at the top level.** VS Code only reads `.vscode/`, Cursor
only reads `.cursor/rules/`, and the runner loads `.env` from the repo root.

Run all tests from the repo root:

```bash
python -m unittest discover -p 'test_*.py'
```

---

## What It Does

The scanner reads 1-minute OHLC bar data (with pre-calculated swing markers from TradingView) for both MES and MNQ, then detects moments where the two instruments diverge structurally — one breaks a prior swing level while the other fails to confirm. This divergence suggests the confirming instrument's move was a false break (liquidity sweep), creating a potential reversal entry.

### Signal Qualification (current: v8.8 and v8.10)

A valid signal requires all of the following. Where the live versions differ, it says so.

- **Intermarket divergence** — one instrument's sw2 wick-breaks a prior structural swing (sw1) while the other instrument's parallel sw2 fails to break its parallel sw1.
- **Exact parallel timestamps** — sw2 and sw1 must sit on the same 1-minute bar on both instruments (v8.8 audit fix S3; v8.7 still allows sw1 within ±2 minutes).
- **Same-session sw1** — the sw1 walk-back stays inside the signal's own session, not just the same calendar day (v8.8 audit fix S1).
- **sw2 staleness** — sw2 must be within the last 120 minutes (1m chart) or 36 bars (5m chart).
- **sw1 staleness (v8.10 only)** — the confirming instrument's sw1 may be at most 90 minutes older than its sw2. v8.5–v8.9 have no sw1 age limit: the v8.2 60-minute check stopped being applied in the v8.5 rewrite.
- **Both confirm sides scored (v8.10 only)** — ES-confirm and NQ-confirm divergences are evaluated independently. v8.8 and earlier skip NQ-confirm whenever an ES-confirm SMT exists.
- **15m macro bias alignment** — signal direction must agree with the combined ES+NQ structural bias on the 15-minute timeframe, read at the signal's confirmation time (v8.8+).
- **5m SMT confluence (lenient)** — the signal is blocked only by an opposing 5m SMT, also read at the confirmation time.
- **Fair Value Gap confluence** — either an FVG forms after the divergence (`FVG_AFTER_SMT`), or the sw2 confirmation-bar close sits inside a pre-existing FVG (`SMT_IN_FVG`). FVGs whose three bars span a session gap are rejected.
- **Clean data** — ES and NQ are inner-joined by minute; a minute missing on either side is dropped from both, never filled.

Each unique SMT fires once: signals are deduplicated by `(date, sw2_conf_time, direction, instrument)`.

### Entry Types

Entry, stop and target are written to the output from v8.8 on (`entry_price`, `stop_loss`, `take_profit`, `target_R`). v8.7 never stored them; the backtesting toolkit rebuilds them from the documented formula.

| Type | Entry | Stop | Target |
|------|-------|------|--------|
| `FVG_AFTER_SMT` | Limit at the 50% level of the FVG that forms after the divergence | Beyond the swing before the FVG (ES 2.0 / NQ 8.0 pt) | 3R |
| `SMT_IN_FVG` | Market at the close of the bar right after sw2 (exactly sw2 + 1 min), which must be inside a pre-existing FVG | Just outside that FVG's edge (ES 1.0 / NQ 3.0 pt) | 1.5R |

Minimum stop distance for both types: 20 ticks on ES (5.0 pt), 40 ticks on NQ (10.0 pt).

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
- **sw1 staleness limit**: sw1 must be within 60 minutes of sw2 on the 1m chart — prevents comparing structural points across sessions or extended ranging periods. (This check stopped being applied in the v8.5 rewrite; v8.10 brings back an sw1 limit at 90 minutes.)
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

### v8.8 — Audit Fixes + Confirmation-Clock Correctness (tagged OOS, live)

v8.8 implements the September 2026 strategy audit (S1, S2, S3) and a set of correctness fixes found while building the backtesting toolkit. No detection thresholds were retuned. The file at the `v8.8` tag is frozen as the out-of-sample baseline.

- **S1 — same-session sw1**: the walk-back for sw1 is limited to the signal's own session
- **S3 — exact parallel sw1**: sw1 parallel tolerance 2 min → 0; both instruments' sw1 must be on the same bar
- **S2 — entry/stop/target protocol**: market entry for `SMT_IN_FVG`, 50% limit for `FVG_AFTER_SMT`, stops and targets as in the Entry Types table, and a shared minimum stop
- **Signal time is the confirmation bar**: `smt_time`, the 15m bias and the 5m SMT check are all read at the bar that confirms the setup, not at whichever later bar the scan loop happened to be on
- **No session-gap artifacts**: an SMT_IN_FVG confirmation must be exactly 1 minute after sw2, and an FVG's three bars must be consecutive minutes — so a 14:29 bar can no longer pair with the next morning's 07:30 bar
- **FVG_AFTER_SMT stop anchored at the FVG bar**: the swing used for the stop is the most recent one before the FVG formed
- **ES/NQ inner-join**: a minute that exists on only one instrument is dropped from both before 5m/15m bars are built
- **Pre-session FVGs count for SMT_IN_FVG**: a gap formed in 07:30–08:00 or 12:30–13:00 is valid pre-existing structure

### v8.9 — Swing-Wick SMT_IN_FVG Membership (not run live)

v8.9 changed one rule: `SMT_IN_FVG` fires when the confirming instrument's sw1 and/or sw2 **wick** sits inside a pre-existing FVG, instead of the confirmation-bar **close**. Everything else matches v8.8.

**v8.9 is not run live, and this change is deliberately left out of all later versions.** v8.10 is built from the tagged v8.8 file, not from v8.9. `scanner_versions/smt_scanner_v8_9.py` is kept as a historical record only.

### v8.10 — Dual-Confirm + sw1 Staleness (live)

v8.10 is built on the tagged OOS v8.8 file and adds two rules, both prompted by live chart-vs-alert review:

- **Dual-confirm**: v8.8 skips an NQ-confirm SMT whenever an ES-confirm SMT exists, which dropped a valid NQ-confirm / ES-fail setup on 2026-10-05 (09:14 sw1 / 09:24 sw2). v8.10 scores both sides independently; ES LONG and NQ LONG at the same sw2 are two separate signals
- **90-minute sw1 staleness**: the confirming instrument's sw1 may be at most 90 minutes older than sw2 (`SW1_STALENESS_MINS = 90`). Prompted by the 2026-10-06 07:37 → 09:18 setup (101 minutes apart). Older failed parallels are dropped before the primary sw1 is chosen; if none remain, there is no SMT

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

The scanner was built iteratively. Each version reflects a specific refinement based on manual review of signal output against chart data. See [CHANGE_LOG](CHANGE_LOG) for detailed descriptions.

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
| **v8.7** | **Dedup by sw2 identity (not 30-min bucket); FVG search from sw2 confirmation bar; ES_FVG_MIN lowered to 0.5pt** — live |
| **v8.8** | **Audit S1/S2/S3 + confirmation-clock and session-gap fixes; entry/stop/target columns** — tagged OOS, live |
| v8.9 | SMT_IN_FVG membership by sw1/sw2 wick in pre-FVG — **not run live; omitted from future versions** |
| **v8.10** | **Built on v8.8 (not v8.9): ES-confirm and NQ-confirm scored independently; 90-min sw1–sw2 staleness** — live |

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
- **Structural pullback filter was critical** (v4–v8.4 era): without minimum retracement between consecutive swings (ES ≥4pt, NQ ≥15pt), false signal rates were unacceptably high. The v8.5 walk-back rewrite removed this check; the staleness windows handle recency instead

---

## Tools

### Trading Journal (v6)

A self-contained HTML trading journal (kept outside this repo) with:
- Signal CSV import with auto-populated fields (macro bias, 5m SMT, confirmations count, entry type)
- Signal review workflow with status tracking (pending → reviewed → logged)
- Trade history table with color-coded columns
- Equity curve visualization
- CSV export
- Light/dark theme

### Macro Bias Diagnostic

`tools/macro_bias_diagnostic.py` — standalone Python script that classifies 15m structural bias per session. Reads 1m CSVs, resamples to 15m, runs swing detection, and outputs session-by-session bias classification to CSV.

### Pine Script Indicator

`tools/macro_bias_background.pine` — TradingView Pine Script v6 indicator that displays macro bias as colored chart backgrounds. A Bias Timeframe input switches the calculation between 5m and 15m. The chart must be that timeframe or a lower one that divides it evenly. Deep green = strong bullish, light green = bullish slowing, deep red = strong bearish, light red = bearish slowing, yellow = transitional.

### Swing Marker Detection

`tools/swing_marker_detection.py` (and the matching `.pine`) tags 4-bar Swing High / Swing Low markers on 1-minute bars — the same rule the scanners and `backtesting_toolkit/projectx_historical_pull.py` use. Run `python tools/swing_marker_detection.py <csv_path> [output_path]`.

### Live Scanners and Backtesting Toolkit

See `live_scanners/README.md` (Slack alerts from v8.7, v8.8 and v8.10) and `backtesting_toolkit/README.md` (fill simulator, P&L report, version comparison).

---

## Session Coverage

| Session | Window (ET) |
|---------|------------|
| NY Morning | 8:00 AM – 10:30 AM |
| NY Afternoon | 1:00 PM – 3:00 PM |

30-minute pre-session lookback for swing context.

---

## Requirements

```bash
pip install -r requirements.txt
```

Input: one 1-minute bar CSV per instrument (MES and MNQ) with columns `time, open, high, low, close, Swing High, Swing Low` — either a TradingView export using the Swing High/Low Marker indicator, or a pull from `backtesting_toolkit/projectx_historical_pull.py`.

---

## Usage

```python
from scanner_versions.smt_scanner_v8_8 import run

results, ts_filtered, macro_filtered, stale_filtered, smt5m_filtered = run(
    es_path='path/to/MES_1min.csv',
    nq_path='path/to/MNQ_1min.csv'
)

results.to_csv('signals_output.csv', index=False)
```

---

## Status

Active development. Live (Slack alerts, no automated execution): v8.7, tagged OOS v8.8 and v8.10 — see `live_scanners/`. v8.9 is not run live.

Live-achievable backtests of v8.7 / v8.8 / v8.9 at the 50% FVG entry show negative expectancy so far; see `backtesting_toolkit/entry_variants.md`. Audit item S11 is still open.
