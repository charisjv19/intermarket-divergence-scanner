"""
SMT Setup Scanner v5.0 — ES/NQ 1-min
=====================================
Changes from v4 → v5:

  FIX 1: FVG LOCATION REFERENCE PRICE
  -------------------------------------
  v4 used the SMT bar close price to validate FVG location. This was too
  strict — it rejected a valid LONG FVG on May 13 (NQ) by just 2.13pts
  because the midpoint fell slightly below the close, even though the FVG
  was clearly above the swept low.

  v5 uses the SWEPT EXTREME as the location anchor instead:
    - LONG:  FVG midpoint must be ABOVE the sw2 swept low price
    - SHORT: FVG midpoint must be BELOW the sw2 swept high price

  This is more aligned with how the setup reads on the chart — the swept
  extreme is the true structural reference point, not the close of the SMT bar.

  FIX 2: EQUAL HIGH/LOW SMT VARIANT
  ------------------------------------
  v4 only accepted SMT where one instrument made a strictly higher or lower
  swing. v5 also accepts the case where one instrument makes an EQUAL high
  or low (within EQUAL_THRESHOLD points) while the other diverges.
  EQUAL_THRESHOLD = 0.25pt (ES tick size — allows for rounding differences)

  FIX 3: SESSION TIME CUTOFF
  ---------------------------
  v4 flagged signals regardless of where the FVG formed relative to session
  end. Two signals were manually cancelled because the FVG formed outside
  the session window (March 25, March 30).

  v5 enforces two session rules:
    a) FVG bar must be within the same session as the SMT bar
    b) SMT bar must fire at least SESSION_CUTOFF_MINS before session end,
       giving enough time to complete a trade. Default: 30 minutes.
       (NY Morning cutoff: 10:00am ET, NY Afternoon cutoff: 14:30pm ET)

  FIX 4: SMT AND FVG MUST BE IN THE SAME SESSION
  ------------------------------------------------
  Explicit check: if the FVG bar falls outside session hours (even by one
  bar), the signal is discarded. This catches edge cases where the SMT fires
  near session end and the FVG forms just after close.

All v4 filters retained:
  - Structural swing tracking (ES >= 1pt, NQ >= 3pt individual swing filter)
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

# ── Individual swing structural qualification thresholds ──────────────────────
ES_STRUCT_MIN_HIGH = 1.0
ES_STRUCT_MIN_LOW  = 1.0
NQ_STRUCT_MIN_HIGH = 3.0
NQ_STRUCT_MIN_LOW  = 3.0

# ── FVG size filters (points) ─────────────────────────────────────────────────
ES_FVG_MIN = 1.0
ES_FVG_MAX = 25.0
NQ_FVG_MIN = 3.0
NQ_FVG_MAX = 150.0

# ── FVG scan window (bars after SMT bar) ──────────────────────────────────────
FVG_LOOKAHEAD = 15

# ── Timestamp alignment tolerance (minutes) ───────────────────────────────────
MAX_SW_TIME_GAP_MINS = 0

# ── v5 FIX 2: Equal high/low SMT tolerance ───────────────────────────────────
# Allow one instrument to make an EQUAL swing (within this threshold) while
# the other diverges — counts as a valid SMT divergence.
# 0.25 = one ES tick. Set to 0.0 to disable equal-high/low variant.
EQUAL_THRESHOLD = 0.25

# ── v5 FIX 3: Session cutoff — minimum minutes before session end ─────────────
# SMT must fire at least this many minutes before session end to be valid.
# NY Morning ends 10:30 → cutoff at 10:00 (30 min buffer)
# NY Afternoon ends 15:00 → cutoff at 14:30 (30 min buffer)
SESSION_CUTOFF_MINS = 30

SESSIONS = [
    ('NY Morning',   (8, 0),   (10, 30)),
    ('NY Afternoon', (13, 30), (15, 0)),
]


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


def get_session(hour, minute):
    """Return session name and end time (h, m) if within session, else (None, None)."""
    t = hour * 60 + minute
    for name, (sh, sm), (eh, em) in SESSIONS:
        start = sh * 60 + sm
        end   = eh * 60 + em
        if start <= t < end:
            return name, (eh, em)
    return None, None


def session(row):
    name, _ = get_session(row['hour'], row['minute'])
    return name


def within_cutoff(hour, minute):
    """Return True if bar is at least SESSION_CUTOFF_MINS before session end."""
    t = hour * 60 + minute
    for name, (sh, sm), (eh, em) in SESSIONS:
        start = sh * 60 + sm
        end   = eh * 60 + em
        cutoff = end - SESSION_CUTOFF_MINS
        if start <= t < cutoff:
            return True
    return False


def fvg_in_session(fvg_bar_et):
    """Return True if FVG bar timestamp falls within any session window."""
    h = fvg_bar_et.hour
    m = fvg_bar_et.minute
    t = h * 60 + m
    for name, (sh, sm), (eh, em) in SESSIONS:
        start = sh * 60 + sm
        end   = eh * 60 + em
        if start <= t < end:
            return True
    return False


# ── STRUCTURAL SWING TRACKER ──────────────────────────────────────────────────
def get_structural_swings(flags, prices, indices, timestamps, min_retrace, n=2):
    recents = []
    struct_history = []
    for flag, price, idx, ts in zip(flags, prices, indices, timestamps):
        if flag == 1:
            price = float(price)
            if len(struct_history) == 0:
                struct_history.append((price, int(idx), ts))
            else:
                prev_price = struct_history[-1][0]
                if abs(price - prev_price) >= min_retrace:
                    struct_history.append((price, int(idx), ts))
        recents.append(list(struct_history[-n:]) if len(struct_history) >= n else [])
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


# ── v5 FIX 1: FVG FINDER WITH SWEPT EXTREME LOCATION VALIDATION ───────────────
def find_fvg(sdf, start_idx, direction, instrument, lookahead, swept_extreme):
    """
    Find first valid FVG in lookahead window after SMT bar.

    v5 change: FVG midpoint validated against SWEPT EXTREME (sw2 price)
    rather than SMT bar close:
      LONG:  FVG midpoint must be ABOVE swept_extreme (the swept low)
      SHORT: FVG midpoint must be BELOW swept_extreme (the swept high)

    Continues scanning if a FVG is found but fails the location check.
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
            if midpoint > swept_extreme:
                # v5 FIX 4: FVG bar must be within session hours
                fvg_bar_et = sdf.iloc[j]['et']
                if not fvg_in_session(fvg_bar_et):
                    continue  # FVG outside session — keep scanning
                return dict(fvg_found=True, fvg_bar=fvg_bar_et,
                            fvg_low=lo, fvg_high=hi,
                            entry_50=round(midpoint, 2),
                            fvg_size=round(hi - lo, 2))

        if direction == 'SHORT' and pl > nh:
            lo, hi = nh, pl
            midpoint = (lo + hi) / 2
            if midpoint < swept_extreme:
                fvg_bar_et = sdf.iloc[j]['et']
                if not fvg_in_session(fvg_bar_et):
                    continue
                return dict(fvg_found=True, fvg_bar=fvg_bar_et,
                            fvg_low=lo, fvg_high=hi,
                            entry_50=round(midpoint, 2),
                            fvg_size=round(hi - lo, 2))

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

    sdf['sh_es'] = get_structural_swings(
        sdf['Swing High_es'], sdf['high_es'], sdf.index, sdf['et'], ES_STRUCT_MIN_HIGH)
    sdf['sl_es'] = get_structural_swings(
        sdf['Swing Low_es'],  sdf['low_es'],  sdf.index, sdf['et'], ES_STRUCT_MIN_LOW)
    sdf['sh_nq'] = get_structural_swings(
        sdf['Swing High_nq'], sdf['high_nq'], sdf.index, sdf['et'], NQ_STRUCT_MIN_HIGH)
    sdf['sl_nq'] = get_structural_swings(
        sdf['Swing Low_nq'],  sdf['low_nq'],  sdf.index, sdf['et'], NQ_STRUCT_MIN_LOW)

    signals     = []
    ts_filtered = 0
    cutoff_filtered = 0
    session_fvg_filtered = 0

    for i, row in sdf.iterrows():
        sh_e, sl_e = row['sh_es'], row['sl_es']
        sh_n, sl_n = row['sh_nq'], row['sl_nq']

        # v5 FIX 3: skip if SMT bar fires too close to session end
        if not within_cutoff(row['hour'], row['minute']):
            cutoff_filtered += 1
            continue

        candidates = []

        # ── BULLISH SMT (comparing structural swing lows) ─────────────────────
        if len(sl_e) == 2 and len(sl_n) == 2:
            (p_e1, i_e1, t_e1), (p_e2, i_e2, t_e2) = sl_e
            (p_n1, i_n1, t_n1), (p_n2, i_n2, t_n2) = sl_n

            if timestamps_aligned(t_e1, t_n1) and timestamps_aligned(t_e2, t_n2):

                # ES sweeps lower low, NQ holds higher low → buy ES
                # v5 FIX 2: also accept equal NQ low (within EQUAL_THRESHOLD)
                es_lower = p_e2 < p_e1
                nq_higher = p_n2 > p_n1
                nq_equal  = abs(p_n2 - p_n1) <= EQUAL_THRESHOLD

                if es_lower and (nq_higher or nq_equal):
                    if structural_pullback(sdf, i_e1, i_e2, 'low', 'es', ES_MIN_PULLBACK) and \
                       structural_pullback(sdf, i_n1, i_n2, 'low', 'nq', NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction': 'LONG', 'instrument': 'ES',
                            'sweep_pts': round(p_e1 - p_e2, 2),
                            'fail_pts':  round(abs(p_n2 - p_n1), 2),
                            'smt_variant': 'equal' if nq_equal else 'standard',
                            'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,
                            'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,
                            'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,
                            'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,
                            'swept_extreme': p_e2,  # swept low for LONG
                        })

                # NQ sweeps lower low, ES holds higher low → buy NQ
                nq_lower = p_n2 < p_n1
                es_higher = p_e2 > p_e1
                es_equal  = abs(p_e2 - p_e1) <= EQUAL_THRESHOLD

                if nq_lower and (es_higher or es_equal):
                    if structural_pullback(sdf, i_e1, i_e2, 'low', 'es', ES_MIN_PULLBACK) and \
                       structural_pullback(sdf, i_n1, i_n2, 'low', 'nq', NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction': 'LONG', 'instrument': 'NQ',
                            'sweep_pts': round(p_n1 - p_n2, 2),
                            'fail_pts':  round(abs(p_e2 - p_e1), 2),
                            'smt_variant': 'equal' if es_equal else 'standard',
                            'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,
                            'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,
                            'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,
                            'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,
                            'swept_extreme': p_n2,  # swept low for LONG
                        })
            else:
                ts_filtered += 1

        # ── BEARISH SMT (comparing structural swing highs) ────────────────────
        if len(sh_e) == 2 and len(sh_n) == 2:
            (p_e1, i_e1, t_e1), (p_e2, i_e2, t_e2) = sh_e
            (p_n1, i_n1, t_n1), (p_n2, i_n2, t_n2) = sh_n

            if timestamps_aligned(t_e1, t_n1) and timestamps_aligned(t_e2, t_n2):

                # ES sweeps higher high, NQ fails → sell ES
                es_higher = p_e2 > p_e1
                nq_lower  = p_n2 < p_n1
                nq_equal  = abs(p_n2 - p_n1) <= EQUAL_THRESHOLD

                if es_higher and (nq_lower or nq_equal):
                    if structural_pullback(sdf, i_e1, i_e2, 'high', 'es', ES_MIN_PULLBACK) and \
                       structural_pullback(sdf, i_n1, i_n2, 'high', 'nq', NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction': 'SHORT', 'instrument': 'ES',
                            'sweep_pts': round(p_e2 - p_e1, 2),
                            'fail_pts':  round(abs(p_n1 - p_n2), 2),
                            'smt_variant': 'equal' if nq_equal else 'standard',
                            'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,
                            'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,
                            'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,
                            'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,
                            'swept_extreme': p_e2,  # swept high for SHORT
                        })

                # NQ sweeps higher high, ES fails → sell NQ
                nq_higher = p_n2 > p_n1
                es_lower  = p_e2 < p_e1
                es_equal  = abs(p_e2 - p_e1) <= EQUAL_THRESHOLD

                if nq_higher and (es_lower or es_equal):
                    if structural_pullback(sdf, i_e1, i_e2, 'high', 'es', ES_MIN_PULLBACK) and \
                       structural_pullback(sdf, i_n1, i_n2, 'high', 'nq', NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction': 'SHORT', 'instrument': 'NQ',
                            'sweep_pts': round(p_n2 - p_n1, 2),
                            'fail_pts':  round(abs(p_e1 - p_e2), 2),
                            'smt_variant': 'equal' if es_equal else 'standard',
                            'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,
                            'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,
                            'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,
                            'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,
                            'swept_extreme': p_n2,  # swept high for SHORT
                        })
            else:
                ts_filtered += 1

        for c in candidates:
            fvg = find_fvg(sdf, i, c['direction'], c['instrument'],
                           FVG_LOOKAHEAD, c['swept_extreme'])
            if fvg.get('fvg_found'):
                signals.append({
                    'smt_time':    row['et'],
                    'date':        row['date'],
                    'session':     row['session'],
                    'es_close':    row['close_es'],
                    'nq_close':    row['close_nq'],
                    **{k: v for k, v in c.items() if k != 'swept_extreme'},
                    **fvg,
                })

    out = pd.DataFrame(signals)
    if out.empty:
        print("No signals found after all filters.")
        print(f"  Timestamp filter rejected:      {ts_filtered}")
        print(f"  Session cutoff rejected:        {cutoff_filtered}")
        return out, ts_filtered, cutoff_filtered

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

    return out.reset_index(drop=True), ts_filtered, cutoff_filtered


