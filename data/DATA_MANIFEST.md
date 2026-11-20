# MES / MNQ 1-minute archive: consolidated data manifest

Built 2026-11-18. Updated 2026-11-19: TopstepX 1-second data and volume delta added; MNQ export 39 merged in; MES back-adjustment removed before 2026-03-16; MNQ confirmed unadjusted; Feb 27 last-hour bars added (see below). Replaces the per-symbol TradingView exports with one file per symbol.

| File | Rows | Range (ET) |
|---|---|---|
| `MES_1m_2025-11-23_2026-05-17.csv` | 167,069 | 2025-11-23 18:00 to 2026-05-17 18:32 |
| `MNQ_1m_2025-11-23_2026-05-17.csv` | 167,027 | 2025-11-23 18:00 to 2026-05-17 18:32 |
| `MES_swing_flag_differences.csv` | 19 | Bars where recomputed swing flags differ from TradingView's (see below) |
| `MNQ_swing_flag_differences.csv` | 34 | Same, for MNQ |

Format: the same scanner format as the original exports (`time, open, high, low, close, Swing High, Swing Low, EMA`), with `time` in ISO-8601 Eastern time. They load directly with `smt_scanner_v8_10.run(es_path, nq_path)`. Verified 2026-11-18: a full run completes and returns 376 signals.

## Sources

| Symbol | Source files | Covers |
|---|---|---|
| MES | Project exports 1, 2, 3, 4, 5, 8–16, **with back-adjustment removed before the Mar 16 roll** (−108.75 before 2025-12-15 18:00 ET, −50.75 from then to 2026-03-16 18:00 ET) | Nov 23, 2025 to Mar 31, 2026 and Apr 26 to May 17, 2026 |
| MES | Nov 2026 exports 37, 38, 39 **after a −130.75 pt correction** (see `data/MES_37-39_CORRECTION_NOTE.md`) | Mar 22 to Apr 30, 2026 |
| MNQ | Project exports 1, 2, 3, 4, 5, 8–16 | as MES |
| MNQ | Nov 2026 exports 36, 37, 38 (no correction needed) | Mar 22 to Apr 30, 2026 |
| MNQ | Nov 2026 export 39 (no correction needed; 3,795 overlapping bars matched to the tick) | May 12 23:28 to May 15 16:59, 2026: fills 17 minutes missing from MNQ export 3 |

There are no files numbered 6, 7 or 17–35.

## MES back-adjustment correction (2026-11-19)

The original MES exports (1–16) were made with TradingView back-adjustment (B-ADJ) on, and the MNQ exports with it off. B-ADJ shifts history by the roll gaps between each bar's contract and the contract that was front at export time. So the old MES bars were too high by a different constant in each roll period:

| Period (ET) | Offset in old MES exports | Measured against | Bars compared | Result |
|---|---|---|---|---|
| before 2025-12-15 18:00 | +108.75 | MES export 41 (B-ADJ off), 2025-11-27 to 12-01 | 2,376 | identical difference on every OHLC value |
| 2025-12-15 18:00 to 2026-03-16 18:00 | +50.75 | MES export 40 (B-ADJ off), 2026-02-26 to 03-02 | 3,496 | identical difference on every OHLC value |
| from 2026-03-16 18:00 | 0 | journal fills, 2026-03-19 onward | 45 fills | inside the bars |

**MNQ was checked the same way and needs no correction.** MNQ exports 40 (Nov 30 to Dec 1, 1,659 bars) and 41 (Feb 27 to Mar 2, 1,725 overlapping bars), both made with B-ADJ off, match the archive with zero difference on every value. That fits MNQ's visible roll jumps.

This gives MES roll gaps of 58.00 points (December) and 50.75 points (March). Corrections applied to `open`, `high`, `low`, `close` and `EMA`; swing flags were then recomputed (2 changed, both at the 2026-03-16 roll).

How the roll boundaries were confirmed: MES had no jump at either roll while MNQ did. After the correction, MES steps up with MNQ at both reopens: 2025-12-15 18:00 (MES +0.89%, MNQ +1.01%) and 2026-03-16 18:00 (MES +0.76%, MNQ +0.88%). Placing the December boundary at 2025-12-11 instead produces contradictory moves, so it was rejected.

