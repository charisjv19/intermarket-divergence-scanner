"""
SMT Setup Scanner v8.8  (audit Sep 2026 — SMT_FVG_Strategy_Audit_Sep2026)
Base: v8.7. Logic is unchanged except for the audit fixes noted below.

v8.8 scope is audit defects S1, S2, S3, S11 ONLY. No threshold changes
(ES_FVG_MIN / NQ_FVG_MIN or any other threshold) — threshold review is
deferred until after the May–June out-of-sample test.

Fixes applied in this file:
  - [S1] Session-aware sw1 walk-back. detect_smt_v86() now restricts the
    same-day swing pool to swings in the SAME trading session as the signal,
    so an afternoon signal can no longer pull its sw1 from the morning session
    of the same calendar day. Previously the pool was scoped by calendar date
    only.
  - [S3] Exact sw1 parallel match. SW1_PARALLEL_TOL_MINS changed from 2 to 0,
    so the failing-instrument sw1 must occur on the SAME bar as the
    confirming-instrument sw1 (zero tolerance), matching the sw2 alignment
    (MAX_SW_TIME_GAP_MINS = 0) and the structural-correctness invariant.
  - [S2] Entry / stop / target protocol. SMT_IN_FVG now takes a MARKET entry at
    the close of the sw2 confirmation bar (sw2 bar + 1), decoupled from any
    post-SMT FVG. Pre-FVG membership is checked at the confirmation-bar
    close (sw2 + 1), not the outer-loop bar. smt_time is that same
    confirmation-bar timestamp, not the later scan bar i. SMT_IN_FVG
    confirmation must be exactly 1 minute after sw2_conf_time (elapsed
    timestamp). Any other gap is a session boundary: reject SMT_IN_FVG
    outright, no FVG_AFTER_SMT fallback. FVG_AFTER_SMT is not gated by
    that confirmation rule; its clock is the FVG-formation bar.
    find_fvg 3-bar windows (j-1, j, j+1) must be consecutive 1-minute
    bars; a session-gap triplet is rejected, not used as a 50% entry.
    fvg_in_session() uses the same SESSION_CUTOFF_MINS window as
    classify_bar() for FVG_AFTER_SMT / find_fvg. SMT_IN_FVG pre-FVG
    membership also accepts a pre-session-formed FVG (07:30–08:00 /
    12:30–13:00). combined_15m_bias and smt5m_status are evaluated
    as of the confirmation clock (sw2+1 min for SMT_IN_FVG; FVG bar
    for FVG_AFTER_SMT), not the outer scan bar i.
    ES/NQ 1m bars are inner-joined on timestamp; a minute missing on
    either side is dropped from both, never filled. 5m/15m resample
    uses that same intersection.
    SL is just outside that FVG
    (LONG: pre_fvg_low - buffer / SHORT: pre_fvg_high + buffer), then
    floored at the 20-tick (ES) / 40-tick (NQ) minimum. TP = 1.5R.
    FVG_AFTER_SMT keeps its 50% limit entry but now also emits an SL (beyond the
    most recent swing whose timestamp is strictly before the FVG bar, looked up
    on the FVG-bar swing-history snapshot — not the outer scan bar i) and
    TP = 3R.
    New output columns: entry_price, stop_loss, take_profit, target_R.

Pending (not yet implemented in this file): S11 (emit smt5m_prices).

Inherited from v8.7:
  1. Dedup by sw2_time (not 30-min bucket).
  2. FVG search starts from sw2_time + 1 (not signal_time + 1).
  3. ES_FVG_MIN lowered to 0.5pt (was 1.0pt) per Feb 25 review.
"""

import pandas as pd
import numpy as np

ES_CSV = '/mnt/user-data/uploads/CME_MINI_MES1___1__7_.csv'
NQ_CSV = '/mnt/user-data/uploads/CME_MINI_MNQ1___1__7_.csv'

ES_MIN_PULLBACK         = 2.0
NQ_MIN_PULLBACK         = 8.0
ES_FVG_MIN              = 0.5    # [v8.7] lowered from 1.0
ES_FVG_MAX              = 25.0
NQ_FVG_MIN              = 3.0
NQ_FVG_MAX              = 150.0
FVG_LOOKAHEAD           = 15
PRE_FVG_LOOKBACK        = 30
MAX_SW_TIME_GAP_MINS    = 0
SESSION_CUTOFF_MINS     = 30
PRE_SESSION_LOOKBACK_MINS = 30

# [v8.5] sw1 lookback window
PRIOR_SWING_LOOKBACK    = 20
SW2_STALENESS_MINS_1M   = 120
SW2_STALENESS_BARS_5M   = 36
# sdf keeps session + pre_session bars only. Consecutive sdf indices are
# not always consecutive 1-minute bars: NY Afternoon session ends at 14:29
# (15:00 minus SESSION_CUTOFF_MINS), and the next sdf row is next day's
# 07:30 pre_session. SMT_IN_FVG confirmation is the true next 1-minute
# bar after sw2 — not idx+1 with a gap ceiling. FVG_AFTER_SMT does not
# use this gate.
SMT_IN_FVG_CONFIRM_MINS = 1.0
MAX_CONF_GAP_MINS       = SMT_IN_FVG_CONFIRM_MINS  # alias: exact 1 minute, not a 5-min ceiling