if __name__ == '__main__':
    print("=" * 60)
    print("SMT Scanner v5.0")
    print("=" * 60)
    print(f"  Timestamp alignment:       MAX_SW_TIME_GAP_MINS = {MAX_SW_TIME_GAP_MINS}")
    print(f"  Pair pullback (ES):        >= {ES_MIN_PULLBACK}pt")
    print(f"  Pair pullback (NQ):        >= {NQ_MIN_PULLBACK}pt")
    print(f"  Struct swing min (ES H/L): >= {ES_STRUCT_MIN_HIGH}pt / {ES_STRUCT_MIN_LOW}pt")
    print(f"  Struct swing min (NQ H/L): >= {NQ_STRUCT_MIN_HIGH}pt / {NQ_STRUCT_MIN_LOW}pt")
    print(f"  FVG size (ES):             {ES_FVG_MIN}–{ES_FVG_MAX}pt")
    print(f"  FVG size (NQ):             {NQ_FVG_MIN}–{NQ_FVG_MAX}pt")
    print(f"  FVG lookahead:             {FVG_LOOKAHEAD} bars")
    print(f"  FVG location ref:          swept extreme (sw2 price)")
    print(f"  Equal high/low SMT:        YES (threshold = {EQUAL_THRESHOLD}pt)")
    print(f"  Session cutoff:            {SESSION_CUTOFF_MINS} min before session end")
    print(f"  FVG session check:         YES (FVG bar must be within session)")
    print()

    result = run(ES_CSV, NQ_CSV)
    if isinstance(result, tuple):
        results, ts_filtered, cutoff_filtered = result
    else:
        results = result
        ts_filtered = cutoff_filtered = 0

    if not results.empty:
        total = len(results)
        days  = results['date'].nunique()
        print(f"Signals passing all filters:    {total}")
        print(f"Days covered:                   {days}")
        print(f"Avg signals/day:                {total / days:.1f}")
        print(f"Rejected by timestamp filter:   {ts_filtered}")
        print(f"Rejected by session cutoff:     {cutoff_filtered}")
        print()

        ref_cols = ['smt_time', 'direction', 'instrument', 'smt_variant',
                    'es_sw1_time', 'es_sw1_price', 'es_sw2_time', 'es_sw2_price',
                    'nq_sw1_time', 'nq_sw1_price', 'nq_sw2_time', 'nq_sw2_price',
                    'fvg_size', 'entry_50']
        print("Sample signals (first 10):")
        print(results[ref_cols].head(10).to_string())

        out_path = '/mnt/user-data/outputs/smt_signals_v5.csv'
        results.to_csv(out_path, index=False)
        print(f"\nSaved → {out_path}")
    else:
        print("No signals found.")
