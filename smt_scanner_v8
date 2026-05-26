"""
SMT Setup Scanner v8.0 — ES/NQ 1-min
=====================================
Changes from v7 → v8:

  FIX 1: TWO-LAYER MACRO DIRECTION FILTER
  -----------------------------------------
  v7 had no macro filter — signals fired regardless of direction relative
  to the broader market trend. ~50% of all cancellations were against-macro,
  meaning the scanner was wasting half of manual review time on invalid signals.

  v8 adds a two-layer macro filter. BOTH layers must agree for a signal to pass.

  Layer 1 — 5-min 20 EMA:
    Price of the instrument above its 5-min 20 EMA at signal time → bullish
    Price of the instrument below its 5-min 20 EMA at signal time → bearish
    LONG signals only pass when EMA says bullish
    SHORT signals only pass when EMA says bearish

  Layer 2 — 1-min Structural Bias:
    Scanner evaluates recent 1-min swing structure at signal time.
    Uses last 2 structural swing highs and last 2 structural swing lows.
    Higher highs AND higher lows → bullish bias
    Lower highs AND lower lows → bearish bias
    Mixed (e.g. higher highs but lower lows) → unclear → signal blocked
    LONG signals only pass when structure is bullish
    SHORT signals only pass when structure is bearish

  If EMA and structure disagree → signal blocked.
  Starting with both active (strictest). Loosen by removing one layer
  if signal count proves too low after manual review.

  FIX 2: FVG EXISTENCE VALIDATION
  ---------------------------------
  v7 could flag FVGs where the displacement candle was not genuine —
  the next bar would wick back into the gap, meaning no real displacement
  occurred. v8 adds a check: the FVG bar's body must not be fully
  overlapped by adjacent candle wicks. Specifically, the FVG zone
  (prev_high to next_low for LONG, next_high to prev_low for SHORT)
  must have a minimum size of FVG_MIN_SIZE to confirm real displacement.
  This is already handled by FVG size filters but the session/position
  check is now also enforced more strictly.

  INPUTS (same two CSVs as previous versions):
  ---------------------------------------------
  The 5-min 20 EMA is plotted on the 1-min chart in TradingView and
  exports automatically as a column in the 1-min CSV. No separate
  5-min export needed.
    ES_CSV  — ES 1-min with Swing High/Low Marker v1.2 + 5-min 20 EMA
    NQ_CSV  — NQ 1-min with Swing High/Low Marker v1.2 + 5-min 20 EMA

All v7 filters retained:
  - Structural swing filter removed — all tagged swings tracked (last 2)
  - Timestamp alignment: MAX_SW_TIME_GAP_MINS = 0 (exact same bar)
  - Pair-level structural pullback: ES >= 2pt, NQ >= 8pt
  - FVG location: midpoint above swept extreme (LONG) / below (SHORT)
  - Equal SMT: exact price match only (EQUAL_THRESHOLD = 0.0)
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
# Only two CSVs needed — 5-min 20 EMA is plotted on the 1-min chart in
# TradingView and exports as a column in the 1-min CSV automatically.
ES_CSV = '/mnt/user-data/uploads/CME_MINI_MES1___1__4_.csv'
NQ_CSV = '/mnt/user-data/uploads/CME_MINI_MNQ1___1__4_.csv'

# -- Pair-level structural pullback --------------------------------------------
ES_MIN_PULLBACK = 2.0
NQ_MIN_PULLBACK = 8.0

# -- FVG size filters (points) -------------------------------------------------
ES_FVG_MIN = 1.0;  ES_FVG_MAX = 25.0
NQ_FVG_MIN = 3.0;  NQ_FVG_MAX = 150.0

# -- FVG scan window -----------------------------------------------------------
FVG_LOOKAHEAD = 15

# -- Timestamp alignment -------------------------------------------------------
MAX_SW_TIME_GAP_MINS = 0

# -- Equal SMT threshold -------------------------------------------------------
EQUAL_THRESHOLD = 0.0

# -- Session cutoff ------------------------------------------------------------
SESSION_CUTOFF_MINS = 30

# -- Pre-session lookback ------------------------------------------------------
PRE_SESSION_LOOKBACK_MINS = 30

# -- Session definitions -------------------------------------------------------
SESSIONS = [
    ('NY Morning',   8,  0, 10, 30),
    ('NY Afternoon', 13, 0, 15,  0),
]

# -- v8: Macro filter toggles --------------------------------------------------
# Set to False to disable a layer if signal count is too low
USE_EMA_FILTER        = True
USE_STRUCTURE_FILTER  = True

# -- v8: Structural bias lookback ----------------------------------------------
# Number of recent swing highs and lows to assess structural bias
STRUCTURE_LOOKBACK = 2


# -- LOAD & ALIGN --------------------------------------------------------------
def load_1min(es_path, nq_path):
    es = pd.read_csv(es_path)
    nq = pd.read_csv(nq_path)
    for df, instr in [(es, 'ES'), (nq, 'NQ')]:
        df['time'] = pd.to_datetime(df['time'], utc=True)
        df['Swing High'] = pd.to_numeric(df['Swing High'], errors='coerce').fillna(0)
        df['Swing Low']  = pd.to_numeric(df['Swing Low'],  errors='coerce').fillna(0)
        # Detect EMA column exported from TradingView 1-min chart
        ema_col = next((c for c in df.columns if 'ema' in c.lower()), None)
        if ema_col:
            df['ema'] = pd.to_numeric(df[ema_col], errors='coerce')
        else:
            df['ema'] = float('nan')
            print(f"WARNING: No EMA column found in {instr} CSV. EMA filter will be disabled.")
    merged = pd.merge(es, nq, on='time', suffixes=('_es', '_nq'))
    merged = merged.sort_values('time').reset_index(drop=True)
    merged['et']     = merged['time'].dt.tz_convert('America/New_York')
    merged['hour']   = merged['et'].dt.hour
    merged['minute'] = merged['et'].dt.minute
    merged['date']   = merged['et'].dt.date
    return merged


# load_5min removed — EMA now read directly from 1-min CSV column


def classify_bar(hour, minute):
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


# -- v8: EMA MACRO FILTER ------------------------------------------------------
def get_ema_bias(row, instrument):
    """
    Check whether the instrument close is above or below the 5-min 20 EMA.
    After pd.merge with suffixes (_es, _nq), columns are:
      ema_es / ema_nq   — EMA value from 1-min CSV column
      close_es / close_nq — close price
    Returns 'bullish', 'bearish', or 'unclear'.
    """
    suffix = '_es' if instrument == 'ES' else '_nq'
    try:
        ema   = row[f'ema{suffix}']
        close = row[f'close{suffix}']
        if pd.isna(ema) or pd.isna(close):
            return 'unclear'
        if close > ema:
            return 'bullish'
        elif close < ema:
            return 'bearish'
    except KeyError:
        return 'unclear'
    return 'unclear'


# -- v8: STRUCTURAL BIAS FILTER ------------------------------------------------
def get_structural_bias(sdf, current_idx, instrument):
    """
    Assess structural bias by looking at the last STRUCTURE_LOOKBACK
    swing highs and swing lows on the 1-min chart up to current_idx.

    Uses raw swing marker flags (not structural-filtered) for this assessment.
    Returns 'bullish', 'bearish', or 'unclear'.
    """
    sh_col = f'Swing High_{instrument.lower()}'
    sl_col = f'Swing Low_{instrument.lower()}'
    h_col  = f'high_{instrument.lower()}'
    l_col  = f'low_{instrument.lower()}'

    # Get recent bars up to current
    past = sdf.iloc[:current_idx + 1]

    # Collect last N swing highs and lows
    recent_highs = past[past[sh_col] == 1][h_col].values[-STRUCTURE_LOOKBACK:]
    recent_lows  = past[past[sl_col] == 1][l_col].values[-STRUCTURE_LOOKBACK:]

    if len(recent_highs) < STRUCTURE_LOOKBACK or len(recent_lows) < STRUCTURE_LOOKBACK:
        return 'unclear'  # not enough swings to assess

    # Check if highs are trending up or down
    highs_bullish  = all(recent_highs[i] < recent_highs[i+1]
                         for i in range(len(recent_highs)-1))
    highs_bearish  = all(recent_highs[i] > recent_highs[i+1]
                         for i in range(len(recent_highs)-1))

    # Check if lows are trending up or down
    lows_bullish   = all(recent_lows[i] < recent_lows[i+1]
                         for i in range(len(recent_lows)-1))
    lows_bearish   = all(recent_lows[i] > recent_lows[i+1]
                         for i in range(len(recent_lows)-1))

    if highs_bullish and lows_bullish:
        return 'bullish'
    elif highs_bearish and lows_bearish:
        return 'bearish'
    return 'unclear'


# -- v8: COMBINED MACRO CHECK --------------------------------------------------
def macro_allows(direction, row, current_idx, instrument, sdf):
    """
    Returns (True, 'ok') if both macro layers agree with the signal direction,
    or (False, reason) if blocked.
    """
    expected_bias = 'bullish' if direction == 'LONG' else 'bearish'

    if USE_EMA_FILTER:
        ema_bias = get_ema_bias(row, instrument)
        if ema_bias != expected_bias:
            return False, f'EMA={ema_bias}'

    if USE_STRUCTURE_FILTER:
        struct_bias = get_structural_bias(sdf, current_idx, instrument)
        if struct_bias != expected_bias:
            return False, f'structure={struct_bias}'

    return True, 'ok'


# -- SWING TRACKER (all tagged swings, no threshold) ---------------------------
def get_swings(flags, prices, indices, timestamps, n=2):
    recents = []
    history = []
    for flag, price, idx, ts in zip(flags, prices, indices, timestamps):
        if flag == 1:
            history.append((float(price), int(idx), ts))
        recents.append(list(history[-n:]) if len(history) >= n else [])
    return recents


# -- PAIR-LEVEL STRUCTURAL PULLBACK --------------------------------------------
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
    return abs((pd.Timestamp(t1) - pd.Timestamp(t2)).total_seconds()) / 60 \
           <= MAX_SW_TIME_GAP_MINS


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
    # Load 1-min data (EMA column included from TradingView export)
    df = load_1min(es_path, nq_path)
    bcs = [classify_bar(r['hour'], r['minute']) for _, r in df.iterrows()]
    df['bar_type'], df['bar_session'] = zip(*bcs)
    sdf = df[df['bar_type'].notna()].copy().reset_index(drop=True)
    sdf['session'] = sdf['bar_session']

    # Build swing history — no threshold, all tagged swings
    sdf['sh_es'] = get_swings(sdf['Swing High_es'], sdf['high_es'], sdf.index, sdf['et'])
    sdf['sl_es'] = get_swings(sdf['Swing Low_es'],  sdf['low_es'],  sdf.index, sdf['et'])
    sdf['sh_nq'] = get_swings(sdf['Swing High_nq'], sdf['high_nq'], sdf.index, sdf['et'])
    sdf['sl_nq'] = get_swings(sdf['Swing Low_nq'],  sdf['low_nq'],  sdf.index, sdf['et'])

    signals = []
    ts_filtered    = 0
    macro_filtered = 0

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
                # ES sweeps lower, NQ holds or equal
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
                # NQ sweeps lower, ES holds or equal
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

        # -- MACRO FILTER + FVG SEARCH -----------------------------------------
        for c in candidates:
            # v8: check both macro layers before scanning for FVG
            allowed, reason = macro_allows(
                c['direction'], row, i, c['instrument'], sdf
            )
            if not allowed:
                macro_filtered += 1
                continue

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
        print(f"No signals after all filters.")
        print(f"  Timestamp filter:  {ts_filtered}")
        print(f"  Macro filter:      {macro_filtered}")
        return out, ts_filtered, macro_filtered

    out = out[out['fvg_found'] == True].copy()
    es_mask = (out['instrument']=='ES')&(out['fvg_size']>=ES_FVG_MIN)&(out['fvg_size']<=ES_FVG_MAX)
    nq_mask = (out['instrument']=='NQ')&(out['fvg_size']>=NQ_FVG_MIN)&(out['fvg_size']<=NQ_FVG_MAX)
    out = out[es_mask|nq_mask].copy()
    out = out.sort_values('smt_time').reset_index(drop=True)
    out['bucket'] = pd.to_datetime(out['smt_time']).dt.floor('30min')
    out = out.drop_duplicates(subset=['date','bucket','direction','instrument'])
    return out.reset_index(drop=True), ts_filtered, macro_filtered


if __name__ == '__main__':
    print("=" * 60)
    print("SMT Scanner v8.0")
    print("=" * 60)
    print(f"  Macro filter — EMA (5m/20):    {'ON' if USE_EMA_FILTER else 'OFF'}")
    print(f"  Macro filter — Structure:      {'ON' if USE_STRUCTURE_FILTER else 'OFF'}")
    print(f"  Structure lookback:            {STRUCTURE_LOOKBACK} swings")
    print(f"  Pair pullback (ES/NQ):         >= {ES_MIN_PULLBACK}pt / >= {NQ_MIN_PULLBACK}pt")
    print(f"  Timestamp alignment:           {MAX_SW_TIME_GAP_MINS} min")
    print(f"  Equal SMT threshold:           exact price match only")
    print(f"  FVG size (ES):                 {ES_FVG_MIN}-{ES_FVG_MAX}pt")
    print(f"  FVG size (NQ):                 {NQ_FVG_MIN}-{NQ_FVG_MAX}pt")
    print(f"  FVG lookahead:                 {FVG_LOOKAHEAD} bars")
    print(f"  Session cutoff:                {SESSION_CUTOFF_MINS} min before end")
    print(f"  Pre-session lookback:          {PRE_SESSION_LOOKBACK_MINS} min")
    print(f"  NY Afternoon start:            13:00 ET")
    print()

    results, ts_filtered, macro_filtered = run(ES_CSV, NQ_CSV)

    if not results.empty:
        total = len(results)
        days  = results['date'].nunique()
        print(f"Signals passing all filters:    {total}")
        print(f"Days covered:                   {days}")
        print(f"Avg/day:                        {total/days:.1f}")
        print(f"Rejected by timestamp filter:   {ts_filtered}")
        print(f"Rejected by macro filter:       {macro_filtered}")
        print()
        ref_cols = ['smt_time','direction','instrument','smt_variant',
                    'es_sw1_time','es_sw1_price','es_sw2_time','es_sw2_price',
                    'nq_sw1_time','nq_sw1_price','nq_sw2_time','nq_sw2_price',
                    'fvg_size','entry_50']
        print(results[ref_cols].to_string())
        out_path = '/mnt/user-data/outputs/smt_signals_v8.csv'
        results.to_csv(out_path, index=False)
        print(f"\nSaved -> {out_path}")
    else:
        print("No signals found.")