# [v8.6] Multi-sw2 candidate consideration
SW2_CANDIDATES          = 5      # last N swings to consider as sw2
SW1_PARALLEL_TOL_MINS   = 0      # [v8.8 / S3] exact match only — no minute tolerance

# [v8.8 / S2] Entry, stop-loss and take-profit protocol
ES_TICK_SIZE            = 0.25   # index points per tick (MES/ES)
NQ_TICK_SIZE            = 0.25   # index points per tick (MNQ/NQ)
# SMT_IN_FVG: MARKET entry at the sw2 confirmation-bar close; SL just outside
# the pre-existing FVG that close sits in, then floored at the shared min-stop.
SMT_IN_FVG_TP_R         = 1.5    # take-profit multiple for SMT_IN_FVG (per audit)
ES_SMT_IN_FVG_SL_BUFFER = 1.0    # points beyond the FVG edge (ES)
NQ_SMT_IN_FVG_SL_BUFFER = 3.0    # points beyond the FVG edge (NQ)
# FVG_AFTER_SMT: LIMIT entry at 50% of the post-SMT FVG; SL beyond the swing that
# preceded the FVG, floored at a minimum stop size; TP at a fixed R multiple.
FVG_AFTER_SMT_TP_R      = 3.0    # take-profit multiple for FVG_AFTER_SMT
ES_FVG_SL_BUFFER        = 2.0    # points beyond the prior swing (ES)
NQ_FVG_SL_BUFFER        = 8.0    # points beyond the prior swing (NQ)
# Shared minimum stop — applied to BOTH SMT_IN_FVG and FVG_AFTER_SMT.
ES_MIN_SL_TICKS         = 20     # minimum SL distance (ES) = 20 * 0.25 = 5.0 pts
NQ_MIN_SL_TICKS         = 40     # minimum SL distance (NQ) = 40 * 0.25 = 10.0 pts

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
def exclusive_bar_times(es_times, nq_times):
    """Timestamps present on only one instrument. Empty pair = aligned."""
    es_i = pd.DatetimeIndex(pd.to_datetime(es_times, utc=True)).unique()
    nq_i = pd.DatetimeIndex(pd.to_datetime(nq_times, utc=True)).unique()
    return es_i.difference(nq_i), nq_i.difference(es_i)


def merge_es_nq_1m(es, nq):
    """Strict inner join on UTC bar time. Missing minutes are dropped, never filled."""
    merged = pd.merge(es, nq, on='time', how='inner', suffixes=('_es', '_nq'))
    merged = merged.sort_values('time').reset_index(drop=True)
    if merged[['close_es', 'close_nq']].isna().any().any():
        raise ValueError(
            'ES/NQ merge produced NaN closes; a missing-bar minute must be '
            'dropped, not filled or assumed'
        )
    return merged


def load(es_path, nq_path):
    es = pd.read_csv(es_path)
    nq = pd.read_csv(nq_path)
    for df in [es, nq]:
        df['time']       = pd.to_datetime(df['time'], utc=True)
        df['Swing High'] = pd.to_numeric(df['Swing High'], errors='coerce').fillna(0)
        df['Swing Low']  = pd.to_numeric(df['Swing Low'],  errors='coerce').fillna(0)
    merged = merge_es_nq_1m(es, nq)
    merged['et']     = merged['time'].dt.tz_convert('America/New_York')
    merged['hour']   = merged['et'].dt.hour
    merged['minute'] = merged['et'].dt.minute
    merged['date']   = merged['et'].dt.date
    return merged


# ── [v8.3] RESAMPLE TO 15M AND [v8.4] 5M ─────────────────────────────────────
def resample_to_15m_and_5m(es_path, nq_path):
    """Build 15m and 5m OHLC bars for ES and NQ from the same 1m CSVs.

    Resample only minutes present in BOTH instruments (same inner-join
    rule as load()). A 1m hole is not filled before aggregation.
    """
    def load_one(path):
        df = pd.read_csv(path)
        df['time'] = pd.to_datetime(df['time'], utc=True)
        return df.sort_values('time').drop_duplicates(subset='time').set_index('time')

    def resample(df, rule):
        ohlc = df[['open','high','low','close']].resample(
            rule, label='left', closed='left'
        ).agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
        ohlc['et'] = ohlc.index.tz_convert('America/New_York')
        return ohlc.reset_index()

    es = load_one(es_path)
    nq = load_one(nq_path)
    shared = es.index.intersection(nq.index)
    es = es.loc[shared]
    nq = nq.loc[shared]
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
    """True iff this bar is a classify_bar() session bar (cutoff applied)."""
    return classify_bar(et.hour, et.minute)[0] == 'session'


def fvg_in_session_or_pre_session(et):
    """True iff this bar is in sdf: session or pre_session.

    SMT_IN_FVG pre-FVG membership allows a gap that formed in
    pre-session (07:30–08:00 / 12:30–13:00). FVG_AFTER_SMT / find_fvg
    still require fvg_in_session() (session only).
    """
    return classify_bar(et.hour, et.minute)[0] in ('session', 'pre_session')


