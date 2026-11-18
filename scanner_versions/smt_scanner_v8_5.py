"""
SMT Setup Scanner v8.5
Changes from v8.4:
  - REWRITTEN 1m and 5m SMT detection using look-back algorithm:
    * sw2 = most recent confirmed swing
    * sw1 = first prior swing (up to 20 swings back) that sw2 wick-broke
    * SMT exists when one instrument confirms (wick-broke prior structure)
      and the other fails (sw2 doesn't break any prior swing)
  - sw2 must be fresh: within 120 mins on 1m, within 36 bars on 5m
  - This matches discretionary reading: "structure A broke, B didn't"
  - Old structural_pullback check removed (no longer meaningful with
    multi-swing look-back; staleness now does that work)
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

# [v8.5] New SMT detection: look-back window for finding sw1
PRIOR_SWING_LOOKBACK    = 20     # how many prior swings to walk back
SW2_STALENESS_MINS_1M   = 120    # sw2 must be within last 120 mins on 1m
SW2_STALENESS_BARS_5M   = 36     # sw2 must be within last 36 5m bars

# [v8.3] 15m macro bias settings
MACRO_SLOWING_THRESHOLD = 0.80
MACRO_MIN_LEGS          = 3
MACRO_LOOKBACK_BARS_15M = 144    # 36 hours

# [v8.4] 5m SMT confluence settings
SMT_5M_LOOKBACK_BARS    = 36     # window of 5m bars to consider
SMT_5M_TIME_GAP_BARS    = 1      # 1-bar leeway between sw2_ES and sw2_NQ on 5m
SMT_5M_MODE             = 'lenient'  # 'strict' = require agreeing 5m SMT
                                     # 'lenient' = block only on opposing 5m SMT

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


# ── [v8.3] RESAMPLE TO 15M AND [v8.4] 5M ─────────────────────────────────────
def resample_to_15m_and_5m(es_path, nq_path):
    """Build 15m and 5m OHLC bars for ES and NQ from the same 1m CSVs."""
    def load_one(path):
        df = pd.read_csv(path)
        df['time'] = pd.to_datetime(df['time'], utc=True)
        return df.sort_values('time').set_index('time')

    def resample(df, rule):
        ohlc = df[['open','high','low','close']].resample(
            rule, label='left', closed='left'
        ).agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
        ohlc['et'] = ohlc.index.tz_convert('America/New_York')
        return ohlc.reset_index()

    es = load_one(es_path)
    nq = load_one(nq_path)
    return resample(es, '15min'), resample(nq, '15min'), resample(es, '5min'), resample(nq, '5min')


# ── [v8.5] 5M SMT CONFLUENCE ENGINE (look-back wick-break) ───────────────────
def _detect_5m_swings_with_index(df):
    h = df['high'].values
    l = df['low'].values
    n = len(df)
    swing_highs = []
    swing_lows  = []
    for i in range(3, n):
        if h[i-1] > h[i-2] and h[i-1] > h[i-3] and h[i-1] > h[i]:
            swing_highs.append((df.iloc[i-1]['et'], float(h[i-1]), i-1))
        if l[i-1] < l[i-2] and l[i-1] < l[i-3] and l[i-1] < l[i]:
            swing_lows.append((df.iloc[i-1]['et'], float(l[i-1]), i-1))
    return swing_highs, swing_lows


def _bars_apart_5m(et_a, et_b):
    return abs((pd.Timestamp(et_a) - pd.Timestamp(et_b)).total_seconds()) / 60 / 5


def _walk_back_break_high_5m(swings, sw2_price, max_lookback=PRIOR_SWING_LOOKBACK):
    """5m version: walks backward through 5m swing highs."""
    if len(swings) < 2: return None
    prior = swings[:-1]
    window = prior[-max_lookback:]
    for s in reversed(window):
        _, p, _ = s
        if sw2_price > p:
            return s
    return None


def _walk_back_break_low_5m(swings, sw2_price, max_lookback=PRIOR_SWING_LOOKBACK):
    if len(swings) < 2: return None
    prior = swings[:-1]
    window = prior[-max_lookback:]
    for s in reversed(window):
        _, p, _ = s
        if sw2_price < p:
            return s
    return None


def check_5m_smt_confluence(es5, nq5, signal_et, trade_direction):
    """
    v8.5 5m SMT: take most recent confirmed 5m swing on each instrument,
    check sw2 staleness (within last 36 bars from signal_et) and timestamp
    alignment (within 1 bar). Then walk back through up to 20 prior 5m swings
    to find first wick break. If ES confirms and NQ fails (or vice versa) → SMT.
    """
    es_win = es5[es5['et'] < signal_et].tail(SMT_5M_LOOKBACK_BARS).reset_index(drop=True)
    nq_win = nq5[nq5['et'] < signal_et].tail(SMT_5M_LOOKBACK_BARS).reset_index(drop=True)

    if len(es_win) < 5 or len(nq_win) < 5:
        return {'status':'none','smt_time':None,'smt_prices':None,'detail':'not enough 5m bars'}

    es_sh, es_sl = _detect_5m_swings_with_index(es_win)
    nq_sh, nq_sl = _detect_5m_swings_with_index(nq_win)

    bullish_smt = None  # (et, prices description)
    bearish_smt = None

    # ── BEARISH 5m SMT (swing highs) ─────────────────────────────────────────
    if len(es_sh) >= 1 and len(nq_sh) >= 1:
        sw2_es = es_sh[-1]
        sw2_nq = nq_sh[-1]
        # Timestamp alignment within 1 bar
        if _bars_apart_5m(sw2_es[0], sw2_nq[0]) <= SMT_5M_TIME_GAP_BARS:
            sw1_es = _walk_back_break_high_5m(es_sh, sw2_es[1])
            sw1_nq = _walk_back_break_high_5m(nq_sh, sw2_nq[1])
            es_confirmed = sw1_es is not None
            nq_confirmed = sw1_nq is not None
            if es_confirmed and not nq_confirmed:
                bearish_smt = (sw2_es[0],
                    f'ES sw2:{sw2_es[1]}@{str(sw2_es[0])[11:16]} broke prior {sw1_es[1]} '
                    f'| NQ sw2:{sw2_nq[1]}@{str(sw2_nq[0])[11:16]} failed (no prior break in {PRIOR_SWING_LOOKBACK})')
            elif nq_confirmed and not es_confirmed:
                bearish_smt = (sw2_nq[0],
                    f'NQ sw2:{sw2_nq[1]}@{str(sw2_nq[0])[11:16]} broke prior {sw1_nq[1]} '
                    f'| ES sw2:{sw2_es[1]}@{str(sw2_es[0])[11:16]} failed')

    # ── BULLISH 5m SMT (swing lows) ──────────────────────────────────────────
    if len(es_sl) >= 1 and len(nq_sl) >= 1:
        sw2_es = es_sl[-1]
        sw2_nq = nq_sl[-1]
        if _bars_apart_5m(sw2_es[0], sw2_nq[0]) <= SMT_5M_TIME_GAP_BARS:
            sw1_es = _walk_back_break_low_5m(es_sl, sw2_es[1])
            sw1_nq = _walk_back_break_low_5m(nq_sl, sw2_nq[1])
            es_confirmed = sw1_es is not None
            nq_confirmed = sw1_nq is not None
            if es_confirmed and not nq_confirmed:
                bullish_smt = (sw2_es[0],
                    f'ES sw2:{sw2_es[1]}@{str(sw2_es[0])[11:16]} broke prior {sw1_es[1]} '
                    f'| NQ sw2:{sw2_nq[1]}@{str(sw2_nq[0])[11:16]} failed')
            elif nq_confirmed and not es_confirmed:
                bullish_smt = (sw2_nq[0],
                    f'NQ sw2:{sw2_nq[1]}@{str(sw2_nq[0])[11:16]} broke prior {sw1_nq[1]} '
                    f'| ES sw2:{sw2_es[1]}@{str(sw2_es[0])[11:16]} failed')

    if bullish_smt and bearish_smt:
        return {'status':'conflicted','smt_time':None,'smt_prices':None,
                'detail':'both bullish and bearish 5m SMT detected'}

    want_bullish = (trade_direction == 'LONG')
    if want_bullish:
        if bullish_smt:
            return {'status':'agree','smt_time':bullish_smt[0],'smt_prices':bullish_smt[1],
                    'detail':'bullish 5m SMT confirms LONG'}
        if bearish_smt:
            return {'status':'oppose','smt_time':None,'smt_prices':None,
                    'detail':'bearish 5m SMT opposes LONG'}
        return {'status':'none','smt_time':None,'smt_prices':None,'detail':'no 5m SMT'}
    else:
        if bearish_smt:
            return {'status':'agree','smt_time':bearish_smt[0],'smt_prices':bearish_smt[1],
                    'detail':'bearish 5m SMT confirms SHORT'}
        if bullish_smt:
            return {'status':'oppose','smt_time':None,'smt_prices':None,
                    'detail':'bullish 5m SMT opposes SHORT'}
        return {'status':'none','smt_time':None,'smt_prices':None,'detail':'no 5m SMT'}


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


# ── [v8.5] SWING HISTORY (FULL) ─────────────────────────────────────────────
def get_swing_history(flags, prices, indices, timestamps):
    """
    Returns a list of accumulated swing history at each bar:
      [[(price, idx, ts), ...], ...]
    Each element is all confirmed swings UP TO that bar (in chronological order).
    The most recent swing is the last element of each sublist.
    """
    out = []
    history = []
    for flag, price, idx, ts in zip(flags, prices, indices, timestamps):
        if flag == 1:
            history.append((float(price), int(idx), ts))
        out.append(list(history))   # snapshot
    return out


# ── [v8.5] WICK-BREAK WALK-BACK ──────────────────────────────────────────────
def find_wick_break_high(swing_history, sw2_price, max_lookback=PRIOR_SWING_LOOKBACK, signal_date=None):
    """
    Walk backwards through prior swings (excluding sw2 itself).
    Return the most recent prior swing where sw2_price > swing_price (wick break).
    If signal_date is provided, sw1 must be from the same calendar date.
    None if no break found within max_lookback swings.
    """
    if len(swing_history) < 2: return None
    prior = swing_history[:-1]      # exclude sw2 (last element)
    window = prior[-max_lookback:]
    for s in reversed(window):
        p, _, ts = s
        if signal_date is not None and hasattr(ts, 'date') and ts.date() != signal_date:
            continue  # skip swings from a different day
        if sw2_price > p:           # strict wick break
            return s
    return None


def find_wick_break_low(swing_history, sw2_price, max_lookback=PRIOR_SWING_LOOKBACK, signal_date=None):
    """Inverse: prior swing where sw2_price < swing_price."""
    if len(swing_history) < 2: return None
    prior = swing_history[:-1]
    window = prior[-max_lookback:]
    for s in reversed(window):
        p, _, ts = s
        if signal_date is not None and hasattr(ts, 'date') and ts.date() != signal_date:
            continue
        if sw2_price < p:
            return s
    return None


def find_failing_sw1(swing_history, signal_date=None):
    """
    For the FAILING instrument: find the most recent prior swing
    (the structural point sw2 was compared against but failed to break).
    Returns the second-to-last swing in history, filtered by session date.
    """
    if len(swing_history) < 2: return None
    prior = swing_history[:-1]
    for s in reversed(prior):
        _, _, ts = s
        if signal_date is not None and hasattr(ts, 'date') and ts.date() != signal_date:
            continue
        return s
    return None


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

    # [v8.3/v8.4] Build 15m and 5m bars once, reused for every macro/confluence check
    es15, nq15, es5, nq5 = resample_to_15m_and_5m(es_path, nq_path)

    sdf['sh_es_hist'] = get_swing_history(sdf['Swing High_es'], sdf['high_es'], sdf.index, sdf['et'])
    sdf['sl_es_hist'] = get_swing_history(sdf['Swing Low_es'],  sdf['low_es'],  sdf.index, sdf['et'])
    sdf['sh_nq_hist'] = get_swing_history(sdf['Swing High_nq'], sdf['high_nq'], sdf.index, sdf['et'])
    sdf['sl_nq_hist'] = get_swing_history(sdf['Swing Low_nq'],  sdf['low_nq'],  sdf.index, sdf['et'])

    signals = []
    ts_filtered     = 0
    mac_filtered    = 0
    stale_filtered  = 0
    smt5m_filtered  = 0

    for i, row in sdf.iterrows():
        if row['bar_type'] != 'session': continue

        sh_e_hist = row['sh_es_hist']
        sl_e_hist = row['sl_es_hist']
        sh_n_hist = row['sh_nq_hist']
        sl_n_hist = row['sl_nq_hist']
        candidates = []

        # ── [v8.5] LONG candidates (BULLISH SMT — sweep of lows) ──────────────
        if len(sl_e_hist) >= 1 and len(sl_n_hist) >= 1:
            sw2_es = sl_e_hist[-1]   # (price, idx, ts)
            sw2_nq = sl_n_hist[-1]
            t_e2 = sw2_es[2]; t_n2 = sw2_nq[2]
            sig_date = row['et'].date()

            # Staleness: sw2 must be within last 120 min of current bar
            sw2_es_age = (row['et'] - t_e2).total_seconds() / 60
            sw2_nq_age = (row['et'] - t_n2).total_seconds() / 60

            if sw2_es_age > SW2_STALENESS_MINS_1M or sw2_nq_age > SW2_STALENESS_MINS_1M:
                stale_filtered += 1
            elif timestamps_aligned(t_e2, t_n2):
                sw1_es = find_wick_break_low(sl_e_hist, sw2_es[0], signal_date=sig_date)
                sw1_nq = find_wick_break_low(sl_n_hist, sw2_nq[0], signal_date=sig_date)
                es_confirmed = sw1_es is not None
                nq_confirmed = sw1_nq is not None

                # SMT = exactly one instrument confirmed (broke down), the other failed
                if es_confirmed and not nq_confirmed:
                    # ES broke prior low, NQ failed to break → BULLISH SMT → LONG ES
                    nq_fail_sw1 = find_failing_sw1(sl_n_hist, signal_date=sig_date)
                    candidates.append({
                        'direction':'LONG','instrument':'ES',
                        'sweep_pts':round(sw1_es[0] - sw2_es[0], 2),
                        'fail_pts':round(abs(sw2_nq[0] - nq_fail_sw1[0]), 2) if nq_fail_sw1 else 0.0,
                        'smt_variant':'standard',
                        'es_sw1_time':fmt_ts(sw1_es[2]),'es_sw1_price':sw1_es[0],
                        'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':sw2_es[0],
                        'nq_sw1_time':fmt_ts(nq_fail_sw1[2]) if nq_fail_sw1 else fmt_ts(t_n2),
                        'nq_sw1_price':nq_fail_sw1[0] if nq_fail_sw1 else sw2_nq[0],
                        'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':sw2_nq[0],
                        'swept_extreme':sw2_es[0],
                    })
                elif nq_confirmed and not es_confirmed:
                    # NQ broke prior low, ES failed → BULLISH SMT → LONG NQ
                    es_fail_sw1 = find_failing_sw1(sl_e_hist, signal_date=sig_date)
                    candidates.append({
                        'direction':'LONG','instrument':'NQ',
                        'sweep_pts':round(sw1_nq[0] - sw2_nq[0], 2),
                        'fail_pts':round(abs(sw2_es[0] - es_fail_sw1[0]), 2) if es_fail_sw1 else 0.0,
                        'smt_variant':'standard',
                        'es_sw1_time':fmt_ts(es_fail_sw1[2]) if es_fail_sw1 else fmt_ts(t_e2),
                        'es_sw1_price':es_fail_sw1[0] if es_fail_sw1 else sw2_es[0],
                        'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':sw2_es[0],
                        'nq_sw1_time':fmt_ts(sw1_nq[2]),'nq_sw1_price':sw1_nq[0],
                        'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':sw2_nq[0],
                        'swept_extreme':sw2_nq[0],
                    })
            else:
                ts_filtered += 1

        # ── [v8.5] SHORT candidates (BEARISH SMT — sweep of highs) ────────────
        if len(sh_e_hist) >= 1 and len(sh_n_hist) >= 1:
            sw2_es = sh_e_hist[-1]
            sw2_nq = sh_n_hist[-1]
            t_e2 = sw2_es[2]; t_n2 = sw2_nq[2]
            sig_date = row['et'].date()

            sw2_es_age = (row['et'] - t_e2).total_seconds() / 60
            sw2_nq_age = (row['et'] - t_n2).total_seconds() / 60

            if sw2_es_age > SW2_STALENESS_MINS_1M or sw2_nq_age > SW2_STALENESS_MINS_1M:
                stale_filtered += 1
            elif timestamps_aligned(t_e2, t_n2):
                sw1_es = find_wick_break_high(sh_e_hist, sw2_es[0], signal_date=sig_date)
                sw1_nq = find_wick_break_high(sh_n_hist, sw2_nq[0], signal_date=sig_date)
                es_confirmed = sw1_es is not None
                nq_confirmed = sw1_nq is not None

                if es_confirmed and not nq_confirmed:
                    # ES broke prior high, NQ failed → BEARISH SMT → SHORT ES
                    nq_fail_sw1 = find_failing_sw1(sh_n_hist, signal_date=sig_date)
                    candidates.append({
                        'direction':'SHORT','instrument':'ES',
                        'sweep_pts':round(sw2_es[0] - sw1_es[0], 2),
                        'fail_pts':round(abs(sw2_nq[0] - nq_fail_sw1[0]), 2) if nq_fail_sw1 else 0.0,
                        'smt_variant':'standard',
                        'es_sw1_time':fmt_ts(sw1_es[2]),'es_sw1_price':sw1_es[0],
                        'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':sw2_es[0],
                        'nq_sw1_time':fmt_ts(nq_fail_sw1[2]) if nq_fail_sw1 else fmt_ts(t_n2),
                        'nq_sw1_price':nq_fail_sw1[0] if nq_fail_sw1 else sw2_nq[0],
                        'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':sw2_nq[0],
                        'swept_extreme':sw2_es[0],
                    })
                elif nq_confirmed and not es_confirmed:
                    # NQ broke prior high, ES failed → BEARISH SMT → SHORT NQ
                    es_fail_sw1 = find_failing_sw1(sh_e_hist, signal_date=sig_date)
                    candidates.append({
                        'direction':'SHORT','instrument':'NQ',
                        'sweep_pts':round(sw2_nq[0] - sw1_nq[0], 2),
                        'fail_pts':round(abs(sw2_es[0] - es_fail_sw1[0]), 2) if es_fail_sw1 else 0.0,
                        'smt_variant':'standard',
                        'es_sw1_time':fmt_ts(es_fail_sw1[2]) if es_fail_sw1 else fmt_ts(t_e2),
                        'es_sw1_price':es_fail_sw1[0] if es_fail_sw1 else sw2_es[0],
                        'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':sw2_es[0],
                        'nq_sw1_time':fmt_ts(sw1_nq[2]),'nq_sw1_price':sw1_nq[0],
                        'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':sw2_nq[0],
                        'swept_extreme':sw2_nq[0],
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

            # [v8.4] 5m SMT CONFLUENCE check
            smt5m = check_5m_smt_confluence(es5, nq5, row['et'], c['direction'])
            if SMT_5M_MODE == 'strict':
                # Strict mode: require agreeing 5m SMT
                if smt5m['status'] != 'agree':
                    smt5m_filtered += 1
                    continue
            else:
                # Lenient mode: block only on opposing or conflicted
                if smt5m['status'] in ('oppose','conflicted'):
                    smt5m_filtered += 1
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
                'entry_50':   fvg['entry_50'],
                'fvg_size':   fvg['fvg_size'],
                'entry_type': entry_type,
                # [v8.3] 15m macro bias state at the moment of the signal
                'es_15m_bias':       es_bias_15m,
                'nq_15m_bias':       nq_bias_15m,
                'combined_15m_bias': combined_bias_15m,
                'bias_detail':       bias_detail,
                # [v8.4] 5m SMT confluence context
                'smt5m_status':      smt5m['status'],
                'smt5m_time':        smt5m['smt_time'],
                'smt5m_prices':      smt5m['smt_prices'],
                # pre-existing FVG context (None if not SMT_IN_FVG)
                'pre_fvg_bar':  pre_fvg['pre_fvg_bar']  if pre_fvg else None,
                'pre_fvg_low':  pre_fvg['pre_fvg_low']  if pre_fvg else None,
                'pre_fvg_high': pre_fvg['pre_fvg_high'] if pre_fvg else None,
            }
            signals.append(signal)

    # ── POST-PROCESSING ───────────────────────────────────────────────────────
    out = pd.DataFrame(signals)
    if out.empty:
        print(f"No signals. TS filtered: {ts_filtered} | Macro filtered: {mac_filtered} | "
              f"Stale: {stale_filtered} | 5m SMT filtered: {smt5m_filtered}")
        return out, ts_filtered, mac_filtered, stale_filtered, smt5m_filtered

    out = out[out['fvg_found']==True].copy()

    # FVG size filter
    es_mask = (out['instrument']=='ES') & (out['fvg_size']>=ES_FVG_MIN) & (out['fvg_size']<=ES_FVG_MAX)
    nq_mask = (out['instrument']=='NQ') & (out['fvg_size']>=NQ_FVG_MIN) & (out['fvg_size']<=NQ_FVG_MAX)
    out = out[es_mask|nq_mask].copy()

    out = out.sort_values('smt_time').reset_index(drop=True)
    out['bucket'] = pd.to_datetime(out['smt_time']).dt.floor('30min')

    # [v8.2] Dedup: SMT_IN_FVG > FVG_AFTER_SMT
    type_order = {'SMT_IN_FVG': 0, 'FVG_AFTER_SMT': 1}
    out['_type_rank'] = out['entry_type'].map(type_order).fillna(2)
    out = out.sort_values(['smt_time','_type_rank'])
    out = out.drop_duplicates(subset=['date','bucket','direction','instrument'], keep='first')
    out = out.drop(columns=['_type_rank'])

    return out.reset_index(drop=True), ts_filtered, mac_filtered, stale_filtered, smt5m_filtered


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("SMT Scanner v8.5")
    print("="*60)
    print(f"SMT detection:    look-back wick-break ({PRIOR_SWING_LOOKBACK} prior swings)")
    print(f"sw2 staleness:    {SW2_STALENESS_MINS_1M} mins (1m) / {SW2_STALENESS_BARS_5M} bars (5m)")
    print(f"Macro filter:     15m structural bias (3-TF framework)")
    print(f"5m SMT mode:      {SMT_5M_MODE}")
    print(f"FVG selection:    earliest valid FVG after SMT")
    print(f"SMT_IN_FVG entry: limit at 50% of post-SMT FVG")
    print("="*60)

    results, ts, mac, stale, smt5m = run(ES_CSV, NQ_CSV)

    if not results.empty:
        total = len(results)
        days  = results['date'].nunique()
        print(f"\nSignals:           {total}")
        print(f"Days:              {days}")
        print(f"Avg/day:           {total/days:.1f}")
        print(f"TS filtered:       {ts}")
        print(f"Macro filtered:    {mac}")
        print(f"Stale filtered:    {stale}")
        print(f"5m SMT filtered:   {smt5m}")
        print(f"\nBy entry type:\n{results['entry_type'].value_counts().to_string()}")
        print(f"\nBy 15m bias:\n{results['combined_15m_bias'].value_counts().to_string()}")
        print(f"\nBy 5m SMT status:\n{results['smt5m_status'].value_counts().to_string()}")
        out_path = '/mnt/user-data/outputs/smt_signals_v8_5.csv'
        results.to_csv(out_path, index=False)
        print(f"\nSaved → {out_path}")
    else:
        print("No signals found.")
