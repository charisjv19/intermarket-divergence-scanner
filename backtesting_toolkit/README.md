# Backtesting toolkit

Research tools for testing scanner versions on historical 1-minute data.
Nothing here changes a scanner. Run every command from the **repo root**
after `pip install -r requirements.txt`.

Cursor rules for these tools live in `.cursor/rules/backtest-*.mdc`
(Cursor only reads rules from that folder). Every report built on the fill
simulator must disclose its three known gaps: partial exits not simulated,
concurrent-trade limits not enforced, 5m-opposing-SMT blocking not
implemented.

## Files

| File | What it does |
|------|--------------|
| `projectx_historical_pull.py` | Pulls TopstepX 1-min bars in 6-hour pages and tags 4-bar swings. Also used by the live runners. |
| `data.py` | `pull_data()` disk cache (`backtesting_toolkit/cache/`, gitignored) and TradingView CSV fallback. |
| `structural_tests.py` | Mechanical checks a version must pass before any backtest. |
| `simulate_fills.py` | Walks 1-min OHLC from each signal: touch logic, stop-first on same bar, 20-min / 50%-to-TP limit cancels, 11:00 flatten after 09:45 entries. |
| `pnl_report.py` | Wilson CI on win rate, bootstrap expectancy, profit factor, binomial test vs 25%. |
| `compare_versions.py` | `review_these.csv` — only the differences between two versions. |
| `reconstruct_v87.py`, `v87_v88_compare.py` | v8.7 levels rebuilt from the documented formula; v8.7 vs v8.8 categories. |
| `discretion_review/` | Outcome-blind 40-signal hand-review pack (see its README). |
| `entry_variants.md` | Entry-price variant results (live-achievable Step 5). |
| `fixtures/` | Pine swing reference CSV for the structural tests. |

TopstepX only keeps about 6–8 weeks of recent front-month 1-min history.
Older windows need contract-specific TradingView exports.

## Commands

```bash
# Pull history (needs PROJECTX_USERNAME / PROJECTX_API_KEY in the environment or .env)
python -m backtesting_toolkit.projectx_historical_pull --contract-id CON.F.US.MES.Z26 \
    --start 2026-10-01 --end 2026-10-05 --out MES.csv

# Structural checks (must pass before a backtest)
python -m backtesting_toolkit.structural_tests --signals signals.csv --pine-csv MES.csv --scanner smt_scanner_v8_8.py

# Fill simulation and P&L report
python -m backtesting_toolkit.simulate_fills --signals signals.csv --es-bars MES.csv --nq-bars MNQ.csv --out fills.csv --spot-check 10
python -m backtesting_toolkit.pnl_report --signals signals.csv --es-bars MES.csv --nq-bars MNQ.csv --out-dir report/

# Compare two versions (prefer --a-signals/--b-signals when the CSVs already exist)
python -m backtesting_toolkit.compare_versions --a smt_scanner_v8_8.py --b smt_scanner_v8_10.py \
    --es MES.csv --nq MNQ.csv --out review_these.csv
python -m backtesting_toolkit.v87_v88_compare --v87-signals v87.csv --v88-signals v88.csv \
    --es-bars MES.csv --nq-bars MNQ.csv --out-dir cmp/

# Tests
python -m unittest discover -s backtesting_toolkit/tests -t . -p 'test_*.py'
```

## Not in the repo

The scripts and data behind the live-achievable "Step 5" waterfall numbers in
`entry_variants.md` (`/tmp/waterfall/`, `/tmp/windows/`) and the report
outputs under `/opt/cursor/artifacts/` were produced on a Cursor cloud
machine and never committed. `discretion_review/score_review` needs
`step5_v89_combined.csv` from that run.