After the correction, the file matches both B-ADJ-off exports with 0 mismatches, every price is on the 0.25 tick grid, and 45 of 48 MES journal fills sit inside the bars (the other 3 are the known journal slips). Swings, SMTs, FVGs and the 15m bias depend on price differences within a roll period, so they are unchanged. Absolute MES levels before Mar 16 are now true contract prices.

## How overlaps were resolved

218k raw MES rows and 218k raw MNQ rows reduce to about 167k unique bars per symbol. Wherever files overlap, every bar matched exactly, with one exception: MNQ 2026-04-28 08:20, where the high differed by 0.25 (27125.00 in export 1; 27124.75 in exports 2 and 36). The majority value, **27124.75**, was kept.

## Checks passed

- No duplicate timestamps; every timestamp is on a whole minute.
- No missing OHLC values. High ≥ open/close ≥ low on every bar.
- Every price is on the 0.25 tick grid.
- Overlaps were also checked against the journal fills in `es_fvg_trades 11.csv`. MES and MNQ fills fall inside the bars (apart from three probable journal slips noted in the correction note).

## Swing flags: recomputed

`Swing High` / `Swing Low` were **recomputed over the full continuous series** using `compute_swings` from `backtesting_toolkit/projectx_historical_pull.py`. This is the same 4-bar rule and function the live feed uses: a bar is a swing high if its high is above the two bars before it and the one bar after.

They differ from TradingView's exported flags on 19 MES bars and 34 MNQ bars, out of about 97,000 flags (≈0.05%). Each one is listed in the `_swing_flag_differences.csv` files:
- **Most are at the edges of export files** (Friday 15:59, Sunday 18:00 reopen, Apr 3 09:14). A single export can't confirm a swing on its first or last bars; the merged series can. All of these fall outside the NY sessions.
- **The remaining dozen are inside MNQ exports 1/2 and MES exports 1/2** (Apr 30 to May 12). Here TradingView's flags don't match its own OHLC under the 4-bar rule; some are shifted by a bar. Several fall inside NY sessions (e.g. MNQ 2026-04-30 10:03, 2026-05-06 09:57, 2026-05-07 11:53). Backtests run on the original exports could see slightly different swing points there.

## Coverage

124 trading days per symbol, continuous apart from weekends. Short or partial sessions:

| Trading day | Ends (ET) | Reason |
|---|---|---|
| Nov 27 / Nov 28, 2025 | 12:59 / 13:14 | Thanksgiving / day after |
| Dec 24, 2025 | 13:14 | Christmas Eve |
| Jan 19, 2026 / Feb 16, 2026 | 12:59 | MLK Day / Presidents' Day |
| Apr 3, 2026 | 09:14 | Good Friday |
| Fridays Dec 5, Dec 19, Jan 2, Jan 16, Jan 30, Feb 13, Mar 13 | 15:59 |
| Fri Feb 27 | 16:59 MES; MNQ is missing 16:00–16:42 (last hour added from the B-ADJ-off reference exports) | **The 16:00–16:59 hour is missing from those exports** (session normally ends 16:59) |
| May 17, 2026 (Sun) | 18:32 | End of data: first 33 minutes of the session only |

Bars present in only one symbol: 46 MES-only minutes (2026-02-27 16:00–16:42, plus 2026-05-10 18:00 and 2026-05-12 20:31 and 22:32 ET, all outside sessions) and 4 MNQ-only minutes (thin overnight minutes in December, plus 2026-05-14 12:51 ET). MNQ export 39 filled the other 17 gaps from May 13–15 on 2026-11-19; the rest were **not filled**. Filling the gap changed one recomputed swing flag (2026-05-14 00:13 ET, now a swing low as well). The scanner joins MES and MNQ on time, so those minutes drop out of both.

## 1-second data and volume delta (TopstepX, from Nov 2026)

**Source:** TopstepX `retrieveBars` with 1-second bars (`projectx_historical_pull --unit second`), contract `.Z26`. Prices are raw contract prices (no back-adjustment). Each row is one second that traded (seconds with no trades have no row), with columns `time, open, high, low, close, Volume`, times in UTC.

