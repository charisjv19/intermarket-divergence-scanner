# Live scanner notes

Human review log for **live Slack alerts** vs the chart. This is not
`CHANGE_LOG` (shipped version diffs) and not tagged-OOS evidence.

**Logging here never changes a running scanner.** An entry, a
`Wanted change` line, or an open-queue row is observation only. It is
not a work order, not a bugfix ticket, and not permission to edit,
retune, push, or cut a new version.

Currently open live versions (do not touch from this log):

- `smt_scanner_v8_7.py` — live v8.7
- `smt_scanner_v8_8.py` — tagged OOS v8.8
- `smt_scanner_v8_10.py` — live dual-confirm v8.10

Any version change, patch, push, or new scanner file requires:

1. An **explicit** request to change that version (naming it), and
2. **Direct verification** of the change before it is treated as done.

Do not infer a code change from “this alert was wrong.” Threshold
retunes (`ES_FVG_MIN`, lookahead, etc.) to recover one live print are
overfit unless that explicit request flags them first.

Machine record of what actually posted: `live/state/v87_alerts.jsonl`,
`v88_alerts.jsonl`, `v810_alerts.jsonl` (gitignored). This file is the
chart-vs-alert notes.

---

## How to add an entry

Paste a new `##` block **at the top of the log** (below the open queue).
One Slack miss or false per block. Fill every field; use `n/a` rather
than deleting a line.

```md
## YYYY-MM-DD HH:MM ET — short title

- **Versions:** v8.7 / v8.8 / v8.10 (which you were watching)
- **Slack:** posted / missing / late (relayed_at if known)
- **Chart:** sw1, sw2, direction, MES vs MNQ, FVG time if any
- **Kind:** miss | false | delay | format | protocol | ops
- **Disposition:** expected | bug | new-protocol | open
- **What I saw:**
- **What the scanner did:**
- **Why (if known):**
- **Wanted change:** none | note only — does not start a version change
```

Kinds:

| Kind | Means |
|------|--------|
| miss | Chart SMT/FVG a live version did not alert |
| false | Alert that should not have fired |
| delay | Correct identity, Slack later than the chart event |
| format | Alert fired; swings / FVG / 15m text wrong |
| protocol | Spec behaved; the spec may be wrong |
| ops | Poll, webhook, seed, lag — not detection logic |

---

## Open queue

Watch list only. Rows here do **not** start work on v8.7, v8.8, v8.10,
or any later version. Implementation needs an explicit ask plus
verification.

| ID | Observation | Suggested track | Status |
|----|-------------|-----------------|--------|
| Q1 | ES-confirm exclusive drops NQ-confirm on the same sw2 | v8.10 dual-confirm (already live) | watching |
| Q2 | Stale ES-confirm (e.g. 09:08) occupies the exclusive slot and hides a later NQ-confirm (09:18 / 09:36) | v8.8 exclusive only; v8.10 already scores both | watching |
| Q3 | SMT_IN_FVG clock is sw2+1 close-in-pre-FVG, not “when I see the FVG after SMT” | new protocol if we want fire-at-FVG | open |
| Q4 | Live poll can lag tens of minutes; Slack time ≠ FVG bar | ops | open |
| Q5 | Primary sw1 is the **oldest** failed parallel; later chart sw1s land in `alt_sw1_times` | format (Slack now prints alts) | watching |

---

## Log (newest first)

## 2026-10-06 09:18 / 09:36 — NQ SMT not FVG_AFTER_SMT on v8.8 / v8.7

- **Versions:** v8.7, tagged OOS v8.8, v8.10
- **Slack:** v8.8 missing; v8.7 missing (had an earlier 08:37 MNQ only); v8.10 posted both at `2026-10-06T13:46:21Z` (~09:46 ET)
- **Chart:** LONG NQ-confirm. 09:18 MNQ 07:37 `31497.50` → `31493.25` (swept), MES 07:37 `7857.00` → `7859.75` (HL). 09:36 MNQ 08:26 `31496.00` → `31457.50` (swept), MES equal `7860.25`. Post-SMT MNQ FVGs at 09:24 (14.00 pt) and 09:38 (13.75 pt).
- **Kind:** miss on v8.7/v8.8; expected on v8.10
- **Disposition:** expected vs frozen exclusive spec; **new-protocol** already running as v8.10 (Q1/Q2)
- **What I saw:** SMT between 09:18 and 09:36 with FVG after SMT. No v8.8 / v8.7 FVG_AFTER_SMT for that pair.
- **What the scanner did:** v8.10 `FVG_AFTER_SMT LONG MNQ` identities `2026-10-06|13:18|LONG|NQ` (FVG 09:24, sw1 07:37) and `2026-10-06|13:36|LONG|NQ` (FVG 09:38, primary sw1 08:26, alts `08:37, 09:00, 09:18`). v8.8 `run()` on the same bars: no Oct 6 signals. v8.7 Oct 6: only 08:37 MNQ FVG_AFTER_SMT.
- **Why (if known):** v8.8/v8.7 ES-first exclusive stayed on an **older ES-confirm** MES 08:01 `7862.25` → 09:08 `7861.25` / MNQ HL `31502` → `31507`. That candidate never got a valid MES FVG inside 15 bars above the swept low `7861.25`, so exclusive mode emitted nothing and never scored the NQ-confirm. 09:18/09:36 are not ES-confirm (MES HL / equal). Combined 15m was `BULLISH (SLOWING)` — macro did not block. Artifact: `/opt/cursor/artifacts/smt_0918_0936_diagnosis.txt`.
- **Wanted change:** note only (Q1/Q2). Do not patch v8.7, v8.8, or v8.10 from this entry.

