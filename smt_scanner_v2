"""
SMT Setup Scanner v2.1 — ES/NQ 1-min
Adds: exact swing candle timestamps + prices for both ES and NQ
so each signal shows the two bars the scanner used for the SMT comparison.
"""

import pandas as pd
import numpy as np
from pathlib import Path

ES_CSV = '/mnt/user-data/uploads/CME_MINI_MES1___1__1_.csv'
NQ_CSV = '/mnt/user-data/uploads/CME_MINI_MNQ1___1__1_.csv'

ES_MIN_PULLBACK  = 4.0
NQ_MIN_PULLBACK  = 15.0
ES_FVG_MIN       = 1.0
ES_FVG_MAX       = 25.0
NQ_FVG_MIN       = 3.0
NQ_FVG_MAX       = 150.0
FVG_LOOKAHEAD    = 15

# ── LOAD & ALIGN ──────────────────────────────────────────────────
def load(es_path, nq_path):
    es = pd.read_csv(es_path)
    nq = pd.read_csv(nq_path)
    for df in [es, nq]:
        df['time'] = pd.to_datetime(df['time'], utc=True)
        df['Swing High'] = pd.to_numeric(df['Swing High'], errors='coerce').fillna(0)
        df['Swing Low']  = pd.to_numeric(df['Swing Low'],  errors='coerce').fillna(0)
    merged = pd.merge(es, nq, on='time', suffixes=('_es','_nq'))
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

# ── SWING HISTORY — stores (price, bar_index, et_timestamp) ───────
def get_swings(flags, prices, indices, timestamps, n=2):
    recents, history = [], []
    for flag, price, idx, ts in zip(flags, prices, indices, timestamps):
        if flag == 1:
            history.append((float(price), int(idx), ts))
        recents.append(list(history[-n:]) if len(history) >= n else [])
    return recents

# ── STRUCTURAL PULLBACK CHECK ─────────────────────────────────────
def structural_pullback(sdf, i1, i2, swing_type, instrument, min_pb):
    start, end = min(i1, i2), max(i1, i2)
    if end <= start + 1:
        return False
    segment = sdf.iloc[start:end+1]
    if swing_type == 'high':
        ref_high = max(sdf.iloc[i1][f'high_{instrument}'], sdf.iloc[i2][f'high_{instrument}'])
        min_low  = segment[f'low_{instrument}'].min()
        return (ref_high - min_low) >= min_pb
    else:
        ref_low  = min(sdf.iloc[i1][f'low_{instrument}'], sdf.iloc[i2][f'low_{instrument}'])
        max_high = segment[f'high_{instrument}'].max()
        return (max_high - ref_low) >= min_pb

# ── FVG FINDER ────────────────────────────────────────────────────
def find_fvg(sdf, start_idx, direction, instrument, lookahead):
    col_h = f'high_{instrument.lower()}'
    col_l = f'low_{instrument.lower()}'
    end = min(start_idx + lookahead, len(sdf) - 2)
    for j in range(start_idx + 1, end + 1):
        ph = sdf.iloc[j-1][col_h];  pl = sdf.iloc[j-1][col_l]
        nh = sdf.iloc[j+1][col_h];  nl = sdf.iloc[j+1][col_l]
        if direction == 'LONG' and ph < nl:
            lo, hi = ph, nl
            return dict(fvg_found=True, fvg_bar=sdf.iloc[j]['et'],
                        fvg_low=lo, fvg_high=hi,
                        entry_50=round((lo+hi)/2, 2), fvg_size=round(hi-lo, 2))
        if direction == 'SHORT' and pl > nh:
            lo, hi = nh, pl
            return dict(fvg_found=True, fvg_bar=sdf.iloc[j]['et'],
                        fvg_low=lo, fvg_high=hi,
                        entry_50=round((lo+hi)/2, 2), fvg_size=round(hi-lo, 2))
    return dict(fvg_found=False)

def fmt_ts(ts):
    """Format ET timestamp to readable HH:MM string."""
    try:
        return str(ts)[:16].replace('T', ' ')
    except:
        return str(ts)[:16]

