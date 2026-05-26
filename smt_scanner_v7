"""
SMT Setup Scanner v7.0 — ES/NQ 1-min
=====================================
Changes from v6 → v7:

  FIX 1: STRUCTURAL SWING FILTER REMOVED (PRIORITY FIX)
  -------------------------------------------------------
  v6 had individual structural swing qualification thresholds:
    ES_STRUCT_MIN_HIGH/LOW = 1.0pt
    NQ_STRUCT_MIN_HIGH/LOW = 3.0pt

  These thresholds were filtering out valid structural swing points when
  consecutive swings were close in price, causing the scanner to miss real
  SMT setups. Confirmed across 4 separate examples:

    March 25  13:10/13:19  ES 0.00pt diff filtered   → missed LONG NQ
    April 13  08:56/09:09  ES 0.50pt diff filtered   → missed LONG NQ (hit 3R)
    April 14  08:25/08:54  intermediate swings overwrote sw1 → missed LONG ES (hit 3R)
    April 14  09:08/09:29  NQ 2.00pt diff filtered   → missed LONG NQ (no-fill)

  In all cases timestamps matched, divergence was real, and pair-level
  pullback filters passed — the ONLY reason signals were missed was the
  individual structural swing threshold.

  v7 FIX: Remove individual structural swing thresholds entirely.
  The scanner now tracks ALL tagged swing highs/lows (last 2) without
  any price-difference filtering between consecutive swings.
  Structural qualification is handled ENTIRELY by the pair-level
  pullback filter (ES >= 2pt, NQ >= 8pt between sw1 and sw2),
  which is the correct and sufficient place to enforce structure.

All other v6 filters retained:
  - Timestamp alignment: MAX_SW_TIME_GAP_MINS = 0 (exact same bar)
  - Pair-level structural pullback: ES >= 2pt, NQ >= 8pt between sw1/sw2
  - FVG location: midpoint above swept extreme (LONG) / below (SHORT)
  - Equal high/low SMT: exact price match only (EQUAL_THRESHOLD = 0.0)
  - Session cutoff: 30 min before session end
  - Pre-session lookback: 30 min before each session
  - NY Afternoon start: 13:00 ET
  - FVG must form within same session as SMT
  - FVG size filters: ES 1-25pt, NQ 3-150pt
  - Deduplication: one signal per 30-min bucket per direction per instrument
"""

import pandas as pd
import numpy as np

# -- File paths ----------------------------------------------------------------
ES_CSV = '/mnt/user-data/uploads/12april_24april_MES.csv'
NQ_CSV = '/mnt/user-data/uploads/12april_24april_MNQ.csv'

# -- Pair-level structural pullback (between sw1 and sw2) ----------------------
# These are now the ONLY structural filters. Loosened from v6 (ES 4pt→2pt, NQ 15pt→8pt)
ES_MIN_PULLBACK = 2.0
NQ_MIN_PULLBACK = 8.0

# -- FVG size filters (points) -------------------------------------------------
ES_FVG_MIN = 1.0;  ES_FVG_MAX = 25.0
NQ_FVG_MIN = 3.0;  NQ_FVG_MAX = 150.0

# -- FVG scan window -----------------------------------------------------------
FVG_LOOKAHEAD = 15

# -- Timestamp alignment tolerance (minutes) -----------------------------------
MAX_SW_TIME_GAP_MINS = 0

# -- Equal high/low SMT: exact price match only --------------------------------
EQUAL_THRESHOLD = 0.0

# -- Session cutoff (minutes before session end) --------------------------------
SESSION_CUTOFF_MINS = 30

# -- Pre-session swing lookback (minutes before session start) -----------------
PRE_SESSION_LOOKBACK_MINS = 30

# -- Session definitions -------------------------------------------------------
# (name, start_hour, start_min, end_hour, end_min)
SESSIONS = [
    ('NY Morning',   8,  0, 10, 30),
    ('NY Afternoon', 13, 0, 15,  0),
]


# -- LOAD & ALIGN --------------------------------------------------------------
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


def classify_bar(hour, minute):
    """Returns (bar_type, session_name) or (None, None)."""
    t = hour * 60 + minute
    for name, sh, sm, eh, em in SESSIONS:
        ss = sh*60+sm; se = eh*60+em
        ps = ss - PRE_SESSION_LOOKBACK_MINS
        ce = se - SESSION_CUTOFF_MINS
        if ss <= t < ce:  return 'session', name
        if ps <= t < ss:  return 'pre_session', name
    return None, None