## 2026-10-06 07:30 / 07:37 — expected SMT_IN_FVG, none fired

- **Versions:** v8.7, v8.8, v8.10
- **Slack:** none for that pair
- **Chart:** MES 07:30 SL `7855.75`, 07:37 SL `7857.00` (HL, not a sweep). MNQ 07:30 is a **swing high**, not a swing low (`31504` low is not tagged SL; SL is 07:31 `31500.50`). 07:38 gap is pre-session.
- **Kind:** miss (chart read) — scanner had no LONG SMT
- **Disposition:** expected
- **What I saw:** 07:30/07:37 looked like SMT into a gap.
- **What the scanner did:** `detect_smt` LONG none. Pre-session FVG is not a valid FVG_AFTER_SMT formation window (`fvg_in_session` only). SMT_IN_FVG can *membership-match* a pre-session gap, but only after a real SMT.
- **Why (if known):** No exact-timestamp parallel swing lows at 07:30 (NQ is SH). MES did not wick-break 07:30 at 07:37 (`7857.00` > `7855.75`). Not a dual-confirm miss.
- **Wanted change:** none. Note only.

## 2026-10-06 ~09:46 ET — v8.10 Slack late vs FVG bars

- **Versions:** v8.10 (also v8.8 poll `signals=3 new=0` around 13:45Z)
- **Slack:** v8.10 both 09:18 and 09:36 identities relayed together at 13:46:21Z
- **Chart:** MNQ FVG bars 09:24 and 09:38 ET
- **Kind:** delay / ops
- **Disposition:** open (Q4)
- **What I saw:** Alerts arrived ~8–22 minutes after the FVG bars (and after a long poll gap).
- **What the scanner did:** Live wrapper runs full-window `run()` on closed 1m bars, then relays new identities only. A slow poll holds both signals until that cycle finishes.
- **Why (if known):** Not a detection miss on v8.10. Slack timestamp is relay time, not `fvg_bar`.
- **Wanted change:** note only (Q4). Late Slack is not a detection miss. Do not change the runner from this entry.

## 2026-10-05 09:14 / 09:24 — NQ-confirm blocked; ES path did fire

- **Versions:** v8.7, v8.8, v8.10
- **Slack:** v8.7 `SMT_IN_FVG LONG MES` ~09:29; v8.8 `FVG_AFTER_SMT LONG MES`; v8.10 also `LONG NQ` at the same sw2 09:24
- **Chart:** sw2 09:24. User pair 09:14/09:24 was NQ-confirm / ES-fail. Primary ES-confirm sw1 was 08:20 (ES swept). 09:14 is an alt on the NQ-confirm identity.
- **Kind:** protocol (Q1) + format (Q5)
- **Disposition:** v8.8 exclusive = expected skip of NQ; v8.10 new-protocol covers it
- **What I saw:** 09:14/09:24 looked like the SMT pair; Slack sw1 was 08:20.
- **What the scanner did:** Same sw2 09:24. Oldest failed-parallel is primary sw1; later parallels go to `alt_sw1_times` (Slack lists them). v8.7 SMT_IN_FVG still required the post-SMT FVG in that version (09:27). v8.8 S2 SMT_IN_FVG is sw2+1 membership; that morning’s ES path was FVG_AFTER_SMT because 09:25 close had left the 09:17 FVG.
- **Why (if known):** ES-first exclusive. Primary sw1 = oldest, not the chart’s most obvious later swing.
- **Wanted change:** note only (Q1/Q5). No version edit from this entry.

## 2026-10-05 08:37 — v8.7 MNQ FVG_AFTER_SMT, not v8.8

- **Versions:** v8.7 vs v8.8
- **Slack:** v8.7 posted Oct 6 08:37 `FVG_AFTER_SMT LONG MNQ` (FVG 08:50). v8.8 did not.
- **Chart:** sw2 08:37. v8.7 sw1 MES 07:30 / MNQ 07:31 (1-minute offset).
- **Kind:** miss on v8.8 relative to v8.7
- **Disposition:** expected — S3
- **What I saw:** v8.7 alert, no matching v8.8.
- **What the scanner did:** v8.7 `SW1_PARALLEL_TOL_MINS = 2`. Tagged OOS v8.8 is exact timestamp only (`0`).
- **Why (if known):** Defect S3. Do not loosen v8.8 sw1 tolerance to recover this signal.
- **Wanted change:** note only. v8.7-only (S3). Do not loosen v8.8 from this entry.
