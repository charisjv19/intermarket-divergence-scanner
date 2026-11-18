"""
Macro Bias Diagnostic Script — v1.0
Reads 1-minute MES/MNQ CSVs, resamples to 15m and 5m,
then produces a session-by-session macro bias report.

Reads strictly left to right — no lookahead.
Swing confirmation requires the bar AFTER the swing to have closed.

Output: macro_bias_report.csv + printed summary
"""

import pandas as pd
import numpy as np

# ── CONFIG ────────────────────────────────────────────────────────────────────
ES_CSV = "CME_MINI_MES1_1_1.csv"   # update path as needed
NQ_CSV = "CME_MINI_MNQ1_1_1.csv"   # update path as needed

SESSIONS = [
    ("NY Morning",   8,  0, 10, 30),
    ("NY Afternoon", 13, 0, 15,  0),
]

SLOWING_THRESHOLD = 0.80   # leg must be >= 80% of previous to hold momentum
MIN_LEGS_FOR_MOMENTUM = 3  # need at least 3 legs to compare momentum
LOOKBACK_HOURS = 36        # how far back to look on 15m for swing context


# ── LOAD & RESAMPLE ───────────────────────────────────────────────────────────
def load_and_resample(es_path, nq_path):
    """Load 1m CSVs and resample to 5m and 15m."""
    def load_one(path):
        df = pd.read_csv(path)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.sort_values("time").reset_index(drop=True)
        df["et"] = df["time"].dt.tz_convert("America/New_York")
        return df.set_index("time")

    es1 = load_one(es_path)
    nq1 = load_one(nq_path)

    def resample_ohlc(df, rule):
        ohlc = df[["open","high","low","close"]].resample(rule, label="left", closed="left").agg({
            "open":  "first",
            "high":  "max",
            "low":   "min",
            "close": "last",
        }).dropna()
        ohlc["et"] = ohlc.index.tz_convert("America/New_York")
        return ohlc.reset_index()

    es5  = resample_ohlc(es1,  "5min")
    nq5  = resample_ohlc(nq1,  "5min")
    es15 = resample_ohlc(es1, "15min")
    nq15 = resample_ohlc(nq1, "15min")

    return es15, nq15, es5, nq5


# ── SWING DETECTION (left-to-right, no lookahead) ─────────────────────────────
def detect_swings(df):
    """
    Confirm swings on bar[1] using bar[0] as confirmation.
    Mimics: isSwingHigh = high[1] > high[2] and high[1] > high[3] and high[1] > high[0]
    Returns df with swing_high and swing_low columns (price or NaN).
    Works strictly in order — only looks at already-closed bars.
    """
    highs = df["high"].values
    lows  = df["low"].values
    n     = len(df)

    swing_high = np.full(n, np.nan)
    swing_low  = np.full(n, np.nan)

    for i in range(3, n):
        # bar[i] is the confirmation bar (just closed)
        # bar[i-1] is the candidate swing bar
        candidate_h = highs[i-1]
        candidate_l = lows[i-1]

        # swing high: candidate > bars on both sides + one bar further back
        if (candidate_h > highs[i-2] and
            candidate_h > highs[i-3] and
            candidate_h > highs[i]):
            swing_high[i-1] = candidate_h

        # swing low: candidate < bars on both sides + one bar further back
        if (candidate_l < lows[i-2] and
            candidate_l < lows[i-3] and
            candidate_l < lows[i]):
            swing_low[i-1] = candidate_l

    df = df.copy()
    df["swing_high"] = swing_high
    df["swing_low"]  = swing_low
    return df