def fvg_in_session(et):
    t = et.hour*60 + et.minute
    for name, sh, sm, eh, em in SESSIONS:
        if sh*60+sm <= t < eh*60+em:
            return True
    return False


# -- v7: SIMPLE LAST-2 SWING TRACKER (no structural threshold) -----------------
def get_swings(flags, prices, indices, timestamps, n=2):
    """
    Track last n tagged swings with NO price-difference filtering.
    All swing marker tags are accepted — structural qualification is
    handled by the pair-level pullback filter downstream.
    """
    recents = []
    history = []
    for flag, price, idx, ts in zip(flags, prices, indices, timestamps):
        if flag == 1:
            history.append((float(price), int(idx), ts))
        recents.append(list(history[-n:]) if len(history) >= n else [])
    return recents


# -- PAIR-LEVEL STRUCTURAL PULLBACK CHECK --------------------------------------
def structural_pullback(sdf, i1, i2, swing_type, instrument, min_pb):
    start, end = min(i1, i2), max(i1, i2)
    if end <= start + 1:
        return False
    segment = sdf.iloc[start:end + 1]
    if swing_type == 'high':
        ref = max(sdf.iloc[i1][f'high_{instrument}'], sdf.iloc[i2][f'high_{instrument}'])
        return (ref - segment[f'low_{instrument}'].min()) >= min_pb
    else:
        ref = min(sdf.iloc[i1][f'low_{instrument}'], sdf.iloc[i2][f'low_{instrument}'])
        return (segment[f'high_{instrument}'].max() - ref) >= min_pb


# -- TIMESTAMP ALIGNMENT -------------------------------------------------------
def timestamps_aligned(t1, t2):
    return abs((pd.Timestamp(t1) - pd.Timestamp(t2)).total_seconds()) / 60 <= MAX_SW_TIME_GAP_MINS


# -- FVG FINDER ----------------------------------------------------------------
def find_fvg(sdf, start_idx, direction, instrument, lookahead, swept_extreme):
    col_h = f'high_{instrument.lower()}'
    col_l = f'low_{instrument.lower()}'
    end = min(start_idx + lookahead, len(sdf) - 2)
    for j in range(start_idx + 1, end + 1):
        ph = sdf.iloc[j-1][col_h]; pl = sdf.iloc[j-1][col_l]
        nh = sdf.iloc[j+1][col_h]; nl = sdf.iloc[j+1][col_l]
        if direction == 'LONG' and ph < nl:
            lo, hi = ph, nl; mid = (lo+hi)/2
            if mid > swept_extreme and fvg_in_session(sdf.iloc[j]['et']):
                return dict(fvg_found=True, fvg_bar=sdf.iloc[j]['et'],
                            fvg_low=lo, fvg_high=hi,
                            entry_50=round(mid,2), fvg_size=round(hi-lo,2))
        if direction == 'SHORT' and pl > nh:
            lo, hi = nh, pl; mid = (lo+hi)/2
            if mid < swept_extreme and fvg_in_session(sdf.iloc[j]['et']):
                return dict(fvg_found=True, fvg_bar=sdf.iloc[j]['et'],
                            fvg_low=lo, fvg_high=hi,
                            entry_50=round(mid,2), fvg_size=round(hi-lo,2))
    return dict(fvg_found=False)


def fmt_ts(ts):
    try: return str(ts)[:16].replace('T', ' ')
    except: return str(ts)[:16]


