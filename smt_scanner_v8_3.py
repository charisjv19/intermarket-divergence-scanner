"""
SMT Setup Scanner v8.3
Changes from v8.2:
  - Replaced session open bias macro filter with 15m structural macro bias.
  - Macro bias now uses the SAME 3-timeframe framework logic as the
    macro_bias_diagnostic.py and the TradingView Pine indicator.
  - Bias is computed per-signal (evolves through the session as new
    15m bars close), not just at session open.
  - LONG signals allowed only if combined 15m bias = BULLISH or BULLISH (SLOWING)
  - SHORT signals allowed only if combined 15m bias = BEARISH or BEARISH (SLOWING)
  - Blocked: TRANSITIONAL, CONFLICTED, UNCLEAR
  - The full macro bias state is recorded in the output CSV.
"""

import pandas as pd
import numpy as np

ES_CSV = '/mnt/user-data/uploads/CME_MINI_MES1___1__7_.csv'
NQ_CSV = '/mnt/user-data/uploads/CME_MINI_MNQ1___1__7_.csv'

ES_MIN_PULLBACK         = 2.0
NQ_MIN_PULLBACK         = 8.0
ES_FVG_MIN              = 1.0
ES_FVG_MAX              = 25.0
NQ_FVG_MIN              = 3.0
NQ_FVG_MAX              = 150.0
FVG_LOOKAHEAD           = 15
PRE_FVG_LOOKBACK        = 30
MAX_SW_TIME_GAP_MINS    = 0
SESSION_CUTOFF_MINS     = 30
PRE_SESSION_LOOKBACK_MINS = 30
STALENESS_LIMIT_MINS    = 60     # [v8.2] sw1 must be within 60 mins of sw2

# [v8.3] 15m macro bias settings — mirrors macro_bias_diagnostic.py
MACRO_SLOWING_THRESHOLD = 0.80   # leg < 80% of previous = slowing
MACRO_MIN_LEGS          = 3      # need ≥3 legs to assess momentum
MACRO_LOOKBACK_BARS_15M = 144    # 144 × 15m = 36 hours

SESSIONS = [('NY Morning',8,0,10,30),('NY Afternoon',13,0,15,0)]


# ── LOAD ──────────────────────────────────────────────────────────────────────
def load(es_path, nq_path):
    es = pd.read_csv(es_path)
    nq = pd.read_csv(nq_path)
    for df in [es, nq]:
        df['time']       = pd.to_datetime(df['time'], utc=True)
        df['Swing High'] = pd.to_numeric(df['Swing High'], errors='coerce').fillna(0)
        df['Swing Low']  = pd.to_numeric(df['Swing Low'],  errors='coerce').fillna(0)
    merged = pd.merge(es, nq, on='time', suffixes=('_es','_nq'))
    merged = merged.sort_values('time').reset_index(drop=True)
    merged['et']     = merged['time'].dt.tz_convert('America/New_York')
    merged['hour']   = merged['et'].dt.hour
    merged['minute'] = merged['et'].dt.minute
    merged['date']   = merged['et'].dt.date
    return merged


# ── [v8.3] RESAMPLE TO 15M ────────────────────────────────────────────────────
def resample_to_15m(es_path, nq_path):
    """Build 15m OHLC bars for ES and NQ from the same 1m CSVs."""
    def load_one(path):
        df = pd.read_csv(path)
        df['time'] = pd.to_datetime(df['time'], utc=True)
        return df.sort_values('time').set_index('time')

    def resample(df):
        ohlc = df[['open','high','low','close']].resample(
            '15min', label='left', closed='left'
        ).agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
        ohlc['et'] = ohlc.index.tz_convert('America/New_York')
        return ohlc.reset_index()

    return resample(load_one(es_path)), resample(load_one(nq_path))


