"""
SMT Setup Scanner v8.1
Macro filter: Session Open Bias (Option 2)
New signal type: SMT_IN_FVG (pre-existing FVG, market entry)
"""

import pandas as pd
import numpy as np

ES_CSV = '/mnt/user-data/uploads/CME_MINI_MES1___1__7_.csv'
NQ_CSV = '/mnt/user-data/uploads/CME_MINI_MNQ1___1__7_.csv'

ES_MIN_PULLBACK  = 2.0;  NQ_MIN_PULLBACK  = 8.0
ES_FVG_MIN = 1.0;  ES_FVG_MAX = 25.0
NQ_FVG_MIN = 3.0;  NQ_FVG_MAX = 150.0
FVG_LOOKAHEAD    = 15
PRE_FVG_LOOKBACK = 30
MAX_SW_TIME_GAP_MINS    = 0
SESSION_CUTOFF_MINS     = 30
PRE_SESSION_LOOKBACK_MINS = 30
SESSIONS = [('NY Morning',8,0,10,30),('NY Afternoon',13,0,15,0)]

def load(es_path, nq_path):
    es = pd.read_csv(es_path); nq = pd.read_csv(nq_path)
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

def classify_bar(hour, minute):
    t = hour*60+minute
    for name,sh,sm,eh,em in SESSIONS:
        ss=sh*60+sm; se=eh*60+em; ps=ss-PRE_SESSION_LOOKBACK_MINS; ce=se-SESSION_CUTOFF_MINS
        if ss<=t<ce: return 'session',name
        if ps<=t<ss: return 'pre_session',name
    return None,None

def fvg_in_session(et):
    t=et.hour*60+et.minute
    for name,sh,sm,eh,em in SESSIONS:
        if sh*60+sm<=t<eh*60+em: return True
    return False

def get_session_open_bias(df_full, signal_et, instrument):
    col_h=f'high_{instrument.lower()}'; col_l=f'low_{instrument.lower()}'; col_c=f'close_{instrument.lower()}'
    sig_date=signal_et.date(); sig_t=signal_et.hour*60+signal_et.minute
    if 480<=sig_t<630:
        prev_days=df_full[df_full['et'].dt.date<sig_date]
        if prev_days.empty: return 'unclear'
        prev_day=prev_days['et'].dt.date.max()
        prev_bars=df_full[df_full['et'].dt.date==prev_day]
        if prev_bars.empty: return 'unclear'
        ref_mid=(prev_bars[col_h].max()+prev_bars[col_l].min())/2
        open_bars=df_full[(df_full['et'].dt.date==sig_date)&(df_full['et'].dt.hour==8)&(df_full['et'].dt.minute<=5)]
        if open_bars.empty: return 'unclear'
        open_price=open_bars.iloc[0][col_c]
    elif 780<=sig_t<900:
        morning=df_full[(df_full['et'].dt.date==sig_date)&(df_full['et'].dt.hour*60+df_full['et'].dt.minute>=480)&(df_full['et'].dt.hour*60+df_full['et'].dt.minute<630)]
        if morning.empty: return 'unclear'
        ref_mid=(morning[col_h].max()+morning[col_l].min())/2
        open_bars=df_full[(df_full['et'].dt.date==sig_date)&(df_full['et'].dt.hour==13)&(df_full['et'].dt.minute<=5)]
        if open_bars.empty: return 'unclear'
        open_price=open_bars.iloc[0][col_c]
    else:
        return 'unclear'
    if open_price>ref_mid: return 'bullish'
    elif open_price<ref_mid: return 'bearish'
    return 'unclear'

def get_swings(flags,prices,indices,timestamps,n=2):
    recents=[]; history=[]
    for flag,price,idx,ts in zip(flags,prices,indices,timestamps):
        if flag==1: history.append((float(price),int(idx),ts))
        recents.append(list(history[-n:]) if len(history)>=n else [])
    return recents

