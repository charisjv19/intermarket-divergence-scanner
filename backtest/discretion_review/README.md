# Discretion review pack

Outcome-blind hand-review of v8.9 live-achievable (Step 2 closed-bar) signals.
Does **not** change any scanner or `simulate_fills.py`. Scoring is a separate
script and is never loaded by the review page.

## What it is

A stored random order of the May-Aug + Sep-Jan v8.9 closed-bar identities
(the same set Step 5 numbers use), charts clipped at the decision bar, and a
local HTML tool. Any completed prefix of the stored order is a random sample.

- FVG_AFTER_SMT decision bar = FVG completing bar (`fvg_bar + 1m`).
- SMT_IN_FVG decision bar = confirmation bar (`smt_time`).
- Images, filenames, HTML, and `decisions.csv` contain no outcome, R, or fill fields.

## Build the 40-signal pilot

```bash
python -m backtest.discretion_review.build_pack \
  --pilot 40 \
  --out /opt/cursor/artifacts/discretion_review_pilot
```

Open `review.html` from that folder. Keys: `T` take, `S` skip, `1`–`7`
skip+reason, `Backspace` undo. Reasons are a `REASONS` array at the top of
`review.html` (and in `review_template.html`). Autosave is browser
`localStorage`; download `decisions.csv` when you want a copy.

The full shuffled order (all May-Aug + Sep-Jan identities) is written to
`private/order_full.csv` so a later full pack can reuse the same IDs and order.

## Score after review

```bash
python -m backtest.discretion_review.score_review \
  --decisions decisions.csv \
  --id-map /opt/cursor/artifacts/discretion_review_pilot/private/id_map.csv \
  --fills /opt/cursor/artifacts/live_achievable_waterfall/step5_v89_combined.csv
```

Skipped and never-filled signals count as 0R. The reviewed set is split in
half by `stored_order`: first half for describing skip reasons, second half
held out.