# ── SESSION HELPERS ───────────────────────────────────────────────────────────
def classify_bar(hour, minute):
    t = hour*60+minute
    for name,sh,sm,eh,em in SESSIONS:
        ss=sh*60+sm; se=eh*60+em
        ps=ss-PRE_SESSION_LOOKBACK_MINS; ce=se-SESSION_CUTOFF_MINS
        if ss<=t<ce: return 'session', name
        if ps<=t<ss: return 'pre_session', name
    return None, None

def fvg_in_session(et):
    t = et.hour*60+et.minute
    for name,sh,sm,eh,em in SESSIONS:
        if sh*60+sm<=t<eh*60+em:
            return True
    return False


# ── [v8.3] 15m MACRO BIAS ENGINE ─────────────────────────────────────────────
# Mirrors macro_bias_diagnostic.py and the TradingView Pine indicator.
# Reads only 15m bars closed BEFORE the signal timestamp — no lookahead.

def _detect_15m_swings(df):
    """4-bar swing logic: high[1] > high[0,2,3] and low[1] < low[0,2,3]."""
    h = df['high'].values
    l = df['low'].values
    n = len(df)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    for i in range(3, n):
        if h[i-1] > h[i-2] and h[i-1] > h[i-3] and h[i-1] > h[i]:
            sh[i-1] = h[i-1]
        if l[i-1] < l[i-2] and l[i-1] < l[i-3] and l[i-1] < l[i]:
            sl[i-1] = l[i-1]
    df = df.copy()
    df['swing_high'] = sh
    df['swing_low']  = sl
    return df


def _classify_structure(swing_highs, swing_lows):
    """
    swing_highs / swing_lows are lists of (timestamp, price) tuples.
    Returns (structure, momentum, detail).
      structure: 'bullish' | 'bearish' | 'transitional' | 'insufficient_data'
      momentum:  'strong' | 'slowing' | 'insufficient_data' | 'n/a'
    """
    if len(swing_highs) < 2 and len(swing_lows) < 2:
        return 'insufficient_data', 'insufficient_data', 'too few swings'

    sh_p = [p for _, p in swing_highs[-4:]]
    sl_p = [p for _, p in swing_lows[-4:]]

    hh = sum(1 for i in range(1, len(sh_p)) if sh_p[i] > sh_p[i-1])
    lh = sum(1 for i in range(1, len(sh_p)) if sh_p[i] < sh_p[i-1])
    hl = sum(1 for i in range(1, len(sl_p)) if sl_p[i] > sl_p[i-1])
    ll = sum(1 for i in range(1, len(sl_p)) if sl_p[i] < sl_p[i-1])

    bullish_score = hh + hl
    bearish_score = lh + ll

    if bullish_score > bearish_score and hh > 0 and hl > 0:
        structure = 'bullish'
    elif bearish_score > bullish_score and lh > 0 and ll > 0:
        structure = 'bearish'
    else:
        structure = 'transitional'

    detail = f'HH:{hh} LH:{lh} HL:{hl} LL:{ll}'

    if structure == 'transitional':
        return structure, 'n/a', detail

    # build impulse legs and compare last two
    legs = []
    if structure == 'bullish' and len(swing_highs) >= MACRO_MIN_LEGS:
        sh_list = swing_highs[-(MACRO_MIN_LEGS+1):]
        for i in range(1, len(sh_list)):
            if sh_list[i][1] > sh_list[i-1][1]:
                legs.append(sh_list[i][1] - sh_list[i-1][1])
    elif structure == 'bearish' and len(swing_lows) >= MACRO_MIN_LEGS:
        sl_list = swing_lows[-(MACRO_MIN_LEGS+1):]
        for i in range(1, len(sl_list)):
            if sl_list[i][1] < sl_list[i-1][1]:
                legs.append(sl_list[i-1][1] - sl_list[i][1])

    if len(legs) < 2:
        return structure, 'insufficient_data', detail + f' legs:{[round(x,2) for x in legs]}'

    ratio = legs[-1] / legs[-2] if legs[-2] > 0 else 1.0
    momentum = 'strong' if ratio >= MACRO_SLOWING_THRESHOLD else 'slowing'
    return structure, momentum, detail + f' legs:{[round(x,2) for x in legs]} ratio:{round(ratio*100,1)}%'