def fvg_window_is_contiguous(sdf, j):
    """
    True iff sdf bars j-1, j, j+1 are consecutive 1-minute clocks.
    sdf is session+pre_session only, so consecutive indices can jump
    14:29 → next 07:30. A 3-bar FVG must not span that gap.
    """
    if j < 1 or j + 1 >= len(sdf):
        return False
    t0 = pd.Timestamp(sdf.iloc[j - 1]['et'])
    t1 = pd.Timestamp(sdf.iloc[j]['et'])
    t2 = pd.Timestamp(sdf.iloc[j + 1]['et'])
    g01 = (t1 - t0).total_seconds() / 60.0
    g12 = (t2 - t1).total_seconds() / 60.0
    return g01 == 1.0 and g12 == 1.0


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
def prior_swing_before_timestamp(swing_hist, fvg_time):
    """Most recent swing whose timestamp is strictly before fvg_time.

    Used for FVG_AFTER_SMT SL. Compare timestamps, not positional sdf
    indices from a later scan-bar snapshot.
    """
    fvg_ts = pd.Timestamp(fvg_time)
    for s in reversed(swing_hist):
        if pd.Timestamp(s[2]) < fvg_ts:
            return s
    return None


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


# ── [v8.6] PARALLEL SWING FINDER (optimized) ─────────────────────────────────
def find_parallel_swing(swing_history, target_ts, tol_mins, sig_date):
    """
    Find swing closest in time to target_ts within tol_mins, same calendar date.
    """
    if not swing_history: return None
    try:
        target_sec = target_ts.timestamp() if hasattr(target_ts, 'timestamp') else pd.Timestamp(target_ts).timestamp()
    except Exception:
        target_sec = pd.Timestamp(target_ts).timestamp()
    tol_sec = tol_mins * 60
    best = None
    best_diff = None
    for s in reversed(swing_history):
        _, _, ts = s
        if hasattr(ts, 'date') and ts.date() != sig_date:
            break
        try:
            ts_sec = ts.timestamp() if hasattr(ts, 'timestamp') else pd.Timestamp(ts).timestamp()
        except Exception:
            ts_sec = pd.Timestamp(ts).timestamp()
        diff = abs(ts_sec - target_sec)
        if diff <= tol_sec:
            if best_diff is None or diff < best_diff:
                best = s
                best_diff = diff
        elif best is not None and diff > tol_sec + 1800:  # past window
            break
    return best


# ── [v8.6] SMT DETECTION ENGINE ──────────────────────────────────────────────
def detect_smt_v86(conf_hist, fail_hist, current_et, direction):
    """
    v8.6 algorithm: multi-sw2 candidates + parallel sw1 lookup.
    """
    if len(conf_hist) < 2 or not fail_hist:
        return None

    sig_date = current_et.date()
    sig_session = classify_bar(current_et.hour, current_et.minute)[1]

    # Build same-SESSION conf history by walking backwards (much faster than list comp).
    # [v8.8 S1] Restrict the walk-back to the same trading session as the signal, not
    # just the same calendar date. Without this, an afternoon signal can pull its sw1
    # from the morning session of the same day (audit Sep 2026, defect S1). Swings from
    # a different session on the same date are skipped; the date change still stops the
    # walk-back entirely.
    same_day_conf = []
    for s in reversed(conf_hist):
        if s[2].date() != sig_date:
            break
        if classify_bar(s[2].hour, s[2].minute)[1] != sig_session:
            continue
        same_day_conf.append(s)
    same_day_conf.reverse()  # back to chronological order

    if not same_day_conf:
        return None
    candidates = same_day_conf[-SW2_CANDIDATES:]

    for sw2_idx in range(len(candidates)-1, -1, -1):   # newest first
        sw2_conf = candidates[sw2_idx]
        p2c, _, t2c = sw2_conf
        age = (current_et - t2c).total_seconds() / 60
        if age > SW2_STALENESS_MINS_1M:
            continue

        # Parallel sw2 on failing side (exact-minute alignment)
        sw2_fail = find_parallel_swing(fail_hist, t2c, MAX_SW_TIME_GAP_MINS, sig_date)
        if not sw2_fail:
            continue
        p2f, _, t2f = sw2_fail

        # Position of sw2 in same_day_conf — use the actual position, not .index()
        sw2_pos_in_sameday = len(same_day_conf) - len(candidates) + sw2_idx
        prior_conf = same_day_conf[max(0, sw2_pos_in_sameday - PRIOR_SWING_LOOKBACK):sw2_pos_in_sameday]

        # Find all prior_conf swings that sw2_conf wick-broke
        broken_sw1s = []
        for prior in prior_conf:
            p1c, _, t1c = prior
            if direction == 'SHORT' and p2c > p1c:
                broken_sw1s.append(prior)
            elif direction == 'LONG' and p2c < p1c:
                broken_sw1s.append(prior)

        if not broken_sw1s:
            continue

        # For each broken sw1, find parallel on failing side
        failed_parallels = []
        for sw1c in broken_sw1s:
            p1c, _, t1c = sw1c
            sw1f = find_parallel_swing(fail_hist, t1c, SW1_PARALLEL_TOL_MINS, sig_date)
            if not sw1f:
                continue
            # sw1f must be a DIFFERENT swing than sw2f (can't be the same point)
            if sw1f[2] == sw2_fail[2]:
                continue
            p1f, _, t1f = sw1f
            if direction == 'SHORT':
                fail_broke = p2f > p1f
            else:
                fail_broke = p2f < p1f
            if not fail_broke:
                failed_parallels.append({'conf_sw1': sw1c, 'fail_sw1': sw1f})

        if not failed_parallels:
            continue

        # Primary = OLDEST failed parallel
        failed_parallels.sort(key=lambda x: x['conf_sw1'][2])
        primary = failed_parallels[0]
        alt_times = [pd.Timestamp(fp['conf_sw1'][2]).strftime('%H:%M')
                     for fp in failed_parallels[1:]]

        return {
            'sw2_conf': sw2_conf,
            'sw2_fail': sw2_fail,
            'sw1_conf': primary['conf_sw1'],
            'sw1_fail': primary['fail_sw1'],
            'confirmations_count': len(failed_parallels),
            'alt_sw1_times': ', '.join(alt_times) if alt_times else '',
        }

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