# ── STRUCTURE & MOMENTUM CLASSIFIER ──────────────────────────────────────────
def classify_structure(swing_highs, swing_lows):
    """
    Given ordered lists of (time, price) swing highs and lows,
    classify sequence and measure impulse leg momentum.

    Returns: (structure, momentum, detail_string)
    structure: 'bullish' | 'bearish' | 'transitional'
    momentum:  'strong' | 'slowing' | 'insufficient_data'
    """
    if len(swing_highs) < 2 and len(swing_lows) < 2:
        return "insufficient_data", "insufficient_data", "Not enough swings to classify"

    # ── sequence check ────────────────────────────────────────────────────────
    # look at last 3 swing highs and lows to determine HH/HL or LH/LL
    sh_prices = [p for _, p in swing_highs[-4:]]
    sl_prices = [p for _, p in swing_lows[-4:]]

    hh_count = sum(1 for i in range(1, len(sh_prices)) if sh_prices[i] > sh_prices[i-1])
    lh_count = sum(1 for i in range(1, len(sh_prices)) if sh_prices[i] < sh_prices[i-1])
    hl_count = sum(1 for i in range(1, len(sl_prices)) if sl_prices[i] > sl_prices[i-1])
    ll_count = sum(1 for i in range(1, len(sl_prices)) if sl_prices[i] < sl_prices[i-1])

    bullish_score = hh_count + hl_count
    bearish_score = lh_count + ll_count

    if bullish_score > bearish_score and hh_count > 0 and hl_count > 0:
        structure = "bullish"
    elif bearish_score > bullish_score and lh_count > 0 and ll_count > 0:
        structure = "bearish"
    else:
        structure = "transitional"

    detail = (f"SH seq: {sh_prices} | SL seq: {sl_prices} | "
              f"HH:{hh_count} LH:{lh_count} HL:{hl_count} LL:{ll_count}")

    # ── momentum check ────────────────────────────────────────────────────────
    if structure == "transitional":
        return structure, "n/a", detail

    # build impulse legs
    # bullish: measure from structural break point (previous SH) to new SH
    # bearish: measure from structural break point (previous SL) to new SL
    legs = []
    if structure == "bullish" and len(swing_highs) >= MIN_LEGS_FOR_MOMENTUM:
        sh_list = swing_highs[-MIN_LEGS_FOR_MOMENTUM-1:]
        for i in range(1, len(sh_list)):
            prev_price = sh_list[i-1][1]
            curr_price = sh_list[i][1]
            if curr_price > prev_price:
                legs.append(curr_price - prev_price)

    elif structure == "bearish" and len(swing_lows) >= MIN_LEGS_FOR_MOMENTUM:
        sl_list = swing_lows[-MIN_LEGS_FOR_MOMENTUM-1:]
        for i in range(1, len(sl_list)):
            prev_price = sl_list[i-1][1]
            curr_price = sl_list[i][1]
            if curr_price < prev_price:
                legs.append(prev_price - curr_price)

    if len(legs) < 2:
        return structure, "insufficient_data", detail + f" | Legs: {[round(l,2) for l in legs]}"

    # compare most recent leg to previous leg
    prev_leg = legs[-2]
    curr_leg = legs[-1]
    ratio = curr_leg / prev_leg if prev_leg > 0 else 1.0

    if ratio >= SLOWING_THRESHOLD:
        momentum = "strong"
    else:
        momentum = "slowing"

    detail += f" | Legs: {[round(l,2) for l in legs]} | Last ratio: {round(ratio*100,1)}%"
    return structure, momentum, detail


# ── FINAL BIAS LABEL ──────────────────────────────────────────────────────────
def bias_label(structure, momentum):
    if structure == "insufficient_data":
        return "UNCLEAR"
    if structure == "transitional":
        return "TRANSITIONAL"
    if structure == "bullish":
        return "STRONGLY BULLISH" if momentum == "strong" else \
               "BULLISH SLOWING"  if momentum == "slowing" else "BULLISH"
    if structure == "bearish":
        return "STRONGLY BEARISH" if momentum == "strong" else \
               "BEARISH SLOWING"  if momentum == "slowing" else "BEARISH"
    return "UNCLEAR"