def _bias_label(structure, momentum):
    if structure == 'insufficient_data': return 'UNCLEAR'
    if structure == 'transitional':      return 'TRANSITIONAL'
    if structure == 'bullish':
        return 'STRONGLY BULLISH' if momentum == 'strong' else \
               'BULLISH SLOWING'  if momentum == 'slowing' else 'BULLISH'
    if structure == 'bearish':
        return 'STRONGLY BEARISH' if momentum == 'strong' else \
               'BEARISH SLOWING'  if momentum == 'slowing' else 'BEARISH'
    return 'UNCLEAR'


def get_15m_macro_bias(es15, nq15, signal_et):
    """
    Returns (es_bias, nq_bias, combined_bias, detail).
    Reads only 15m bars closed strictly BEFORE signal_et.
    Combined bias requires both ES and NQ to agree directionally.
    """
    es_win = es15[es15['et'] < signal_et].tail(MACRO_LOOKBACK_BARS_15M)
    nq_win = nq15[nq15['et'] < signal_et].tail(MACRO_LOOKBACK_BARS_15M)

    if es_win.empty or nq_win.empty:
        return 'UNCLEAR', 'UNCLEAR', 'UNCLEAR', 'no data'

    es_sw = _detect_15m_swings(es_win.reset_index(drop=True))
    nq_sw = _detect_15m_swings(nq_win.reset_index(drop=True))

    es_h = [(r['et'], r['swing_high']) for _, r in es_sw.iterrows() if not np.isnan(r['swing_high'])]
    es_l = [(r['et'], r['swing_low'])  for _, r in es_sw.iterrows() if not np.isnan(r['swing_low'])]
    nq_h = [(r['et'], r['swing_high']) for _, r in nq_sw.iterrows() if not np.isnan(r['swing_high'])]
    nq_l = [(r['et'], r['swing_low'])  for _, r in nq_sw.iterrows() if not np.isnan(r['swing_low'])]

    es_struct, es_mom, es_detail = _classify_structure(es_h, es_l)
    nq_struct, nq_mom, nq_detail = _classify_structure(nq_h, nq_l)

    es_bias = _bias_label(es_struct, es_mom)
    nq_bias = _bias_label(nq_struct, nq_mom)

    es_dir = 'bullish' if 'BULLISH' in es_bias else 'bearish' if 'BEARISH' in es_bias else 'unclear'
    nq_dir = 'bullish' if 'BULLISH' in nq_bias else 'bearish' if 'BEARISH' in nq_bias else 'unclear'

    if es_dir == nq_dir and es_dir != 'unclear':
        combined = es_dir.upper()
        if 'SLOWING' in es_bias or 'SLOWING' in nq_bias:
            combined += ' (SLOWING)'
    elif es_dir == 'unclear' and nq_dir == 'unclear':
        combined = 'UNCLEAR'
    else:
        combined = 'CONFLICTED'

    return es_bias, nq_bias, combined, f'ES: {es_detail} || NQ: {nq_detail}'


# ── SWING HISTORY ─────────────────────────────────────────────────────────────
def get_swings(flags, prices, indices, timestamps, n=2):
    recents = []
    history = []
    for flag, price, idx, ts in zip(flags, prices, indices, timestamps):
        if flag == 1:
            history.append((float(price), int(idx), ts))
        recents.append(list(history[-n:]) if len(history) >= n else [])
    return recents


# ── STRUCTURAL PULLBACK ───────────────────────────────────────────────────────
def structural_pullback(sdf, i1, i2, swing_type, instrument, min_pb):
    start, end = min(i1,i2), max(i1,i2)
    if end <= start+1: return False
    segment = sdf.iloc[start:end+1]
    if swing_type == 'high':
        ref = max(sdf.iloc[i1][f'high_{instrument}'], sdf.iloc[i2][f'high_{instrument}'])
        return (ref - segment[f'low_{instrument}'].min()) >= min_pb
    else:
        ref = min(sdf.iloc[i1][f'low_{instrument}'], sdf.iloc[i2][f'low_{instrument}'])
        return (segment[f'high_{instrument}'].max() - ref) >= min_pb