# ── MAIN ──────────────────────────────────────────────────────────
def run(es_path, nq_path):
    df = load(es_path, nq_path)
    df['session'] = df.apply(session, axis=1)
    sdf = df[df['session'].notna()].copy().reset_index(drop=True)

    sdf['sh_es'] = get_swings(sdf['Swing High_es'], sdf['high_es'], sdf.index, sdf['et'])
    sdf['sl_es'] = get_swings(sdf['Swing Low_es'],  sdf['low_es'],  sdf.index, sdf['et'])
    sdf['sh_nq'] = get_swings(sdf['Swing High_nq'], sdf['high_nq'], sdf.index, sdf['et'])
    sdf['sl_nq'] = get_swings(sdf['Swing Low_nq'],  sdf['low_nq'],  sdf.index, sdf['et'])

    signals = []

    for i, row in sdf.iterrows():
        sh_e, sl_e = row['sh_es'], row['sl_es']
        sh_n, sl_n = row['sh_nq'], row['sl_nq']

        candidates = []

        # ── BULLISH SMT (swing lows) ──────────────────────────
        if len(sl_e) == 2 and len(sl_n) == 2:
            (p_e1, i_e1, t_e1), (p_e2, i_e2, t_e2) = sl_e
            (p_n1, i_n1, t_n1), (p_n2, i_n2, t_n2) = sl_n

            # ES sweeps lower, NQ holds → buy ES
            if p_e2 < p_e1 and p_n2 > p_n1:
                if structural_pullback(sdf, i_e1, i_e2, 'low', 'es', ES_MIN_PULLBACK) and \
                   structural_pullback(sdf, i_n1, i_n2, 'low', 'nq', NQ_MIN_PULLBACK):
                    candidates.append({
                        'direction': 'LONG', 'instrument': 'ES',
                        'sweep_pts': round(p_e1-p_e2, 2), 'fail_pts': round(p_n2-p_n1, 2),
                        # LONG: comparing LOWS
                        'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,  # sw1 low
                        'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,  # sw2 low (swept lower)
                        'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,  # sw1 low
                        'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,  # sw2 low (held higher)
                    })

            # NQ sweeps lower, ES holds → buy NQ
            elif p_n2 < p_n1 and p_e2 > p_e1:
                if structural_pullback(sdf, i_e1, i_e2, 'low', 'es', ES_MIN_PULLBACK) and \
                   structural_pullback(sdf, i_n1, i_n2, 'low', 'nq', NQ_MIN_PULLBACK):
                    candidates.append({
                        'direction': 'LONG', 'instrument': 'NQ',
                        'sweep_pts': round(p_n1-p_n2, 2), 'fail_pts': round(p_e2-p_e1, 2),
                        # LONG: comparing LOWS
                        'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,  # sw1 low (held higher)
                        'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,  # sw2 low
                        'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,  # sw1 low
                        'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,  # sw2 low (swept lower)
                    })

        # ── BEARISH SMT (swing highs) ─────────────────────────
        if len(sh_e) == 2 and len(sh_n) == 2:
            (p_e1, i_e1, t_e1), (p_e2, i_e2, t_e2) = sh_e
            (p_n1, i_n1, t_n1), (p_n2, i_n2, t_n2) = sh_n

            # ES sweeps higher, NQ fails → sell ES
            if p_e2 > p_e1 and p_n2 < p_n1:
                if structural_pullback(sdf, i_e1, i_e2, 'high', 'es', ES_MIN_PULLBACK) and \
                   structural_pullback(sdf, i_n1, i_n2, 'high', 'nq', NQ_MIN_PULLBACK):
                    candidates.append({
                        'direction': 'SHORT', 'instrument': 'ES',
                        'sweep_pts': round(p_e2-p_e1, 2), 'fail_pts': round(p_n1-p_n2, 2),
                        # SHORT: comparing HIGHS
                        'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,  # sw1 high
                        'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,  # sw2 high (swept higher)
                        'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,  # sw1 high
                        'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,  # sw2 high (held lower)
                    })

            # NQ sweeps higher, ES fails → sell NQ
            elif p_n2 > p_n1 and p_e2 < p_e1:
                if structural_pullback(sdf, i_e1, i_e2, 'high', 'es', ES_MIN_PULLBACK) and \
                   structural_pullback(sdf, i_n1, i_n2, 'high', 'nq', NQ_MIN_PULLBACK):
                    candidates.append({
                        'direction': 'SHORT', 'instrument': 'NQ',
                        'sweep_pts': round(p_n2-p_n1, 2), 'fail_pts': round(p_e1-p_e2, 2),
                        # SHORT: comparing HIGHS
                        'es_sw1_time': fmt_ts(t_e1), 'es_sw1_price': p_e1,  # sw1 high (held lower)
                        'es_sw2_time': fmt_ts(t_e2), 'es_sw2_price': p_e2,  # sw2 high
                        'nq_sw1_time': fmt_ts(t_n1), 'nq_sw1_price': p_n1,  # sw1 high
                        'nq_sw2_time': fmt_ts(t_n2), 'nq_sw2_price': p_n2,  # sw2 high (swept higher)
                    })

        for c in candidates:
            fvg = find_fvg(sdf, i, c['direction'], c['instrument'], FVG_LOOKAHEAD)
            signals.append({
                'smt_time':   row['et'],
                'date':       row['date'],
                'session':    row['session'],
                'es_close':   row['close_es'],
                'nq_close':   row['close_nq'],
                **c,
                **fvg
            })

    out = pd.DataFrame(signals)
    if out.empty:
        print("No signals found.")
        return out

    out = out[out['fvg_found'] == True].copy()
    es_mask = (out['instrument']=='ES') & (out['fvg_size']>=ES_FVG_MIN) & (out['fvg_size']<=ES_FVG_MAX)
    nq_mask = (out['instrument']=='NQ') & (out['fvg_size']>=NQ_FVG_MIN) & (out['fvg_size']<=NQ_FVG_MAX)
    out = out[es_mask | nq_mask].copy()

    out = out.sort_values('smt_time').reset_index(drop=True)
    out['bucket'] = pd.to_datetime(out['smt_time']).dt.floor('30min')
    out = out.drop_duplicates(subset=['date','bucket','direction','instrument'])

    return out.reset_index(drop=True)

if __name__ == '__main__':
    print("Running SMT Scanner v2.1...")
    results = run(ES_CSV, NQ_CSV)
    if not results.empty:
        total = len(results)
        days  = results['date'].nunique()
        print(f"Total: {total}  |  Days: {days}  |  Avg/day: {total/days:.1f}")
        # Show swing reference data for first few signals
        ref_cols = ['smt_time','direction','instrument',
                    'es_sw1_time','es_sw1_price','es_sw2_time','es_sw2_price',
                    'nq_sw1_time','nq_sw1_price','nq_sw2_time','nq_sw2_price']
        print("\nSample swing references:")
        print(results[ref_cols].head(8).to_string())
        results.to_csv('/mnt/user-data/outputs/smt_signals_v2.csv', index=False)
        print("\nSaved to smt_signals_v2.csv")