def confirmation_gap_minutes(sw2_time, conf_time):
    """Minutes from the swing-flag bar to the sdf row used as confirmation."""
    return (pd.Timestamp(conf_time) - pd.Timestamp(sw2_time)).total_seconds() / 60.0


def confirmation_bar_is_contiguous(sw2_time, conf_time, max_gap_mins=None):
    """True iff confirmation is exactly 1 minute after sw2 (SMT_IN_FVG only).

    max_gap_mins is accepted for call-site compatibility but ignored: a 2–5
    minute hole is not the true next bar.
    """
    gap = confirmation_gap_minutes(sw2_time, conf_time)
    return abs(gap - SMT_IN_FVG_CONFIRM_MINS) < 1e-9


def check_sw2_confirmation_gap(signals, max_gap_mins=None):
    """
    Piece 3 / regression: every SMT_IN_FVG row's smt_time must be exactly
    1 minute after sw2_conf_time. FVG_AFTER_SMT is not on this clock.
    Empty list = pass.
    """
    if signals is None or getattr(signals, 'empty', True):
        return []
    missing = [c for c in ('smt_time', 'sw2_conf_time') if c not in signals.columns]
    if missing:
        return [f"signal CSV missing {missing}; cannot check confirmation gap"]
    rows = signals
    if 'entry_type' in signals.columns:
        rows = signals[signals['entry_type'] == 'SMT_IN_FVG']
        if rows.empty:
            return []
    smt = pd.to_datetime(rows['smt_time'], utc=True)
    sw2 = pd.to_datetime(rows['sw2_conf_time'], utc=True)
    gap = (smt - sw2).dt.total_seconds() / 60.0
    bad = (gap - SMT_IN_FVG_CONFIRM_MINS).abs() > 1e-9
    n = int(bad.sum())
    if not n:
        return []
    sample = rows.loc[bad, ['sw2_conf_time', 'smt_time']].head(3)
    sample_txt = '; '.join(
        f"sw2={r.sw2_conf_time} smt_time={r.smt_time}"
        for r in sample.itertuples(index=False)
    )
    return [
        f"{n} SMT_IN_FVG signals do not confirm exactly "
        f"{SMT_IN_FVG_CONFIRM_MINS:g} min after sw2: {sample_txt}"
    ]


def check_fvg_windows_contiguous(signals, sdf):
    """
    Piece 3 / regression: every FVG_AFTER_SMT fvg_bar's 3-bar window on sdf
    must be consecutive 1-minute bars. Empty list = pass.
    """
    if signals is None or getattr(signals, 'empty', True):
        return []
    if 'entry_type' not in signals.columns or 'fvg_bar' not in signals.columns:
        return ["signal CSV missing entry_type/fvg_bar; cannot check FVG window"]
    fvg_rows = signals[signals['entry_type'] == 'FVG_AFTER_SMT']
    if fvg_rows.empty:
        return []
    sdf_et = pd.to_datetime(sdf['et'], utc=True)
    fails = []
    n_bad = 0
    sample = []
    for r in fvg_rows.itertuples(index=False):
        fvg_et = pd.to_datetime(r.fvg_bar, utc=True)
        hits = sdf.index[sdf_et == fvg_et]
        if len(hits) == 0:
            n_bad += 1
            if len(sample) < 3:
                sample.append(f"fvg_bar={r.fvg_bar} not in sdf")
            continue
        j = int(hits[0])
        if not fvg_window_is_contiguous(sdf, j):
            n_bad += 1
            if len(sample) < 3:
                sample.append(f"fvg_bar={r.fvg_bar}")
    if not n_bad:
        return []
    return [
        f"{n_bad} FVG_AFTER_SMT rows have a 3-bar window that is not "
        f"consecutive 1-minute bars: {'; '.join(sample)}"
    ]


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


def min_stop_points(instrument):
    """Minimum stop distance in index points (20 ticks ES / 40 ticks NQ)."""
    if instrument == 'ES':
        return ES_MIN_SL_TICKS * ES_TICK_SIZE
    return NQ_MIN_SL_TICKS * NQ_TICK_SIZE


def apply_min_stop(entry_price, stop_loss, direction, min_sl):
    """Push the stop further from entry if risk is below the tick floor."""
    risk = abs(entry_price - stop_loss)
    if risk < min_sl:
        if direction == 'LONG':
            stop_loss = round(entry_price - min_sl, 2)
        else:
            stop_loss = round(entry_price + min_sl, 2)
        risk = min_sl
    return stop_loss, risk