# ── TIMESTAMP ALIGNMENT ───────────────────────────────────────────────────────
def timestamps_aligned(t1, t2):
    return abs((pd.Timestamp(t1)-pd.Timestamp(t2)).total_seconds())/60 <= MAX_SW_TIME_GAP_MINS


# ── [v8.2] STALENESS CHECK ────────────────────────────────────────────────────
def sw1_is_fresh(t1, t2):
    """
    sw1 must be within STALENESS_LIMIT_MINS of sw2.
    Prevents the scanner from using a swing point from an hour ago
    as the reference for a current SMT.
    Only applied to 1m SMT detection.
    """
    gap_mins = abs((pd.Timestamp(t2)-pd.Timestamp(t1)).total_seconds()) / 60
    return gap_mins <= STALENESS_LIMIT_MINS


# ── [v8.2] FIND PRE-EXISTING FVG (SMT_IN_FVG) → now returns limit at 50% ─────
def find_preexisting_fvg(sdf, smt_idx, direction, instrument, lookback, current_price):
    """
    Looks back for a pre-existing FVG that the current price is inside.
    v8.2: entry_type is SMT_IN_FVG but entry is a LIMIT at 50% of a
    post-SMT FVG (handled in main loop), not a market entry.
    This function still identifies the pre-existing FVG for context/tagging,
    but the actual entry FVG is found by find_fvg() after the SMT.
    """
    col_h = f'high_{instrument.lower()}'
    col_l = f'low_{instrument.lower()}'
    start = max(0, smt_idx-lookback)
    fvg_min = ES_FVG_MIN if instrument=='ES' else NQ_FVG_MIN
    fvg_max = ES_FVG_MAX if instrument=='ES' else NQ_FVG_MAX

    # search backwards — we want the most recent pre-existing FVG
    for j in range(smt_idx-1, start, -1):
        if j-1 < 0 or j+1 >= len(sdf): continue
        ph = sdf.iloc[j-1][col_h]; pl = sdf.iloc[j-1][col_l]
        nh = sdf.iloc[j+1][col_h]; nl = sdf.iloc[j+1][col_l]

        if direction == 'LONG' and ph < nl:
            lo, hi = ph, nl
            if lo <= current_price <= hi:
                size = round(hi-lo, 2)
                if fvg_min <= size <= fvg_max:
                    return dict(
                        pre_fvg_found=True,
                        pre_fvg_bar=sdf.iloc[j]['et'],
                        pre_fvg_low=lo,
                        pre_fvg_high=hi,
                        pre_fvg_size=size,
                    )

        if direction == 'SHORT' and pl > nh:
            lo, hi = nh, pl
            if lo <= current_price <= hi:
                size = round(hi-lo, 2)
                if fvg_min <= size <= fvg_max:
                    return dict(
                        pre_fvg_found=True,
                        pre_fvg_bar=sdf.iloc[j]['et'],
                        pre_fvg_low=lo,
                        pre_fvg_high=hi,
                        pre_fvg_size=size,
                    )
    return None