**History depth:** TopstepX keeps 1-second bars for a **rolling 14 days** (a pull on 2026-11-19 ~18:56 ET started at 2026-11-05 18:55 ET). From here on, data is collected weekly by `weekly_1s_to_repo.py` into the private historical-data repo. Anything older than 14 days at pull time is not recoverable from TopstepX.

| File | Rows (seconds) | Range (ET) |
|---|---|---|
| `MES_1s_2026-11-05_2026-11-18.csv` | 608,605 | 2026-11-05 18:55:35 to 2026-11-18 23:59:59 |
| `MNQ_1s_2026-11-05_2026-11-18.csv` | 748,568 | 2026-11-05 19:00:19 to 2026-11-18 23:59:59 |

The first session (Nov 6) is partial: it is missing its first 55 minutes for MES and first 60 for MNQ. Nov 19 is partial (to midnight ET only).

**Checks (2026-11-19):**
- No duplicates, no impossible bars, all prices on the 0.25 tick grid, all volumes positive.
- **Against TradingView 1-minute exports (Nov 8–19, 11,400 overlapping minutes per symbol):** 1-minute bars rebuilt from the seconds match TradingView exactly on 99.5–99.98% of minutes for each OHLC field (max difference 1.5 pts). Almost all differences are trades landing in the neighbouring minute; MNQ daily volumes match exactly.
- **One real volume difference:** MES on Nov 17, 09:38–09:39 and 09:48–09:49 ET. TopstepX has 12,673 more contracts over those four minutes than TradingView. TradingView's values there are less than half of the neighbouring minutes, while TopstepX's are in line, so TradingView probably dropped trades. This is unconfirmed without a third source.

**Volume delta** (`backtesting_toolkit/volume_delta.py`) turns the seconds into one row per minute: `open, high, low, close, Volume, Buy Vol, Sell Vol, Delta, Delta High, Delta Low, Active Secs`.
- Each second's volume counts as buying if the second closed above its open, and selling if below. A flat second is compared with the previous second's close; if that's also unchanged, it keeps the previous side.
- Delta = Buy Vol − Sell Vol. Delta High/Low are the extremes of the running delta within the minute.
- This is an **estimate** from 1-second price direction, not true buyer/seller-initiated trade data.
- Sanity checks: buy + sell = volume on every minute. Delta correlates with the minute's price change at 0.81 (MES) and 0.83 (MNQ) in NY hours. MES and MNQ delta correlate at 0.76.

| File | Minutes | Range |
|---|---|---|
| `MES_1m_delta_2026-11-05_2026-11-18.csv` | 12,725 | as above |
| `MNQ_1m_delta_2026-11-05_2026-11-18.csv` | 12,720 | as above |

These are kept separate from the main 1-minute archive until the archive is extended past May 17, 2026. Delta exists only from Nov 5, 2026; earlier months have none.

## Known caveats

1. **Unadjusted continuous front-month series (MES1!/MNQ1!).** Both symbols jump at the contract roll at the 18:00 ET reopen: 2025-12-15 (MES +60.75 including a 58.00 roll gap, MNQ +253.50) and 2026-03-16 (MES +51.00 including a 50.75 roll gap, MNQ +216.50). The 2025-12-11 MNQ drop (−115.50, MES −9.50) is a real overnight move, not a roll. Tested 2026-11-18: rolls don't affect any SMT signal or the 15m bias, because no swing pair or last-4-swing window spans a reopen. They will look like real price moves to any multi-day context analysis.
2. **EMA column is partial and mixed-source.** It is filled for 55k MES and 38k MNQ bars. MNQ exports 3–5 and 36–38 have none, and values come from separate TradingView chart loads. Current scanners (v8.8, v8.10) don't read it, but don't rely on it.
3. **None of the original MES exports should be used directly.** Exports 9–16 and the pre-Mar-16 parts of 4/5/8 carry +50.75 / +108.75, and exports 37–39 carry +130.75. Use this consolidated file.
4. **For future TradingView exports:** turn back-adjustment (B-ADJ) off on both charts, and export Fridays through 16:59.