# -- MAIN ----------------------------------------------------------------------
def run(es_path, nq_path):
    df = load(es_path, nq_path)
    bcs = [classify_bar(r['hour'], r['minute']) for _, r in df.iterrows()]
    df['bar_type'], df['bar_session'] = zip(*bcs)
    sdf = df[df['bar_type'].notna()].copy().reset_index(drop=True)
    sdf['session'] = sdf['bar_session']

    # v7: no structural threshold — track all tagged swings
    sdf['sh_es'] = get_swings(sdf['Swing High_es'], sdf['high_es'], sdf.index, sdf['et'])
    sdf['sl_es'] = get_swings(sdf['Swing Low_es'],  sdf['low_es'],  sdf.index, sdf['et'])
    sdf['sh_nq'] = get_swings(sdf['Swing High_nq'], sdf['high_nq'], sdf.index, sdf['et'])
    sdf['sl_nq'] = get_swings(sdf['Swing Low_nq'],  sdf['low_nq'],  sdf.index, sdf['et'])

    signals = []
    ts_filtered = 0

    for i, row in sdf.iterrows():
        if row['bar_type'] != 'session':
            continue

        sh_e, sl_e = row['sh_es'], row['sl_es']
        sh_n, sl_n = row['sh_nq'], row['sl_nq']
        candidates = []

        # -- BULLISH SMT -------------------------------------------------------
        if len(sl_e) == 2 and len(sl_n) == 2:
            (p_e1,i_e1,t_e1),(p_e2,i_e2,t_e2) = sl_e
            (p_n1,i_n1,t_n1),(p_n2,i_n2,t_n2) = sl_n

            if timestamps_aligned(t_e1,t_n1) and timestamps_aligned(t_e2,t_n2):
                # ES sweeps lower, NQ holds higher or equal
                if p_e2 < p_e1 and (p_n2 > p_n1 or p_n2 == p_n1):
                    if structural_pullback(sdf,i_e1,i_e2,'low','es',ES_MIN_PULLBACK) and \
                       structural_pullback(sdf,i_n1,i_n2,'low','nq',NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction':'LONG','instrument':'ES',
                            'sweep_pts':round(p_e1-p_e2,2),
                            'fail_pts':round(abs(p_n2-p_n1),2),
                            'smt_variant':'equal' if p_n2==p_n1 else 'standard',
                            'es_sw1_time':fmt_ts(t_e1),'es_sw1_price':p_e1,
                            'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':p_e2,
                            'nq_sw1_time':fmt_ts(t_n1),'nq_sw1_price':p_n1,
                            'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':p_n2,
                            'swept_extreme':p_e2,
                        })
                # NQ sweeps lower, ES holds higher or equal
                elif p_n2 < p_n1 and (p_e2 > p_e1 or p_e2 == p_e1):
                    if structural_pullback(sdf,i_e1,i_e2,'low','es',ES_MIN_PULLBACK) and \
                       structural_pullback(sdf,i_n1,i_n2,'low','nq',NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction':'LONG','instrument':'NQ',
                            'sweep_pts':round(p_n1-p_n2,2),
                            'fail_pts':round(abs(p_e2-p_e1),2),
                            'smt_variant':'equal' if p_e2==p_e1 else 'standard',
                            'es_sw1_time':fmt_ts(t_e1),'es_sw1_price':p_e1,
                            'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':p_e2,
                            'nq_sw1_time':fmt_ts(t_n1),'nq_sw1_price':p_n1,
                            'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':p_n2,
                            'swept_extreme':p_n2,
                        })
            else:
                ts_filtered += 1

        # -- BEARISH SMT -------------------------------------------------------
        if len(sh_e) == 2 and len(sh_n) == 2:
            (p_e1,i_e1,t_e1),(p_e2,i_e2,t_e2) = sh_e
            (p_n1,i_n1,t_n1),(p_n2,i_n2,t_n2) = sh_n

            if timestamps_aligned(t_e1,t_n1) and timestamps_aligned(t_e2,t_n2):
                # ES sweeps higher, NQ fails or equal
                if p_e2 > p_e1 and (p_n2 < p_n1 or p_n2 == p_n1):
                    if structural_pullback(sdf,i_e1,i_e2,'high','es',ES_MIN_PULLBACK) and \
                       structural_pullback(sdf,i_n1,i_n2,'high','nq',NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction':'SHORT','instrument':'ES',
                            'sweep_pts':round(p_e2-p_e1,2),
                            'fail_pts':round(abs(p_n1-p_n2),2),
                            'smt_variant':'equal' if p_n2==p_n1 else 'standard',
                            'es_sw1_time':fmt_ts(t_e1),'es_sw1_price':p_e1,
                            'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':p_e2,
                            'nq_sw1_time':fmt_ts(t_n1),'nq_sw1_price':p_n1,
                            'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':p_n2,
                            'swept_extreme':p_e2,
                        })
                # NQ sweeps higher, ES fails or equal
                elif p_n2 > p_n1 and (p_e2 < p_e1 or p_e2 == p_e1):
                    if structural_pullback(sdf,i_e1,i_e2,'high','es',ES_MIN_PULLBACK) and \
                       structural_pullback(sdf,i_n1,i_n2,'high','nq',NQ_MIN_PULLBACK):
                        candidates.append({
                            'direction':'SHORT','instrument':'NQ',
                            'sweep_pts':round(p_n2-p_n1,2),
                            'fail_pts':round(abs(p_e1-p_e2),2),
                            'smt_variant':'equal' if p_e2==p_e1 else 'standard',
                            'es_sw1_time':fmt_ts(t_e1),'es_sw1_price':p_e1,
                            'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':p_e2,
                            'nq_sw1_time':fmt_ts(t_n1),'nq_sw1_price':p_n1,
                            'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':p_n2,
                            'swept_extreme':p_n2,
                        })
            else:
                ts_filtered += 1

        for c in candidates:
            fvg = find_fvg(sdf, i, c['direction'], c['instrument'],
                           FVG_LOOKAHEAD, c['swept_extreme'])
            if fvg.get('fvg_found'):
                signals.append({
                    'smt_time': row['et'], 'date': row['date'],
                    'session':  row['session'],
                    'es_close': row['close_es'], 'nq_close': row['close_nq'],
                    **{k:v for k,v in c.items() if k != 'swept_extreme'},
                    **fvg,
                })

    out = pd.DataFrame(signals)
    if out.empty:
        print(f"No signals. Timestamp filter rejected: {ts_filtered}")
        return out, ts_filtered

    out = out[out['fvg_found'] == True].copy()
    es_mask = (out['instrument']=='ES')&(out['fvg_size']>=ES_FVG_MIN)&(out['fvg_size']<=ES_FVG_MAX)
    nq_mask = (out['instrument']=='NQ')&(out['fvg_size']>=NQ_FVG_MIN)&(out['fvg_size']<=NQ_FVG_MAX)
    out = out[es_mask|nq_mask].copy()
    out = out.sort_values('smt_time').reset_index(drop=True)
    out['bucket'] = pd.to_datetime(out['smt_time']).dt.floor('30min')
    out = out.drop_duplicates(subset=['date','bucket','direction','instrument'])
    return out.reset_index(drop=True), ts_filtered