# ── [v8.2] FIND FVG → earliest valid FVG closest to SMT bar ──────────────────
def find_fvg(sdf, start_idx, direction, instrument, lookahead, swept_extreme):
    """
    v8.2: Finds the EARLIEST valid FVG after the SMT bar (closest in time),
    not the first one that passes size filters.
    This prevents the scanner from skipping a valid early FVG in favor of
    a later one that happens to appear first in the iteration.
    All valid FVGs are collected, then we return the earliest one.
    """
    col_h = f'high_{instrument.lower()}'
    col_l = f'low_{instrument.lower()}'
    end = min(start_idx+lookahead, len(sdf)-2)
    fvg_min = ES_FVG_MIN if instrument=='ES' else NQ_FVG_MIN
    fvg_max = ES_FVG_MAX if instrument=='ES' else NQ_FVG_MAX

    candidates = []

    for j in range(start_idx+1, end+1):
        ph = sdf.iloc[j-1][col_h]; pl = sdf.iloc[j-1][col_l]
        nh = sdf.iloc[j+1][col_h]; nl = sdf.iloc[j+1][col_l]

        if direction == 'LONG' and ph < nl:
            lo, hi = ph, nl
            mid = (lo+hi)/2
            size = round(hi-lo, 2)
            if (mid > swept_extreme and
                fvg_in_session(sdf.iloc[j]['et']) and
                fvg_min <= size <= fvg_max):
                candidates.append(dict(
                    fvg_found=True,
                    fvg_bar=sdf.iloc[j]['et'],
                    fvg_low=lo,
                    fvg_high=hi,
                    entry_50=round(mid, 2),
                    fvg_size=size,
                    entry_type='FVG_AFTER_SMT',
                ))

        if direction == 'SHORT' and pl > nh:
            lo, hi = nh, pl
            mid = (lo+hi)/2
            size = round(hi-lo, 2)
            if (mid < swept_extreme and
                fvg_in_session(sdf.iloc[j]['et']) and
                fvg_min <= size <= fvg_max):
                candidates.append(dict(
                    fvg_found=True,
                    fvg_bar=sdf.iloc[j]['et'],
                    fvg_low=lo,
                    fvg_high=hi,
                    entry_50=round(mid, 2),
                    fvg_size=size,
                    entry_type='FVG_AFTER_SMT',
                ))

    # return earliest (closest to SMT bar) — candidates are already in time order
    if candidates:
        return candidates[0]
    return dict(fvg_found=False, entry_type=None)


# ── FORMATTING ────────────────────────────────────────────────────────────────
def fmt_ts(ts):
    try:    return str(ts)[:16].replace('T',' ')
    except: return str(ts)[:16]


