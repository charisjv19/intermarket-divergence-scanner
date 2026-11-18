# Correction note: MES files 37, 38, 39

Applies to:
- `CME_MINI_MES1_1_37_corrected.csv` (2026-04-19 18:00 ET to 2026-04-30 23:44 ET, 12,765 bars)
- `CME_MINI_MES1_1_38_corrected.csv` (2026-04-05 18:00 ET to 2026-04-17 16:59 ET, 13,800 bars)
- `CME_MINI_MES1_1_39_corrected.csv` (2026-03-22 18:00 ET to 2026-04-03 09:14 ET, 13,335 bars)

Correction made: **130.75 points subtracted** from `open`, `high`, `low`, `close` and `EMA`.
`time`, `Swing High` and `Swing Low` are unchanged. The swing columns are 0/1 flags, so the shift does not affect them.
Corrected on 2026-11-18.

The note is kept in a separate file so the CSVs still load with a plain `pd.read_csv`.

---

## Why a correction was needed

The original TradingView exports of these three MES files were exported in Nov 2026 to fill the April 2026 gap in the archive. Every MES price in them was exactly 130.75 points higher than the price of the same minute in the earlier exports. The matching MNQ files (36, 37, 38) from the same batch had no offset.

Probable cause: TradingView back-adjustment (B-ADJ) was on for the MES chart at export and off for MNQ. Back-adjustment shifts all history by the roll gaps between the bar's contract and the current one, so a single constant offset is what it produces. **Before future exports, turn B-ADJ off on both charts.**

## How the offset was found: overlapping data

The archive was missing roughly Apr 1 to Apr 26, 2026. The new files cover a wider window, so their edges overlap dates the archive already had:

| New file | Overlaps | Overlap window (ET) | Bars compared | Price difference (new minus old), OHLC |
|---|---|---|---|---|
| MES 39 | old 4 | 2026-03-22 18:00 to 2026-03-31 23:59 | 10,020 | +130.75 on every bar (min = max) |
| MES 39 | old 8 | 2026-03-22 18:00 to 2026-03-27 15:59 | 6,840 | +130.75 on every bar |
| MES 39 | old 5 | 2026-03-22 18:00 to 2026-03-25 13:58 | 3,959 | +130.75 on every bar |
| MES 37 | old 2 | 2026-04-26 18:00 to 2026-04-30 23:44 | 5,865 | +130.75 on every bar |
| MES 37 | old 1 | 2026-04-26 18:00 to 2026-04-30 23:44 | 5,864 | +130.75 on every bar |
| MES 38 | none | (falls entirely inside the old gap) | 0 | (see fills check below) |

The difference was identical on every bar, with no drift, noise or variation between open, high, low and close. That points to a fixed price-level shift, not different data. The new MNQ files, compared the same way over the same windows, matched the old MNQ files exactly. The one exception is a single bar (2026-04-28 08:20, high differs by 0.25), and the old files already disagreed with each other on that bar.

## Which version is right: checked against real fills

The old files had already been checked against the journal (`es_fvg_trades 11.csv`). Every MES and MNQ fill landed inside the bars. Those fills came from trades on the live contract, so they are independent of TradingView.

The April journal fills were checked against the new files. A fill passes when its entry and exit both lie inside the session's high-low range:

| File | Fills checked | Inside, as exported | Inside, after −130.75 |
|---|---|---|---|
| MES 37 | 17 | 0 | 15 |
| MES 38 | 12 | 0 | 11 |
| MES 39 | 8 | 0 | 8 |
| MNQ 36 / 37 / 38 (no correction) | 11 / 19 / 6 | all | n/a |

The 3 fills still outside after the correction are probably journal entry slips. One of them (2026-04-27, about 38 points below the session low) is also outside the old files.

MES 38 has no overlapping bars, so this fills check is its main confirmation. It shows the same 130.75 shift as the two files that do overlap.

## Checks on the corrected files

- Corrected MES 37 and 39 match the existing files on all overlapping bars: **0 mismatches**.
- All prices sit on the 0.25 MES tick grid.
- MES and MNQ have identical timestamps in every file pair (MES 37/MNQ 36, MES 38/MNQ 37, MES 39/MNQ 38), with no unpaired bars.

## Coverage with these files

Combined with the existing archive, MES and MNQ now run continuously from **2025-11-23 18:00 ET to 2026-05-17 18:32 ET**: 124 trading days and about 167,000 one-minute bars per symbol. The only short session in the new range is **Fri 2026-04-03**, which ends at 09:14 ET (Good Friday).

## Known caveats (unchanged by this correction)

- These are continuous front-month (MES1!/MNQ1!) series. MNQ shows contract-roll jumps at the 18:00 ET reopen on 2025-12-11, 2025-12-15 and 2026-03-16. Tests on 2026-11-18 showed they don't affect any SMT signal or the 15m bias. They will look like real price moves in any multi-day context study.
- The original, uncorrected MES 37–39 exports should not be used. Keep only the `_corrected` versions in the project.
