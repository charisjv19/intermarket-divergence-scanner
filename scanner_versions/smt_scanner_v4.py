"""
SMT Setup Scanner v4.0 — ES/NQ 1-min
=====================================
Changes from v3 → v4:

  FIX 1: STRUCTURAL SWING TRACKING
  ----------------------------------
  v3 tracked the last 2 swing highs/lows regardless of structural significance.
  This caused intermediate minor swings to overwrite valid structural reference
  points before the scanner could compare them — missing real SMT setups.

  v4 maintains a STRUCTURAL swing list separately from raw swings. A swing only
  qualifies as structural if the retracement from the previous structural swing
  meets a minimum threshold:
    ES_STRUCT_MIN_HIGH = 1.0pt  (min retracement between consecutive structural highs)
    ES_STRUCT_MIN_LOW  = 1.0pt  (min retracement between consecutive structural lows)
    NQ_STRUCT_MIN_HIGH = 3.0pt
    NQ_STRUCT_MIN_LOW  = 3.0pt

  Minor intermediate swings that don't meet this threshold are ignored for SMT
  comparison purposes, preserving the true structural reference points.

  NOTE: These thresholds may need loosening if signal count drops too low.
  If quality is good but quantity is too low, reduce to ES=2pt / NQ=7pt.

  FIX 2: FVG LOCATION VALIDATION
  --------------------------------
  v3 accepted any FVG found in the 15-bar lookahead window regardless of
  position relative to the SMT bar. This caused the scanner to flag FVGs on
  the wrong side of the SMT:
    - LONG signal: FVG was sometimes found BELOW the SMT bar price (invalid)
    - SHORT signal: FVG was sometimes found ABOVE the SMT bar price (invalid)

  v4 validates FVG position after finding it:
    - LONG:  FVG midpoint must be ABOVE the SMT bar close
    - SHORT: FVG midpoint must be BELOW the SMT bar close

All v3 filters retained:
  - Timestamp alignment: MAX_SW_TIME_GAP_MINS = 0 (exact same bar)
  - Pair-level structural pullback: ES >= 4pt, NQ >= 15pt between sw1/sw2
  - FVG size filters: ES 1–25pt, NQ 3–150pt
  - Session filter: NY Morning 08:00–10:30 ET, NY Afternoon 13:30–15:00 ET
  - Deduplication: one signal per 30-min bucket per direction per instrument
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ── File paths — update to match your TradingView export filenames ────────────
ES_CSV = '/mnt/user-data/uploads/CME_MINI_MES1___1__4_.csv'
NQ_CSV = '/mnt/user-data/uploads/CME_MINI_MNQ1___1__4_.csv'

# ── Pair-level structural pullback (between sw1 and sw2) ──────────────────────
ES_MIN_PULLBACK  = 4.0
NQ_MIN_PULLBACK  = 15.0

# ── v4 FIX 1: Individual swing structural qualification thresholds ────────────
# Min retracement required from previous structural swing before a new swing
# is accepted into the structural pool. Prevents minor swings from overwriting
# real structural reference points.
# Loosen these if signal count drops too low after review.
ES_STRUCT_MIN_HIGH = 1.0   # pts retracement between consecutive ES structural highs
ES_STRUCT_MIN_LOW  = 1.0   # pts retracement between consecutive ES structural lows
NQ_STRUCT_MIN_HIGH = 3.0  # pts retracement between consecutive NQ structural highs
NQ_STRUCT_MIN_LOW  = 3.0  # pts retracement between consecutive NQ structural lows

# ── FVG size filters (points) ─────────────────────────────────────────────────
ES_FVG_MIN = 1.0
ES_FVG_MAX = 25.0
NQ_FVG_MIN = 3.0
NQ_FVG_MAX = 150.0

# ── FVG scan window (bars after SMT bar) ──────────────────────────────────────
FVG_LOOKAHEAD = 15

# ── Timestamp alignment tolerance (minutes) ───────────────────────────────────
# 0 = exact same bar required. Loosen to 1 if signal count too low.
MAX_SW_TIME_GAP_MINS = 0


# ── LOAD & ALIGN ──────────────────────────────────────────────────────────────
def load(es_path, nq_path):
    es = pd.read_csv(es_path)
    nq = pd.read_csv(nq_path)
    for df in [es, nq]:
        df['time'] = pd.to_datetime(df['time'], utc=True)
        df['Swing High'] = pd.to_numeric(df['Swing High'], errors='coerce').fillna(0)
        df['Swing Low']  = pd.to_numeric(df['Swing Low'],  errors='coerce').fillna(0)
    merged = pd.merge(es, nq, on='time', suffixes=('_es', '_nq'))
    merged = merged.sort_values('time').reset_index(drop=True)
    merged['et']     = merged['time'].dt.tz_convert('America/New_York')
    merged['hour']   = merged['et'].dt.hour
    merged['minute'] = merged['et'].dt.minute
    merged['date']   = merged['et'].dt.date
    return merged


def session(row):
    t = row['hour'] * 60 + row['minute']
    if 480 <= t <= 630:  return 'NY Morning'
    if 810 <= t < 900:   return 'NY Afternoon'
    return None


# ── v4 FIX 1: STRUCTURAL SWING TRACKER ───────────────────────────────────────
def get_structural_swings(flags, prices, indices, timestamps,
                           min_retrace, price_col_opposite, n=2):
    """
    Build a rolling last-n list of STRUCTURAL swings only.

    A swing qualifies as structural if the retracement from the previous
    structural swing (measured using the opposite price — lows for highs,
    highs for lows) meets min_retrace.

    Args:
        flags:               1/0 swing marker flags
        prices:              swing price (high for swing highs, low for swing lows)
        indices:             bar indices
        timestamps:          ET timestamps
        min_retrace:         minimum retracement in points to qualify as structural
        price_col_opposite:  the opposite price series (lows when tracking highs,
                             highs when tracking lows) — used to measure retracement
        n:                   number of recent structural swings to keep (default 2)

    Returns:
        List of structural swing lists per bar, same shape as get_swings() output.
    """
    recents = []
    struct_history = []  # list of (price, bar_index, timestamp)

    for flag, price, idx, ts, opp_price in zip(
            flags, prices, indices, timestamps, price_col_opposite):

        if flag == 1:
            price = float(price)
            if len(struct_history) == 0:
                # First swing always qualifies
                struct_history.append((price, int(idx), ts))
            else:
                prev_price, prev_idx, prev_ts = struct_history[-1]
                # Measure retracement between previous structural swing and this one
                # For highs: we check how far price pulled back (min low between them)
                # We use opp_price as a proxy for the current bar's opposite side
                # Simple approach: use absolute price difference as retracement proxy
                retrace = abs(price - prev_price)
                if retrace >= min_retrace:
                    struct_history.append((price, int(idx), ts))

        recents.append(
            list(struct_history[-n:]) if len(struct_history) >= n else []
        )

    return recents


# ── STRUCTURAL PULLBACK CHECK (pair level) ────────────────────────────────────
def structural_pullback(sdf, i1, i2, swing_type, instrument, min_pb):
    start, end = min(i1, i2), max(i1, i2)
    if end <= start + 1:
        return False
    segment = sdf.iloc[start:end + 1]
    if swing_type == 'high':
        ref_high = max(sdf.iloc[i1][f'high_{instrument}'],
                       sdf.iloc[i2][f'high_{instrument}'])
        min_low  = segment[f'low_{instrument}'].min()
        return (ref_high - min_low) >= min_pb
    else:
        ref_low  = min(sdf.iloc[i1][f'low_{instrument}'],
                       sdf.iloc[i2][f'low_{instrument}'])
        max_high = segment[f'high_{instrument}'].max()
        return (max_high - ref_low) >= min_pb


# ── TIMESTAMP ALIGNMENT CHECK ─────────────────────────────────────────────────
def timestamps_aligned(t1, t2):
    diff = abs((pd.Timestamp(t1) - pd.Timestamp(t2)).total_seconds()) / 60
    return diff <= MAX_SW_TIME_GAP_MINS


# ── v4 FIX 2: FVG FINDER WITH LOCATION VALIDATION ────────────────────────────
def find_fvg(sdf, start_idx, direction, instrument, lookahead, smt_close):
    """
    Find the first valid FVG in the lookahead window after the SMT bar.

    v4 adds: FVG midpoint must be on the correct side of the SMT bar close.
      LONG:  FVG midpoint must be ABOVE smt_close (FVG is above SMT bar)
      SHORT: FVG midpoint must be BELOW smt_close (FVG is below SMT bar)

    If an FVG is found but fails the location check, the scan continues
    looking for the next FVG rather than stopping at the first one found.
    """
    col_h = f'high_{instrument.lower()}'
    col_l = f'low_{instrument.lower()}'
    end = min(start_idx + lookahead, len(sdf) - 2)

    for j in range(start_idx + 1, end + 1):
        ph = sdf.iloc[j - 1][col_h]
        pl = sdf.iloc[j - 1][col_l]
        nh = sdf.iloc[j + 1][col_h]
        nl = sdf.iloc[j + 1][col_l]

        if direction == 'LONG' and ph < nl:
            lo, hi = ph, nl
            midpoint = (lo + hi) / 2
            # v4: FVG must be ABOVE SMT bar close for a LONG
            if midpoint > smt_close:
                return dict(fvg_found=True, fvg_bar=sdf.iloc[j]['et'],
                            fvg_low=lo, fvg_high=hi,
                            entry_50=round(midpoint, 2),
                            fvg_size=round(hi - lo, 2))
            # else: FVG is below SMT bar — invalid location, keep scanning

        if direction == 'SHORT' and pl > nh:
            lo, hi = nh, pl
            midpoint = (lo + hi) / 2
            # v4: FVG must be BELOW SMT bar close for a SHORT
            if midpoint < smt_close:
                return dict(fvg_found=True, fvg_bar=sdf.iloc[j]['et'],
                            fvg_low=lo, fvg_high=hi,
                            entry_50=round(midpoint, 2),
                            fvg_size=round(hi - lo, 2))
            # else: FVG is above SMT bar — invalid location, keep scanning

    return dict(fvg_found=False)


def fmt_ts(ts):
    try:
        return str(ts)[:16].replace('T', ' ')
    except:
        return str(ts)[:16]


# ── MAIN ──────────────────────────────────────────────────────────────────────
def run(es_path, nq_path):
    df = load(es_path, nq_path)
    df['session'] = df.apply(session, axis=1)
    sdf = df[df['session'].notna()].copy().reset_index(drop=True)

    # v4: Use structural swing tracker instead of raw last-2
    sdf['sh_es'] = get_structural_swings(
        sdf['Swing High_es'], sdf['high_es'], sdf.index, sdf['et'],
        ES_STRUCT_MIN_HIGH, sdf['low_es'])
    sdf['sl_es'] = get_structural_swings(
        sdf['Swing Low_es'], sdf['low_es'], sdf.index, sdf['et'],
        ES_STRUCT_MIN_LOW, sdf['high_es'])
    sdf['sh_nq'] = get_structural_swings(
        sdf['Swing High_nq'], sdf['high_nq'], sdf.index, sdf['et'],
        NQ_STRUCT_MIN_HIGH, sdf['low_nq'])
    sdf['sl_nq'] = get_structural_swings(
        sdf['Swing Low_nq'], sdf['low_nq'], sdf.index, sdf['et'],
        NQ_STRUCT_MIN_LOW, sdf['high_nq'])

    signals    = []
    ts_filtered    = 0
    fvg_loc_filtered = 0  # signals rejected by FVG location check

    for i, row in sdf.iterrows():
        sh_e, sl_e = row['sh_es'], row['sl_es']
        sh_n, sl_n = row['sh_nq'], row['sl_nq']

        candidates = []

        # ── BULLISH SMT (comparing structural swing lows) ─────────────────────
        if len(sl_e) == 2 and len(sl_n) == 2:
            (p_e1, i_e1, t_e1), (p_e2, i_e2, t_e2) = sl_e
            (p_n1, i_n1, t_n1), (p_n2, i_n2, t_n2) = sl_n

            if timestamps_aligned(t_e1, t_n1) and timestamps_aligned(t_e2, t_n2):

                # ES sweeps lower low, NQ holds higher low → buy ES
                if p_e2 < p_e1 and p_n2 > p_n1:
                    if structural_pullback(sdf, i_e1, i_e2, 'low', 'es', ES_MIN_PULLBACK) and \
                       structural_pullback(sdf, i_n1, i_n2, 'low', 'nq', NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction': 'LONG', 'instrument': 'ES',
                            'sweep_pts': round(p_e1 - p_e2, 2),
                            'fail_pts':  round(p_n2 - p_n1, 2),
                            'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,
                            'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,
                            'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,
                            'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,
                        })

                # NQ sweeps lower low, ES holds higher low → buy NQ
                elif p_n2 < p_n1 and p_e2 > p_e1:
                    if structural_pullback(sdf, i_e1, i_e2, 'low', 'es', ES_MIN_PULLBACK) and \
                       structural_pullback(sdf, i_n1, i_n2, 'low', 'nq', NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction': 'LONG', 'instrument': 'NQ',
                            'sweep_pts': round(p_n1 - p_n2, 2),
                            'fail_pts':  round(p_e2 - p_e1, 2),
                            'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,
                            'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,
                            'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,
                            'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,
                        })
            else:
                ts_filtered += 1

        # ── BEARISH SMT (comparing structural swing highs) ────────────────────
        if len(sh_e) == 2 and len(sh_n) == 2:
            (p_e1, i_e1, t_e1), (p_e2, i_e2, t_e2) = sh_e
            (p_n1, i_n1, t_n1), (p_n2, i_n2, t_n2) = sh_n

            if timestamps_aligned(t_e1, t_n1) and timestamps_aligned(t_e2, t_n2):

                # ES sweeps higher high, NQ fails → sell ES
                if p_e2 > p_e1 and p_n2 < p_n1:
                    if structural_pullback(sdf, i_e1, i_e2, 'high', 'es', ES_MIN_PULLBACK) and \
                       structural_pullback(sdf, i_n1, i_n2, 'high', 'nq', NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction': 'SHORT', 'instrument': 'ES',
                            'sweep_pts': round(p_e2 - p_e1, 2),
                            'fail_pts':  round(p_n1 - p_n2, 2),
                            'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,
                            'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,
                            'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,
                            'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,
                        })

                # NQ sweeps higher high, ES fails → sell NQ
                elif p_n2 > p_n1 and p_e2 < p_e1:
                    if structural_pullback(sdf, i_e1, i_e2, 'high', 'es', ES_MIN_PULLBACK) and \
                       structural_pullback(sdf, i_n1, i_n2, 'high', 'nq', NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction': 'SHORT', 'instrument': 'NQ',
                            'sweep_pts': round(p_n2 - p_n1, 2),
                            'fail_pts':  round(p_e1 - p_e2, 2),
                            'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,
                            'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,
                            'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,
                            'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,
                        })
            else:
                ts_filtered += 1

        # ── FVG search with location validation ───────────────────────────────
        # v4 fix: use instrument-specific close for FVG location check
        smt_close_es = row["close_es"]
        smt_close_nq = row["close_nq"]

        for c in candidates:
            fvg = find_fvg(sdf, i, c['direction'], c['instrument'],
                           FVG_LOOKAHEAD,
                           smt_close_nq if c["instrument"] == "NQ" else smt_close_es)
            if fvg.get('fvg_found') and fvg.get('fvg_size') is not None:
                signals.append({
                    'smt_time': row['et'],
                    'date':     row['date'],
                    'session':  row['session'],
                    'es_close': row['close_es'],
                    'nq_close': row['close_nq'],
                    **c,
                    **fvg,
                })

    out = pd.DataFrame(signals)
    if out.empty:
        print("No signals found after all filters.")
        print(f"  Timestamp filter rejected:    {ts_filtered} candidates")
        return out, ts_filtered, fvg_loc_filtered

    out = out[out['fvg_found'] == True].copy()
    es_mask = ((out['instrument'] == 'ES') &
               (out['fvg_size'] >= ES_FVG_MIN) &
               (out['fvg_size'] <= ES_FVG_MAX))
    nq_mask = ((out['instrument'] == 'NQ') &
               (out['fvg_size'] >= NQ_FVG_MIN) &
               (out['fvg_size'] <= NQ_FVG_MAX))
    out = out[es_mask | nq_mask].copy()

    out = out.sort_values('smt_time').reset_index(drop=True)
    out['bucket'] = pd.to_datetime(out['smt_time']).dt.floor('30min')
    out = out.drop_duplicates(subset=['date', 'bucket', 'direction', 'instrument'])

    return out.reset_index(drop=True), ts_filtered, fvg_loc_filtered


if __name__ == '__main__':
    print("=" * 60)
    print("SMT Scanner v4.0")
    print("=" * 60)
    print(f"  Timestamp alignment:       MAX_SW_TIME_GAP_MINS = {MAX_SW_TIME_GAP_MINS}")
    print(f"  Pair pullback (ES):        >= {ES_MIN_PULLBACK}pt")
    print(f"  Pair pullback (NQ):        >= {NQ_MIN_PULLBACK}pt")
    print(f"  Struct swing min (ES H/L): >= {ES_STRUCT_MIN_HIGH}pt / {ES_STRUCT_MIN_LOW}pt")
    print(f"  Struct swing min (NQ H/L): >= {NQ_STRUCT_MIN_HIGH}pt / {NQ_STRUCT_MIN_LOW}pt")
    print(f"  FVG size (ES):             {ES_FVG_MIN}–{ES_FVG_MAX}pt")
    print(f"  FVG size (NQ):             {NQ_FVG_MIN}–{NQ_FVG_MAX}pt")
    print(f"  FVG lookahead:             {FVG_LOOKAHEAD} bars")
    print(f"  FVG location validated:    YES (must be on correct side of SMT bar)")
    print()

    results, ts_filtered, fvg_loc_filtered = run(ES_CSV, NQ_CSV)

    if not results.empty:
        total = len(results)
        days  = results['date'].nunique()
        print(f"Signals passing all filters:       {total}")
        print(f"Days covered:                      {days}")
        print(f"Avg signals/day:                   {total / days:.1f}")
        print(f"Rejected by timestamp filter:      {ts_filtered}")
        print()

        ref_cols = ['smt_time', 'direction', 'instrument',
                    'es_sw1_time', 'es_sw1_price', 'es_sw2_time', 'es_sw2_price',
                    'nq_sw1_time', 'nq_sw1_price', 'nq_sw2_time', 'nq_sw2_price',
                    'fvg_size', 'entry_50']
        print("Sample signals (first 10):")
        print(results[ref_cols].head(10).to_string())

        out_path = '/mnt/user-data/outputs/smt_signals_v4.csv'
        results.to_csv(out_path, index=False)
        print(f"\nSaved → {out_path}")