# ── MAIN SCANNER LOOP ─────────────────────────────────────────────────────────
def run(es_path, nq_path):
    df  = load(es_path, nq_path)
    bcs = [classify_bar(r['hour'], r['minute']) for _, r in df.iterrows()]
    df['bar_type'], df['bar_session'] = zip(*bcs)
    sdf = df[df['bar_type'].notna()].copy().reset_index(drop=True)
    sdf['session'] = sdf['bar_session']
    df_full = df.copy()

    # [v8.3] Build 15m bars once, reused for every signal's macro bias check
    es15, nq15 = resample_to_15m(es_path, nq_path)

    sdf['sh_es'] = get_swings(sdf['Swing High_es'], sdf['high_es'], sdf.index, sdf['et'])
    sdf['sl_es'] = get_swings(sdf['Swing Low_es'],  sdf['low_es'],  sdf.index, sdf['et'])
    sdf['sh_nq'] = get_swings(sdf['Swing High_nq'], sdf['high_nq'], sdf.index, sdf['et'])
    sdf['sl_nq'] = get_swings(sdf['Swing Low_nq'],  sdf['low_nq'],  sdf.index, sdf['et'])

    signals = []
    ts_filtered    = 0
    mac_filtered   = 0
    stale_filtered = 0

    for i, row in sdf.iterrows():
        if row['bar_type'] != 'session': continue

        sh_e, sl_e = row['sh_es'], row['sl_es']
        sh_n, sl_n = row['sh_nq'], row['sl_nq']
        candidates = []

        # ── LONG candidates (sweep of lows) ──────────────────────────────────
        if len(sl_e)==2 and len(sl_n)==2:
            (p_e1,i_e1,t_e1),(p_e2,i_e2,t_e2) = sl_e
            (p_n1,i_n1,t_n1),(p_n2,i_n2,t_n2) = sl_n

            if timestamps_aligned(t_e1,t_n1) and timestamps_aligned(t_e2,t_n2):

                # [v8.2] staleness check on sw1 — how far back is sw1 from sw2?
                if not sw1_is_fresh(t_e1, t_e2):
                    stale_filtered += 1
                elif p_e2<p_e1 and (p_n2>p_n1 or p_n2==p_n1):
                    if (structural_pullback(sdf,i_e1,i_e2,'low','es',ES_MIN_PULLBACK) and
                        structural_pullback(sdf,i_n1,i_n2,'low','nq',NQ_MIN_PULLBACK)):
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
                elif not sw1_is_fresh(t_n1, t_n2):
                    stale_filtered += 1
                elif p_n2<p_n1 and (p_e2>p_e1 or p_e2==p_e1):
                    if (structural_pullback(sdf,i_e1,i_e2,'low','es',ES_MIN_PULLBACK) and
                        structural_pullback(sdf,i_n1,i_n2,'low','nq',NQ_MIN_PULLBACK)):
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

        # ── SHORT candidates (sweep of highs) ─────────────────────────────────
        if len(sh_e)==2 and len(sh_n)==2:
            (p_e1,i_e1,t_e1),(p_e2,i_e2,t_e2) = sh_e
            (p_n1,i_n1,t_n1),(p_n2,i_n2,t_n2) = sh_n

            if timestamps_aligned(t_e1,t_n1) and timestamps_aligned(t_e2,t_n2):

                # [v8.2] staleness check on sw1
                if not sw1_is_fresh(t_e1, t_e2):
                    stale_filtered += 1
                elif p_e2>p_e1 and (p_n2<p_n1 or p_n2==p_n1):
                    if (structural_pullback(sdf,i_e1,i_e2,'high','es',ES_MIN_PULLBACK) and
                        structural_pullback(sdf,i_n1,i_n2,'high','nq',NQ_MIN_PULLBACK)):
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
                elif not sw1_is_fresh(t_n1, t_n2):
                    stale_filtered += 1
                elif p_n2>p_n1 and (p_e2<p_e1 or p_e2==p_e1):
                    if (structural_pullback(sdf,i_e1,i_e2,'high','es',ES_MIN_PULLBACK) and
                        structural_pullback(sdf,i_n1,i_n2,'high','nq',NQ_MIN_PULLBACK)):
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

        # ── [v8.3] 15M MACRO FILTER + FVG SEARCH ──────────────────────────────
        # Compute macro bias ONCE per bar (same for all candidates on this bar)
        es_bias_15m, nq_bias_15m, combined_bias_15m, bias_detail = (
            (None, None, None, None) if not candidates
            else get_15m_macro_bias(es15, nq15, row['et'])
        )

        for c in candidates:
            # [v8.3] Block unless combined bias agrees with trade direction
            # Allowed: BULLISH / BULLISH (SLOWING) for LONG
            # Allowed: BEARISH / BEARISH (SLOWING) for SHORT
            # Blocked: TRANSITIONAL, CONFLICTED, UNCLEAR
            expected_dir = 'BULLISH' if c['direction']=='LONG' else 'BEARISH'
            if expected_dir not in combined_bias_15m:
                mac_filtered += 1
                continue

            current_price = row[f"close_{c['instrument'].lower()}"]

            # [v8.2] Check for pre-existing FVG (SMT_IN_FVG context tag)
            # Then always look for a post-SMT FVG for the actual limit entry
            pre_fvg = find_preexisting_fvg(
                sdf, i, c['direction'], c['instrument'],
                PRE_FVG_LOOKBACK, current_price
            )

            # Always find the post-SMT FVG for the limit entry (v8.2: earliest one)
            fvg = find_fvg(
                sdf, i, c['direction'], c['instrument'],
                FVG_LOOKAHEAD, c['swept_extreme']
            )

            if not fvg.get('fvg_found'):
                continue  # no post-SMT FVG = no entry, no exceptions

            # Determine signal type
            if pre_fvg:
                entry_type = 'SMT_IN_FVG'
            else:
                entry_type = 'FVG_AFTER_SMT'

            signal = {
                'smt_time':   row['et'],
                'date':       row['date'],
                'session':    row['session'],
                'es_close':   row['close_es'],
                'nq_close':   row['close_nq'],
                **{k:v for k,v in c.items() if k != 'swept_extreme'},
                'fvg_found':  fvg['fvg_found'],
                'fvg_bar':    fvg['fvg_bar'],
                'fvg_low':    fvg['fvg_low'],
                'fvg_high':   fvg['fvg_high'],
                'entry_50':   fvg['entry_50'],   # [v8.2] always limit at 50% of post-SMT FVG
                'fvg_size':   fvg['fvg_size'],
                'entry_type': entry_type,
                # [v8.3] 15m macro bias state at the moment of the signal
                'es_15m_bias':       es_bias_15m,
                'nq_15m_bias':       nq_bias_15m,
                'combined_15m_bias': combined_bias_15m,
                'bias_detail':       bias_detail,
                # pre-existing FVG context (None if not SMT_IN_FVG)
                'pre_fvg_bar':  pre_fvg['pre_fvg_bar']  if pre_fvg else None,
                'pre_fvg_low':  pre_fvg['pre_fvg_low']  if pre_fvg else None,
                'pre_fvg_high': pre_fvg['pre_fvg_high'] if pre_fvg else None,
            }
            signals.append(signal)

    # ── POST-PROCESSING ───────────────────────────────────────────────────────
    out = pd.DataFrame(signals)
    if out.empty:
        print(f"No signals. TS filtered: {ts_filtered} | Macro filtered: {mac_filtered} | Stale filtered: {stale_filtered}")
        return out, ts_filtered, mac_filtered, stale_filtered

    out = out[out['fvg_found']==True].copy()

    # FVG size filter
    es_mask = (out['instrument']=='ES') & (out['fvg_size']>=ES_FVG_MIN) & (out['fvg_size']<=ES_FVG_MAX)
    nq_mask = (out['instrument']=='NQ') & (out['fvg_size']>=NQ_FVG_MIN) & (out['fvg_size']<=NQ_FVG_MAX)
    out = out[es_mask|nq_mask].copy()

    out = out.sort_values('smt_time').reset_index(drop=True)
    out['bucket'] = pd.to_datetime(out['smt_time']).dt.floor('30min')

    # [v8.2] Dedup: SMT_IN_FVG takes priority over FVG_AFTER_SMT in same bucket
    # Sort so SMT_IN_FVG comes first, then drop duplicates keeping first
    type_order = {'SMT_IN_FVG': 0, 'FVG_AFTER_SMT': 1}
    out['_type_rank'] = out['entry_type'].map(type_order).fillna(2)
    out = out.sort_values(['smt_time','_type_rank'])
    out = out.drop_duplicates(subset=['date','bucket','direction','instrument'], keep='first')
    out = out.drop(columns=['_type_rank'])

    return out.reset_index(drop=True), ts_filtered, mac_filtered, stale_filtered


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("SMT Scanner v8.3")
    print("="*60)
    print(f"Macro filter:    15m structural bias (3-TF framework)")
    print(f"Staleness limit: {STALENESS_LIMIT_MINS} mins")
    print(f"FVG selection:   earliest valid FVG after SMT")
    print(f"SMT_IN_FVG:      limit at 50% of post-SMT FVG")
    print("="*60)

    results, ts, mac, stale = run(ES_CSV, NQ_CSV)

    if not results.empty:
        total = len(results)
        days  = results['date'].nunique()
        print(f"\nSignals:        {total}")
        print(f"Days:           {days}")
        print(f"Avg/day:        {total/days:.1f}")
        print(f"TS filtered:    {ts}")
        print(f"Macro filtered: {mac}")
        print(f"Stale filtered: {stale}")
        print(f"\nBy entry type:\n{results['entry_type'].value_counts().to_string()}")
        print(f"\nBy 15m bias:\n{results['combined_15m_bias'].value_counts().to_string()}")
        out_path = '/mnt/user-data/outputs/smt_signals_v8_3.csv'
        results.to_csv(out_path, index=False)
        print(f"\nSaved → {out_path}")
    else:
        print("No signals found.")
