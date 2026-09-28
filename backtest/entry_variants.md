# Entry-price variants (live-achievable Step 5)

Not a scanner version. Production scanners and `backtest/simulate_fills.py`
are untouched. Fills are walked with the analysis copies in `/tmp/waterfall/`.

Fill-simulator known gaps (every variant):
- partial exits NOT simulated (no codified rule exists)
- concurrent-trade limits NOT enforced (policy undecided)
- 5m-opposing-SMT blocking NOT implemented (evidence-gathering stage)

Commission excluded. Tapes: the six sha-verified TradingView windows in
`/tmp/windows/` (no Gateway / ProjectX).

Live-achievable Step 5 assumptions (shared by every variant below):
closed-bar 15m/5m macro filters; limit wait starts on the first bar after
the FVG completing bar closes (`fvg_bar + 2m`); timeout is completing-bar
+ 20m; next-bar-open market entries for v8.8/v8.9 `SMT_IN_FVG`; 1-tick
trade-through on limits; 1-tick adverse slippage on market entries and
stop exits; stop-first on same-bar SL/TP; 50%-to-target cancel while
unfilled; session-end no-fill.

Stop anchors are not retuned. Target multiples (and the 50%-to-target
cancel) are recomputed off the variant’s entry-to-stop distance.

Applies to every **limit** trade: `FVG_AFTER_SMT` in v8.7/v8.8/v8.9 and
v8.7 `SMT_IN_FVG`. v8.8/v8.9 `SMT_IN_FVG` stays a market entry.

---

## Baseline — 50% FVG limit

The documented / scanner 50% level (`entry_50` = midpoint of `fvg_low`
and `fvg_high`). Combined Step 5 MTM (existing waterfall CSVs):

- v8.7 n=675 no-fill=220 E=−0.095R PF=0.852 eq MTM=−43.015R eq eod0=−98.382R max DD=−51.470R
- v8.8 n=597 no-fill=125 E=−0.172R PF=0.754 eq MTM=−80.963R eq eod0=−125.780R max DD=−84.054R
- v8.9 n=596 no-fill=103 E=−0.162R PF=0.767 eq MTM=−80.005R eq eod0=−116.294R max DD=−81.636R

## Variant #1 — near-edge FVG limit (tried)

Limit at the FVG near edge (gap boundary closest to price when the FVG
completes):

- LONG: gap upper bound `fvg_high` (bar j+1’s low)
- SHORT: gap lower bound `fvg_low` (bar j+1’s high)

No other depths (25%, 75%, edge ± ticks) were run in this pass.

Combined Step 5 result vs 50% baseline (eq MTM):

- v8.7 −43.015R → −72.535R (Δ −29.520R; worse in all three windows)
- v8.8 −80.963R → −96.508R (Δ −15.545R; Feb–Apr +1.028R, May–Aug −10.322R, Sep–Jan −6.251R)
- v8.9 −80.005R → −98.265R (Δ −18.260R; Feb–Apr +0.836R, May–Aug −11.451R, Sep–Jan −7.644R)

Paired day-block 95% CIs on Δ total R all contain 0. Zero trades filled at 50% but not near-edge. Limit rows added no inverted or zero-risk cases (the three v8.9 inverted rows are the existing SMT market entries). Never-traded = 0.

Report artifacts: `/opt/cursor/artifacts/entry_variant_1_near_edge/`.