def structural_pullback(sdf,i1,i2,swing_type,instrument,min_pb):
    start,end=min(i1,i2),max(i1,i2)
    if end<=start+1: return False
    segment=sdf.iloc[start:end+1]
    if swing_type=='high':
        ref=max(sdf.iloc[i1][f'high_{instrument}'],sdf.iloc[i2][f'high_{instrument}'])
        return (ref-segment[f'low_{instrument}'].min())>=min_pb
    else:
        ref=min(sdf.iloc[i1][f'low_{instrument}'],sdf.iloc[i2][f'low_{instrument}'])
        return (segment[f'high_{instrument}'].max()-ref)>=min_pb

def timestamps_aligned(t1,t2):
    return abs((pd.Timestamp(t1)-pd.Timestamp(t2)).total_seconds())/60<=MAX_SW_TIME_GAP_MINS

def find_preexisting_fvg(sdf, smt_idx, direction, instrument, lookback, current_price):
    col_h=f'high_{instrument.lower()}'; col_l=f'low_{instrument.lower()}'
    start=max(0,smt_idx-lookback)
    fvg_min=ES_FVG_MIN if instrument=='ES' else NQ_FVG_MIN
    fvg_max=ES_FVG_MAX if instrument=='ES' else NQ_FVG_MAX
    for j in range(smt_idx-1,start,-1):
        if j-1<0 or j+1>=len(sdf): continue
        ph=sdf.iloc[j-1][col_h]; pl=sdf.iloc[j-1][col_l]
        nh=sdf.iloc[j+1][col_h]; nl=sdf.iloc[j+1][col_l]
        if direction=='LONG' and ph<nl:
            lo,hi=ph,nl
            if lo<=current_price<=hi:
                size=round(hi-lo,2)
                if fvg_min<=size<=fvg_max:
                    return dict(fvg_found=True,fvg_bar=sdf.iloc[j]['et'],fvg_low=lo,fvg_high=hi,entry_50=round((lo+hi)/2,2),fvg_size=size,entry_type='SMT_IN_FVG')
        if direction=='SHORT' and pl>nh:
            lo,hi=nh,pl
            if lo<=current_price<=hi:
                size=round(hi-lo,2)
                if fvg_min<=size<=fvg_max:
                    return dict(fvg_found=True,fvg_bar=sdf.iloc[j]['et'],fvg_low=lo,fvg_high=hi,entry_50=round((lo+hi)/2,2),fvg_size=size,entry_type='SMT_IN_FVG')
    return None

def find_fvg(sdf,start_idx,direction,instrument,lookahead,swept_extreme):
    col_h=f'high_{instrument.lower()}'; col_l=f'low_{instrument.lower()}'
    end=min(start_idx+lookahead,len(sdf)-2)
    for j in range(start_idx+1,end+1):
        ph=sdf.iloc[j-1][col_h]; pl=sdf.iloc[j-1][col_l]
        nh=sdf.iloc[j+1][col_h]; nl=sdf.iloc[j+1][col_l]
        if direction=='LONG' and ph<nl:
            lo,hi=ph,nl; mid=(lo+hi)/2
            if mid>swept_extreme and fvg_in_session(sdf.iloc[j]['et']):
                return dict(fvg_found=True,fvg_bar=sdf.iloc[j]['et'],fvg_low=lo,fvg_high=hi,entry_50=round(mid,2),fvg_size=round(hi-lo,2),entry_type='FVG_AFTER_SMT')
        if direction=='SHORT' and pl>nh:
            lo,hi=nh,pl; mid=(lo+hi)/2
            if mid<swept_extreme and fvg_in_session(sdf.iloc[j]['et']):
                return dict(fvg_found=True,fvg_bar=sdf.iloc[j]['et'],fvg_low=lo,fvg_high=hi,entry_50=round(mid,2),fvg_size=round(hi-lo,2),entry_type='FVG_AFTER_SMT')
    return dict(fvg_found=False,entry_type=None)

def fmt_ts(ts):
    try: return str(ts)[:16].replace('T',' ')
    except: return str(ts)[:16]