if __name__ == '__main__':
    print("=" * 60)
    print("SMT Scanner v7.0")
    print("=" * 60)
    print(f"  Structural swing filter:   REMOVED — all tagged swings tracked")
    print(f"  Pair pullback (ES/NQ):     >= {ES_MIN_PULLBACK}pt / >= {NQ_MIN_PULLBACK}pt")
    print(f"  Timestamp alignment:       {MAX_SW_TIME_GAP_MINS} min (exact same bar)")
    print(f"  Equal SMT threshold:       exact price match only")
    print(f"  FVG size (ES):             {ES_FVG_MIN}-{ES_FVG_MAX}pt")
    print(f"  FVG size (NQ):             {NQ_FVG_MIN}-{NQ_FVG_MAX}pt")
    print(f"  FVG lookahead:             {FVG_LOOKAHEAD} bars")
    print(f"  FVG location ref:          swept extreme (sw2 price)")
    print(f"  Session cutoff:            {SESSION_CUTOFF_MINS} min before end")
    print(f"  Pre-session lookback:      {PRE_SESSION_LOOKBACK_MINS} min")
    print(f"  NY Afternoon start:        13:00 ET")
    print()

    results, ts_filtered = run(ES_CSV, NQ_CSV)

    if not results.empty:
        total = len(results)
        days  = results['date'].nunique()
        print(f"Signals:                        {total}")
        print(f"Days covered:                   {days}")
        print(f"Avg/day:                        {total/days:.1f}")
        print(f"Rejected by timestamp filter:   {ts_filtered}")
        print()
        ref_cols = ['smt_time','direction','instrument','smt_variant',
                    'es_sw1_time','es_sw1_price','es_sw2_time','es_sw2_price',
                    'nq_sw1_time','nq_sw1_price','nq_sw2_time','nq_sw2_price',
                    'fvg_size','entry_50']
        print(results[ref_cols].to_string())
        out_path = '/mnt/user-data/outputs/smt_signals_v7.csv'
        results.to_csv(out_path, index=False)
        print(f"\nSaved -> {out_path}")
    else:
        print("No signals found.")