def compute_smt_in_fvg_stop(entry_price, pre_fvg_low, pre_fvg_high, direction,
                            sl_buf, min_sl):
    """
    SMT_IN_FVG stop. Unconditionally just outside the pre-existing FVG:
      LONG:  pre_fvg_low  - buffer
      SHORT: pre_fvg_high + buffer
    then floor at min_sl.

    min()/max() selection between the FVG edge and entry is not needed:
    entry_price is always inside [pre_fvg_low, pre_fvg_high] by construction,
    because the pre-FVG membership check is anchored to the confirmation bar
    (sw2_conf_idx + 1) and uses that bar's close.
    """
    if direction == 'LONG':
        stop_loss = round(pre_fvg_low - sl_buf, 2)
    else:
        stop_loss = round(pre_fvg_high + sl_buf, 2)
    return apply_min_stop(entry_price, stop_loss, direction, min_sl)


# ── [v8.2] FIND PRE-EXISTING FVG (SMT_IN_FVG) → now returns limit at 50% ─────
def find_preexisting_fvg(sdf, smt_idx, direction, instrument, lookback, current_price):
    """
    Looks back from smt_idx for a pre-existing FVG that current_price is inside.
    Callers must pass the sw2 confirmation bar (sw2_conf_idx + 1) as smt_idx
    and that bar's close as current_price, so membership matches the market entry.
    The 3-bar window must be consecutive 1-minute bars (same rule as find_fvg).
    The FVG middle bar may be session or pre_session — a pre-session-formed
    gap is a valid SMT_IN_FVG membership match. Do not use fvg_in_session()
    here (that cutoff is for FVG_AFTER_SMT formation only).
    """
    col_h = f'high_{instrument.lower()}'
    col_l = f'low_{instrument.lower()}'
    start = max(0, smt_idx-lookback)
    fvg_min = ES_FVG_MIN if instrument=='ES' else NQ_FVG_MIN
    fvg_max = ES_FVG_MAX if instrument=='ES' else NQ_FVG_MAX

    # search backwards — we want the most recent pre-existing FVG
    for j in range(smt_idx-1, start, -1):
        if j-1 < 0 or j+1 >= len(sdf): continue
        if not fvg_window_is_contiguous(sdf, j):
            continue
        if not fvg_in_session_or_pre_session(sdf.iloc[j]['et']):
            continue
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
    Each 3-bar window (j-1, j, j+1) must be consecutive 1-minute bars;
    a session-gap triplet is rejected entirely.
    """
    col_h = f'high_{instrument.lower()}'
    col_l = f'low_{instrument.lower()}'
    end = min(start_idx+lookahead, len(sdf)-2)
    fvg_min = ES_FVG_MIN if instrument=='ES' else NQ_FVG_MIN
    fvg_max = ES_FVG_MAX if instrument=='ES' else NQ_FVG_MAX

    candidates = []

    for j in range(start_idx+1, end+1):
        if not fvg_window_is_contiguous(sdf, j):
            continue
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
                    fvg_bar_idx=j,
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
                    fvg_bar_idx=j,
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

    # [v8.6] Cache: only re-run SMT detection when swing history changes
    _last_sh_e_len = -1; _last_sl_e_len = -1
    _last_sh_n_len = -1; _last_sl_n_len = -1
    _last_smt_long_es = None
    _last_smt_long_nq = None
    _last_smt_short_es = None
    _last_smt_short_nq = None

    # [v8.6] Cache: macro bias only changes every 15 mins
    _macro_cache = {}   # 15min-floor → (es_bias, nq_bias, combined, detail)
    _smt5m_cache = {}   # (5min-floor, direction) → smt5m_result

    for i, row in sdf.iterrows():
        if row['bar_type'] != 'session': continue

        sh_e_hist = row['sh_es_hist']
        sl_e_hist = row['sl_es_hist']
        sh_n_hist = row['sh_nq_hist']
        sl_n_hist = row['sl_nq_hist']
        candidates = []

        # Detect history changes
        long_hist_changed  = (len(sl_e_hist) != _last_sl_e_len) or (len(sl_n_hist) != _last_sl_n_len)
        short_hist_changed = (len(sh_e_hist) != _last_sh_e_len) or (len(sh_n_hist) != _last_sh_n_len)

        # ── [v8.6] LONG candidates (BULLISH SMT — swing lows) ─────────────────
        if long_hist_changed:
            _last_smt_long_es = detect_smt_v86(sl_e_hist, sl_n_hist, row['et'], 'LONG')
            _last_smt_long_nq = None if _last_smt_long_es else detect_smt_v86(sl_n_hist, sl_e_hist, row['et'], 'LONG')
        smt = _last_smt_long_es
        if smt:
            sw1c = smt['sw1_conf']; sw2c = smt['sw2_conf']
            sw1f = smt['sw1_fail']; sw2f = smt['sw2_fail']
            candidates.append({
                'direction':'LONG','instrument':'ES',
                'sweep_pts':round(sw1c[0] - sw2c[0], 2),
                'fail_pts':round(abs(sw2f[0] - sw1f[0]), 2),
                'smt_variant':'standard',
                'es_sw1_time':fmt_ts(sw1c[2]),'es_sw1_price':sw1c[0],
                'es_sw2_time':fmt_ts(sw2c[2]),'es_sw2_price':sw2c[0],
                'nq_sw1_time':fmt_ts(sw1f[2]),'nq_sw1_price':sw1f[0],
                'nq_sw2_time':fmt_ts(sw2f[2]),'nq_sw2_price':sw2f[0],
                'confirmations_count':smt['confirmations_count'],
                'alt_sw1_times':smt['alt_sw1_times'],
                'swept_extreme':sw2c[0],
                'sw2_conf_idx':sw2c[1],         # [v8.7] sw2 sdf row index
                'sw2_conf_time':sw2c[2],        # [v8.7] sw2 timestamp for dedup
            })
        else:
            smt = _last_smt_long_nq
            if smt:
                sw1c = smt['sw1_conf']; sw2c = smt['sw2_conf']
                sw1f = smt['sw1_fail']; sw2f = smt['sw2_fail']
                candidates.append({
                    'direction':'LONG','instrument':'NQ',
                    'sweep_pts':round(sw1c[0] - sw2c[0], 2),
                    'fail_pts':round(abs(sw2f[0] - sw1f[0]), 2),
                    'smt_variant':'standard',
                    'es_sw1_time':fmt_ts(sw1f[2]),'es_sw1_price':sw1f[0],
                    'es_sw2_time':fmt_ts(sw2f[2]),'es_sw2_price':sw2f[0],
                    'nq_sw1_time':fmt_ts(sw1c[2]),'nq_sw1_price':sw1c[0],
                    'nq_sw2_time':fmt_ts(sw2c[2]),'nq_sw2_price':sw2c[0],
                    'confirmations_count':smt['confirmations_count'],
                    'alt_sw1_times':smt['alt_sw1_times'],
                    'swept_extreme':sw2c[0],
                    'sw2_conf_idx':sw2c[1],
                    'sw2_conf_time':sw2c[2],
                })

        # ── [v8.6] SHORT candidates (BEARISH SMT — swing highs) ───────────────
        if short_hist_changed:
            _last_smt_short_es = detect_smt_v86(sh_e_hist, sh_n_hist, row['et'], 'SHORT')
            _last_smt_short_nq = None if _last_smt_short_es else detect_smt_v86(sh_n_hist, sh_e_hist, row['et'], 'SHORT')
        smt = _last_smt_short_es
        if smt:
            sw1c = smt['sw1_conf']; sw2c = smt['sw2_conf']
            sw1f = smt['sw1_fail']; sw2f = smt['sw2_fail']
            candidates.append({
                'direction':'SHORT','instrument':'ES',
                'sweep_pts':round(sw2c[0] - sw1c[0], 2),
                'fail_pts':round(abs(sw2f[0] - sw1f[0]), 2),
                'smt_variant':'standard',
                'es_sw1_time':fmt_ts(sw1c[2]),'es_sw1_price':sw1c[0],
                'es_sw2_time':fmt_ts(sw2c[2]),'es_sw2_price':sw2c[0],
                'nq_sw1_time':fmt_ts(sw1f[2]),'nq_sw1_price':sw1f[0],
                'nq_sw2_time':fmt_ts(sw2f[2]),'nq_sw2_price':sw2f[0],
                'confirmations_count':smt['confirmations_count'],
                'alt_sw1_times':smt['alt_sw1_times'],
                'swept_extreme':sw2c[0],
                'sw2_conf_idx':sw2c[1],
                'sw2_conf_time':sw2c[2],
            })
        else:
            smt = _last_smt_short_nq
            if smt:
                sw1c = smt['sw1_conf']; sw2c = smt['sw2_conf']
                sw1f = smt['sw1_fail']; sw2f = smt['sw2_fail']
                candidates.append({
                    'direction':'SHORT','instrument':'NQ',
                    'sweep_pts':round(sw2c[0] - sw1c[0], 2),
                    'fail_pts':round(abs(sw2f[0] - sw1f[0]), 2),
                    'smt_variant':'standard',
                    'es_sw1_time':fmt_ts(sw1f[2]),'es_sw1_price':sw1f[0],
                    'es_sw2_time':fmt_ts(sw2f[2]),'es_sw2_price':sw2f[0],
                    'nq_sw1_time':fmt_ts(sw1c[2]),'nq_sw1_price':sw1c[0],
                    'nq_sw2_time':fmt_ts(sw2c[2]),'nq_sw2_price':sw2c[0],
                    'confirmations_count':smt['confirmations_count'],
                    'alt_sw1_times':smt['alt_sw1_times'],
                    'swept_extreme':sw2c[0],
                    'sw2_conf_idx':sw2c[1],
                    'sw2_conf_time':sw2c[2],
                })

        # Update length trackers for caching
        _last_sh_e_len = len(sh_e_hist); _last_sl_e_len = len(sl_e_hist)
        _last_sh_n_len = len(sh_n_hist); _last_sl_n_len = len(sl_n_hist)

        for c in candidates:
            direction = c['direction']
            instr     = c['instrument']
            instr_l   = instr.lower()

            # SMT_IN_FVG confirmation is the true next 1-minute bar after sw2.
            # FVG_AFTER_SMT does not use this gate — it searches from sw2 with
            # FVG_LOOKAHEAD. A failed SMT_IN_FVG confirmation is not a fallback
            # into FVG_AFTER_SMT; FVG_AFTER_SMT is its own path when there is
            # no valid 1-minute confirmation + pre-FVG membership.
            conf_bar_idx = c['sw2_conf_idx'] + 1
            conf_row = None
            conf_ok = False
            if conf_bar_idx < len(sdf):
                conf_row = sdf.iloc[conf_bar_idx]
                conf_ok = confirmation_bar_is_contiguous(
                    c['sw2_conf_time'], conf_row['et']
                )

            pre_fvg = None
            if conf_ok:
                entry_price = round(float(conf_row[f'close_{instr_l}']), 2)
                pre_fvg = find_preexisting_fvg(
                    sdf, conf_bar_idx, direction, instr, PRE_FVG_LOOKBACK, entry_price
                )
            elif conf_row is not None:
                stale_filtered += 1  # SMT_IN_FVG only: next bar is not +1 minute

            if pre_fvg:
                # ── [v8.8 S2] SMT_IN_FVG ──────────────────────────────────────
                # MARKET entry at the confirmation-bar close. SL just outside
                # the pre-existing FVG that close sits in, floored at the
                # shared 20/40-tick minimum. A post-SMT FVG is NOT required.
                entry_type = 'SMT_IN_FVG'
                sl_buf = ES_SMT_IN_FVG_SL_BUFFER if instr == 'ES' else NQ_SMT_IN_FVG_SL_BUFFER
                min_sl = min_stop_points(instr)
                stop_loss, risk = compute_smt_in_fvg_stop(
                    entry_price,
                    pre_fvg['pre_fvg_low'],
                    pre_fvg['pre_fvg_high'],
                    direction,
                    sl_buf,
                    min_sl,
                )
                target_R    = SMT_IN_FVG_TP_R
                take_profit = round(entry_price + target_R * risk, 2) if direction == 'LONG' \
                              else round(entry_price - target_R * risk, 2)
                fvg = None
                clock_row = conf_row
            else:
                # ── FVG_AFTER_SMT ─────────────────────────────────────────────
                # LIMIT entry at 50% of the post-SMT FVG. Not constrained by
                # SMT_IN_FVG's sw2+1 confirmation bar. [v8.8 S2] SL is
                # placed beyond the swing that formed just before the FVG (a swing low
                # for LONG, a swing high for SHORT), floored at the minimum stop size;
                # TP at FVG_AFTER_SMT_TP_R.
                entry_type = 'FVG_AFTER_SMT'
                fvg_start_idx = c.get('sw2_conf_idx', i)
                fvg = find_fvg(
                    sdf, fvg_start_idx, direction, instr,
                    FVG_LOOKAHEAD, c['swept_extreme']
                )
                if not fvg.get('fvg_found'):
                    continue  # no post-SMT FVG = no FVG_AFTER_SMT entry
                entry_price = fvg['entry_50']

                # Re-derive the prior swing at the FVG bar: snapshot as of
                # that bar, most recent swing timestamp strictly before it.
                # Do not reuse scan-bar i hist or compare positional sdf idx.
                fvg_row = sdf.iloc[fvg['fvg_bar_idx']]
                if instr == 'ES':
                    hist_col = 'sl_es_hist' if direction == 'LONG' else 'sh_es_hist'
                else:
                    hist_col = 'sl_nq_hist' if direction == 'LONG' else 'sh_nq_hist'
                prior_swing = prior_swing_before_timestamp(
                    fvg_row[hist_col], fvg_row['et']
                )

                sl_buf = ES_FVG_SL_BUFFER if instr == 'ES' else NQ_FVG_SL_BUFFER
                min_sl = min_stop_points(instr)
                if prior_swing is not None:
                    if direction == 'LONG':
                        stop_loss = round(prior_swing[0] - sl_buf, 2)
                    else:
                        stop_loss = round(prior_swing[0] + sl_buf, 2)
                else:
                    stop_loss = round(entry_price - min_sl, 2) if direction == 'LONG' \
                                else round(entry_price + min_sl, 2)
                stop_loss, risk = apply_min_stop(entry_price, stop_loss, direction, min_sl)
                target_R    = FVG_AFTER_SMT_TP_R
                take_profit = round(entry_price + target_R * risk, 2) if direction == 'LONG' \
                              else round(entry_price - target_R * risk, 2)
                clock_row = fvg_row

            # 15m bias + 5m confluence as of the confirmation clock, not scan i.
            # SMT_IN_FVG: sw2_conf_time + 1 minute. FVG_AFTER_SMT: FVG bar.
            clock_et = clock_row['et']
            bucket_key = pd.Timestamp(clock_et).floor('15min')
            if bucket_key in _macro_cache:
                es_bias_15m, nq_bias_15m, combined_bias_15m, bias_detail = _macro_cache[bucket_key]
            else:
                es_bias_15m, nq_bias_15m, combined_bias_15m, bias_detail = get_15m_macro_bias(
                    es15, nq15, clock_et
                )
                _macro_cache[bucket_key] = (es_bias_15m, nq_bias_15m, combined_bias_15m, bias_detail)
            expected_dir = 'BULLISH' if direction == 'LONG' else 'BEARISH'
            if expected_dir not in combined_bias_15m:
                mac_filtered += 1
                continue

            cache_key = (pd.Timestamp(clock_et).floor('5min'), direction)
            if cache_key in _smt5m_cache:
                smt5m = _smt5m_cache[cache_key]
            else:
                smt5m = check_5m_smt_confluence(es5, nq5, clock_et, direction)
                _smt5m_cache[cache_key] = smt5m
            if SMT_5M_MODE == 'strict':
                if smt5m['status'] != 'agree':
                    smt5m_filtered += 1
                    continue
            else:
                if smt5m['status'] in ('oppose', 'conflicted'):
                    smt5m_filtered += 1
                    continue

            # SMT_IN_FVG clock = confirmation bar (sw2 + 1 minute).
            # FVG_AFTER_SMT clock = FVG-formation bar. Never the scan bar i.
            signal = {
                'smt_time':   clock_row['et'],
                'date':       clock_row['date'],
                'session':    clock_row['session'],
                'es_close':   clock_row['close_es'],
                'nq_close':   clock_row['close_nq'],
                **{k:v for k,v in c.items() if k not in ('swept_extreme','sw2_conf_idx')},
                'entry_type': entry_type,
                # [v8.8 S2] entry / stop / target
                'entry_price': entry_price,
                'stop_loss':   stop_loss,
                'take_profit': take_profit,
                'target_R':    target_R,
                # post-SMT FVG context (FVG_AFTER_SMT only; None for SMT_IN_FVG)
                'fvg_found':  bool(fvg),
                'fvg_bar':    fvg['fvg_bar']  if fvg else None,
                'fvg_low':    fvg['fvg_low']  if fvg else None,
                'fvg_high':   fvg['fvg_high'] if fvg else None,
                'entry_50':   fvg['entry_50'] if fvg else None,
                'fvg_size':   fvg['fvg_size'] if fvg else None,
                # [v8.3] 15m macro bias state at the moment of the signal
                'es_15m_bias':       es_bias_15m,
                'nq_15m_bias':       nq_bias_15m,
                'combined_15m_bias': combined_bias_15m,
                'bias_detail':       bias_detail,
                # [v8.4] 5m SMT confluence context
                'smt5m_status':      smt5m['status'],
                'smt5m_time':        smt5m['smt_time'],
                'smt5m_prices':      smt5m['smt_prices'],
                # pre-existing FVG context (SMT_IN_FVG only)
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

    # [v8.8 S2] SMT_IN_FVG is decoupled from the post-SMT FVG (market entry), so it
    # legitimately has no post-SMT FVG. Require a post-SMT FVG only for FVG_AFTER_SMT.
    out = out[(out['entry_type']=='SMT_IN_FVG') | (out['fvg_found']==True)].copy()

    # FVG size filter — applies to FVG_AFTER_SMT only. The SMT_IN_FVG gap was already
    # size-checked inside find_preexisting_fvg().
    es_mask = (out['instrument']=='ES') & (out['fvg_size']>=ES_FVG_MIN) & (out['fvg_size']<=ES_FVG_MAX)
    nq_mask = (out['instrument']=='NQ') & (out['fvg_size']>=NQ_FVG_MIN) & (out['fvg_size']<=NQ_FVG_MAX)
    out = out[(out['entry_type']=='SMT_IN_FVG') | es_mask | nq_mask].copy()

    out = out.sort_values('smt_time').reset_index(drop=True)

    # [v8.7] Dedup by sw2_conf_time (the SMT identity), not 30-min bucket.
    # This prevents the same SMT from spawning multiple signals across
    # consecutive buckets. SMT_IN_FVG still preferred over FVG_AFTER_SMT
    # when both exist for the same sw2.
    out['bucket'] = pd.to_datetime(out['smt_time']).dt.floor('30min')   # kept for reference
    type_order = {'SMT_IN_FVG': 0, 'FVG_AFTER_SMT': 1}
    out['_type_rank'] = out['entry_type'].map(type_order).fillna(2)
    out = out.sort_values(['smt_time','_type_rank'])
    out = out.drop_duplicates(subset=['date','sw2_conf_time','direction','instrument'], keep='first')
    out = out.drop(columns=['_type_rank'])

    return out.reset_index(drop=True), ts_filtered, mac_filtered, stale_filtered, smt5m_filtered


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("SMT Scanner v8.8")
    print("="*60)
    print(f"SMT detection:    multi-sw2 walk-back + parallel sw1")
    print(f"sw2 candidates:   last {SW2_CANDIDATES} swings (same day)")
    print(f"Parallel sw1 tol: {SW1_PARALLEL_TOL_MINS} mins")
    print(f"sw2 staleness:    {SW2_STALENESS_MINS_1M} mins (1m) / {SW2_STALENESS_BARS_5M} bars (5m)")
    print(f"SMT_IN_FVG confirm: exactly {SMT_IN_FVG_CONFIRM_MINS:g} min after sw2 (not FVG_AFTER_SMT)")
    print(f"Dedup:            by sw2_conf_time (not 30-min bucket)  [v8.7]")
    print(f"FVG search from:  sw2 confirmation bar (not signal bar)  [v8.7]")
    print(f"ES_FVG_MIN:       {ES_FVG_MIN}pt  [v8.7 lowered from 1.0]")
    print(f"Macro filter:     15m structural bias (3-TF framework)")
    print(f"5m SMT mode:      {SMT_5M_MODE}")
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
        out_path = '/mnt/user-data/outputs/smt_signals_v8_8.csv'
        results.to_csv(out_path, index=False)
        print(f"\nSaved → {out_path}")
    else:
        print("No signals found.")