# ── SESSION BIAS ENGINE ───────────────────────────────────────────────────────
def get_session_bias(es15, nq15, session_start_et):
    """
    At the moment the session opens, look back LOOKBACK_HOURS on 15m
    and classify macro bias for ES and NQ independently,
    then combine into a single directional call.
    Reads only bars that closed BEFORE session_start_et (no lookahead).
    """
    cutoff = session_start_et

    es_window = es15[es15["et"] < cutoff].tail(int(LOOKBACK_HOURS * 4))  # 15m bars in window
    nq_window = nq15[nq15["et"] < cutoff].tail(int(LOOKBACK_HOURS * 4))

    if es_window.empty or nq_window.empty:
        return "UNCLEAR", "UNCLEAR", "UNCLEAR", "insufficient data"

    # detect swings on the window (already in time order)
    es_sw = detect_swings(es_window.reset_index(drop=True))
    nq_sw = detect_swings(nq_window.reset_index(drop=True))

    # extract confirmed swings
    es_highs = [(row["et"], row["swing_high"]) for _, row in es_sw.iterrows()
                if not np.isnan(row["swing_high"])]
    es_lows  = [(row["et"], row["swing_low"])  for _, row in es_sw.iterrows()
                if not np.isnan(row["swing_low"])]
    nq_highs = [(row["et"], row["swing_high"]) for _, row in nq_sw.iterrows()
                if not np.isnan(row["swing_high"])]
    nq_lows  = [(row["et"], row["swing_low"])  for _, row in nq_sw.iterrows()
                if not np.isnan(row["swing_low"])]

    es_struct, es_mom, es_detail = classify_structure(es_highs, es_lows)
    nq_struct, nq_mom, nq_detail = classify_structure(nq_highs, nq_lows)

    es_bias = bias_label(es_struct, es_mom)
    nq_bias = bias_label(nq_struct, nq_mom)

    # combined: both must agree for a clear directional bias
    es_dir = "bullish" if "BULLISH" in es_bias else "bearish" if "BEARISH" in es_bias else "unclear"
    nq_dir = "bullish" if "BULLISH" in nq_bias else "bearish" if "BEARISH" in nq_bias else "unclear"

    if es_dir == nq_dir and es_dir != "unclear":
        combined = es_dir.upper()
        if "SLOWING" in es_bias or "SLOWING" in nq_bias:
            combined += " (SLOWING)"
    elif es_dir == "unclear" and nq_dir == "unclear":
        combined = "UNCLEAR"
    else:
        combined = "CONFLICTED"

    return es_bias, nq_bias, combined, f"ES: {es_detail} || NQ: {nq_detail}"


# ── MAIN REPORT ───────────────────────────────────────────────────────────────
def run_diagnostic(es_csv, nq_csv, output_csv="macro_bias_report.csv"):
    print(f"\nLoading data...")
    es15, nq15, es5, nq5 = load_and_resample(es_csv, nq_csv)

    # get all unique trading dates
    all_dates = sorted(es15["et"].dt.date.unique())
    print(f"Dates found: {all_dates[0]} → {all_dates[-1]} ({len(all_dates)} days)\n")

    rows = []

    for date in all_dates:
        for sess_name, sh, sm, eh, em in SESSIONS:
            # session open timestamp
            sess_open = pd.Timestamp(
                year=date.year, month=date.month, day=date.day,
                hour=sh, minute=sm, second=0,
                tz="America/New_York"
            )

            es_bias, nq_bias, combined, detail = get_session_bias(es15, nq15, sess_open)

            row = {
                "date":        str(date),
                "session":     sess_name,
                "session_open": sess_open.strftime("%Y-%m-%d %H:%M"),
                "es_15m_bias": es_bias,
                "nq_15m_bias": nq_bias,
                "combined_bias": combined,
                "detail":      detail,
            }
            rows.append(row)

            # print summary
            print(f"{date} | {sess_name}")
            print(f"  ES 15m:   {es_bias}")
            print(f"  NQ 15m:   {nq_bias}")
            print(f"  Combined: {combined}")
            print()

    report = pd.DataFrame(rows)
    report.to_csv(output_csv, index=False)
    print(f"\nReport saved to: {output_csv}")
    return report


# ── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    es = sys.argv[1] if len(sys.argv) > 1 else ES_CSV
    nq = sys.argv[2] if len(sys.argv) > 2 else NQ_CSV
    run_diagnostic(es, nq)