def run(es_path, nq_path):
    df=load(es_path,nq_path)
    bcs=[classify_bar(r['hour'],r['minute']) for _,r in df.iterrows()]
    df['bar_type'],df['bar_session']=zip(*bcs)
    sdf=df[df['bar_type'].notna()].copy().reset_index(drop=True)
    sdf['session']=sdf['bar_session']
    df_full=df.copy()
    sdf['sh_es']=get_swings(sdf['Swing High_es'],sdf['high_es'],sdf.index,sdf['et'])
    sdf['sl_es']=get_swings(sdf['Swing Low_es'],sdf['low_es'],sdf.index,sdf['et'])
    sdf['sh_nq']=get_swings(sdf['Swing High_nq'],sdf['high_nq'],sdf.index,sdf['et'])
    sdf['sl_nq']=get_swings(sdf['Swing Low_nq'],sdf['low_nq'],sdf.index,sdf['et'])
    signals=[]; ts_filtered=0; mac_filtered=0

    for i,row in sdf.iterrows():
        if row['bar_type']!='session': continue
        sh_e,sl_e=row['sh_es'],row['sl_es']; sh_n,sl_n=row['sh_nq'],row['sl_nq']
        candidates=[]

        if len(sl_e)==2 and len(sl_n)==2:
            (p_e1,i_e1,t_e1),(p_e2,i_e2,t_e2)=sl_e; (p_n1,i_n1,t_n1),(p_n2,i_n2,t_n2)=sl_n
            if timestamps_aligned(t_e1,t_n1) and timestamps_aligned(t_e2,t_n2):
                if p_e2<p_e1 and (p_n2>p_n1 or p_n2==p_n1):
                    if structural_pullback(sdf,i_e1,i_e2,'low','es',ES_MIN_PULLBACK) and structural_pullback(sdf,i_n1,i_n2,'low','nq',NQ_MIN_PULLBACK):
                        candidates.append({'direction':'LONG','instrument':'ES','sweep_pts':round(p_e1-p_e2,2),'fail_pts':round(abs(p_n2-p_n1),2),'smt_variant':'equal' if p_n2==p_n1 else 'standard','es_sw1_time':fmt_ts(t_e1),'es_sw1_price':p_e1,'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':p_e2,'nq_sw1_time':fmt_ts(t_n1),'nq_sw1_price':p_n1,'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':p_n2,'swept_extreme':p_e2})
                elif p_n2<p_n1 and (p_e2>p_e1 or p_e2==p_e1):
                    if structural_pullback(sdf,i_e1,i_e2,'low','es',ES_MIN_PULLBACK) and structural_pullback(sdf,i_n1,i_n2,'low','nq',NQ_MIN_PULLBACK):
                        candidates.append({'direction':'LONG','instrument':'NQ','sweep_pts':round(p_n1-p_n2,2),'fail_pts':round(abs(p_e2-p_e1),2),'smt_variant':'equal' if p_e2==p_e1 else 'standard','es_sw1_time':fmt_ts(t_e1),'es_sw1_price':p_e1,'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':p_e2,'nq_sw1_time':fmt_ts(t_n1),'nq_sw1_price':p_n1,'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':p_n2,'swept_extreme':p_n2})
            else:
                ts_filtered+=1

        if len(sh_e)==2 and len(sh_n)==2:
            (p_e1,i_e1,t_e1),(p_e2,i_e2,t_e2)=sh_e; (p_n1,i_n1,t_n1),(p_n2,i_n2,t_n2)=sh_n
            if timestamps_aligned(t_e1,t_n1) and timestamps_aligned(t_e2,t_n2):
                if p_e2>p_e1 and (p_n2<p_n1 or p_n2==p_n1):
                    if structural_pullback(sdf,i_e1,i_e2,'high','es',ES_MIN_PULLBACK) and structural_pullback(sdf,i_n1,i_n2,'high','nq',NQ_MIN_PULLBACK):
                        candidates.append({'direction':'SHORT','instrument':'ES','sweep_pts':round(p_e2-p_e1,2),'fail_pts':round(abs(p_n1-p_n2),2),'smt_variant':'equal' if p_n2==p_n1 else 'standard','es_sw1_time':fmt_ts(t_e1),'es_sw1_price':p_e1,'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':p_e2,'nq_sw1_time':fmt_ts(t_n1),'nq_sw1_price':p_n1,'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':p_n2,'swept_extreme':p_e2})
                elif p_n2>p_n1 and (p_e2<p_e1 or p_e2==p_e1):
                    if structural_pullback(sdf,i_e1,i_e2,'high','es',ES_MIN_PULLBACK) and structural_pullback(sdf,i_n1,i_n2,'high','nq',NQ_MIN_PULLBACK):
                        candidates.append({'direction':'SHORT','instrument':'NQ','sweep_pts':round(p_n2-p_n1,2),'fail_pts':round(abs(p_e1-p_e2),2),'smt_variant':'equal' if p_e2==p_e1 else 'standard','es_sw1_time':fmt_ts(t_e1),'es_sw1_price':p_e1,'es_sw2_time':fmt_ts(t_e2),'es_sw2_price':p_e2,'nq_sw1_time':fmt_ts(t_n1),'nq_sw1_price':p_n1,'nq_sw2_time':fmt_ts(t_n2),'nq_sw2_price':p_n2,'swept_extreme':p_n2})
            else:
                ts_filtered+=1

        for c in candidates:
            bias=get_session_open_bias(df_full,row['et'],c['instrument'])
            expected='bullish' if c['direction']=='LONG' else 'bearish'
            if bias!=expected:
                mac_filtered+=1
                continue
            current_price=row[f"close_{c['instrument'].lower()}"]
            pre_fvg=find_preexisting_fvg(sdf,i,c['direction'],c['instrument'],PRE_FVG_LOOKBACK,current_price)
            if pre_fvg:
                signals.append({'smt_time':row['et'],'date':row['date'],'session':row['session'],'es_close':row['close_es'],'nq_close':row['close_nq'],**{k:v for k,v in c.items() if k!='swept_extreme'},**pre_fvg})
                continue
            fvg=find_fvg(sdf,i,c['direction'],c['instrument'],FVG_LOOKAHEAD,c['swept_extreme'])
            if fvg.get('fvg_found'):
                signals.append({'smt_time':row['et'],'date':row['date'],'session':row['session'],'es_close':row['close_es'],'nq_close':row['close_nq'],**{k:v for k,v in c.items() if k!='swept_extreme'},**fvg})

    out=pd.DataFrame(signals)
    if out.empty:
        print(f"No signals. TS: {ts_filtered} | Macro: {mac_filtered}")
        return out,ts_filtered,mac_filtered
    out=out[out['fvg_found']==True].copy()
    es_mask=(out['instrument']=='ES')&(out['fvg_size']>=ES_FVG_MIN)&(out['fvg_size']<=ES_FVG_MAX)
    nq_mask=(out['instrument']=='NQ')&(out['fvg_size']>=NQ_FVG_MIN)&(out['fvg_size']<=NQ_FVG_MAX)
    out=out[es_mask|nq_mask].copy()
    out=out.sort_values('smt_time').reset_index(drop=True)
    out['bucket']=pd.to_datetime(out['smt_time']).dt.floor('30min')
    out=out.drop_duplicates(subset=['date','bucket','direction','instrument'])
    return out.reset_index(drop=True),ts_filtered,mac_filtered

if __name__=='__main__':
    print("SMT Scanner v8.1"); print("="*60)
    results,ts,mac=run(ES_CSV,NQ_CSV)
    if not results.empty:
        total=len(results); days=results['date'].nunique()
        print(f"Signals: {total} | Days: {days} | Avg/day: {total/days:.1f}")
        print(f"TS filtered: {ts} | Macro filtered: {mac}")
        print(f"By entry type: {results['entry_type'].value_counts().to_dict()}")
        results.to_csv('/mnt/user-data/outputs/smt_signals_v8_1.csv',index=False)
        print("Saved -> smt_signals_v8_1.csv")
