"""
v8.7 vs v8.8 identity categorization, v8.7 reconstruction, fill-sim P&L.

Identity: (date, sw2_conf_time, direction, instrument) via identity_key().
Categories: UNCHANGED, CHANGED, REMOVED, ADDED.

P&L is v8.7-reconstructed-simulated vs v8.8-actual-simulated realized_R.
v8.7 never stored entry/SL/TP; reconstruction is labeled as a replica.
"""

from __future__ import annotations

import argparse
import math
from datetime import time
from pathlib import Path
from typing import Optional, Union
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.compare_versions import identity_key, index_by_identity
from backtest.pnl_report import compute_stats
from backtest.reconstruct_v87 import (
    RECONSTRUCTION_LABEL,
    STOP_RULE_FLAG,
    reconstruct_signals,
    simulate_reconstructed_v87,
)
from backtest.simulate_fills import (
    GAP_DISCLOSURE,
    prepare_bars,
    simulate_fills,
)

ET = ZoneInfo("America/New_York")
SESSIONS = [("NY Morning", 8, 0, 10, 30), ("NY Afternoon", 13, 0, 15, 0)]
SESSION_CUTOFF_MINS = 30
PRE_SESSION_LOOKBACK_MINS = 30
MAX_CONF_GAP_MINS = 5
S3_TOL_MINS = 2

CATEGORIES = ("UNCHANGED", "CHANGED", "REMOVED", "ADDED")

CSV_COLS = [
    "category",
    "date",
    "sw2_conf_time",
    "direction",
    "instrument",
    "session",
    "entry_type_v87",
    "entry_type_v88",
    "cause",
    "cause_detail",
    "smt_time_v87",
    "smt_time_v88",
    "fvg_bar_v87",
    "fvg_bar_v88",
    "pre_fvg_bar_v87",
    "pre_fvg_bar_v88",
]

NO_POS = ("no_fill_never_traded", "no_fill_timeout", "no_fill_50pct")
HAS_R = ("win", "loss", "no_fill_by_eod")


def _to_et(ts) -> Optional[pd.Timestamp]:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)) or pd.isna(ts):
        return None
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(ET)
    return t.tz_convert(ET)


def _show_time(ts) -> str:
    et = _to_et(ts)
    if et is None:
        return ""
    return et.isoformat(sep=" ", timespec="seconds")


def classify_bar(hour: int, minute: int):
    t = hour * 60 + minute
    for name, sh, sm, eh, em in SESSIONS:
        ss = sh * 60 + sm
        se = eh * 60 + em
        ps = ss - PRE_SESSION_LOOKBACK_MINS
        ce = se - SESSION_CUTOFF_MINS
        if ss <= t < ce:
            return "session", name
        if ps <= t < ss:
            return "pre_session", name
    return None, None


def session_name_of(ts) -> Optional[str]:
    et = _to_et(ts)
    if et is None:
        return None
    return classify_bar(et.hour, et.minute)[1]


def bar_kind_of(ts) -> Optional[str]:
    et = _to_et(ts)
    if et is None:
        return None
    return classify_bar(et.hour, et.minute)[0]


def _parse_sw_time(value, date_hint=None) -> Optional[pd.Timestamp]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if s in {"", "nan", "NaT", "None"}:
        return None
    t = pd.Timestamp(s)
    if t.tzinfo is None:
        if t.year < 2000 and date_hint is not None:
            d = pd.Timestamp(date_hint)
            t = t.replace(year=d.year, month=d.month, day=d.day)
        t = t.tz_localize(ET)
    return t.tz_convert(ET)


def confirming_failing_sw1(row: pd.Series) -> tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    inst = str(row["instrument"]).upper()
    date = row["date"] if "date" in row.index else None
    es1 = _parse_sw_time(row["es_sw1_time"] if "es_sw1_time" in row.index else None, date)
    nq1 = _parse_sw_time(row["nq_sw1_time"] if "nq_sw1_time" in row.index else None, date)
    if inst in {"ES", "MES"}:
        return es1, nq1
    return nq1, es1


def sw1_gap_minutes(row: pd.Series) -> Optional[float]:
    conf, fail = confirming_failing_sw1(row)
    if conf is None or fail is None:
        return None
    return abs((conf - fail).total_seconds()) / 60.0


def sw1_cross_session(row: pd.Series) -> bool:
    """True when a same-day sw1 lives in a different session than sw2 (S1)."""
    date = row["date"] if "date" in row.index else None
    sw2 = _to_et(row["sw2_conf_time"])
    sw2_sess = session_name_of(sw2)
    if sw2_sess is None:
        return False
    for col in ("es_sw1_time", "nq_sw1_time"):
        if col not in row.index:
            continue
        t = _parse_sw_time(row[col], date)
        if t is None or sw2 is None:
            continue
        if t.date() != sw2.date():
            continue
        s = session_name_of(t)
        if s is not None and s != sw2_sess:
            return True
        if s is None and t.date() == sw2.date():
            # same calendar day, outside even pre-session of sw2's session
            other = session_name_of(t)
            if other != sw2_sess:
                return True
    return False


def build_sdf_times(bars: pd.DataFrame) -> list[pd.Timestamp]:
    prepared = bars if "et" in bars.columns else prepare_bars(bars)
    times = []
    for et in prepared["et"]:
        et = _to_et(et)
        if et is None:
            continue
        kind, _ = classify_bar(et.hour, et.minute)
        if kind is not None:
            times.append(et.floor("min"))
    return sorted(set(times))


def _next_sdf(sdf_times: list[pd.Timestamp], when) -> Optional[pd.Timestamp]:
    et = _to_et(when)
    if et is None or not sdf_times:
        return None
    et = et.floor("min")
    for t in sdf_times:
        if t > et:
            return t
    return None


def confirmation_gate_hit(row: pd.Series, sdf_times: Optional[list[pd.Timestamp]]) -> tuple[bool, str]:
    sw2 = _to_et(row["sw2_conf_time"])
    if sw2 is None:
        return False, ""
    if sdf_times:
        nxt = _next_sdf(sdf_times, sw2)
        if nxt is None:
            return True, "no sdf bar after sw2_conf_time"
        gap = (nxt - sw2.floor("min")).total_seconds() / 60.0
        if gap <= 0 or gap > MAX_CONF_GAP_MINS:
            return True, f"sw2→next_sdf gap {gap:.0f}m > {MAX_CONF_GAP_MINS}m (next={_show_time(nxt)})"
        return False, ""
    # no tape: last session-window bar (09:59 / 14:29) cannot confirm in-session
    kind, sess = classify_bar(sw2.hour, sw2.minute)
    if kind != "session":
        return False, ""
    for name, sh, sm, eh, em in SESSIONS:
        if name != sess:
            continue
        last = time(eh, em)
        # last sdf session bar is cutoff-1 minute
        ce_min = eh * 60 + em - SESSION_CUTOFF_MINS - 1
        if sw2.hour * 60 + sw2.minute == ce_min:
            return True, f"sw2_conf_time is last sdf session bar ({name} cutoff)"
    return False, ""


def fvg_window_phantom(ts, sdf_times: Optional[list[pd.Timestamp]]) -> bool:
    et = _to_et(ts)
    if et is None:
        return False
    et = et.floor("min")
    prev = et - pd.Timedelta(minutes=1)
    nxt = et + pd.Timedelta(minutes=1)
    if sdf_times:
        sdf_set = set(sdf_times)
        return not (prev in sdf_set and et in sdf_set and nxt in sdf_set)
    # without tape: FVG whose 3-bar window would cross the morning/afternoon sdf gap
    kind, _ = classify_bar(et.hour, et.minute)
    if kind is None:
        return True
    prev_kind, _ = classify_bar(prev.hour, prev.minute)
    nxt_kind, _ = classify_bar(nxt.hour, nxt.minute)
    return prev_kind is None or nxt_kind is None


def _fvg_in_cutoff(ts) -> bool:
    et = _to_et(ts)
    if et is None:
        return False
    t = et.hour * 60 + et.minute
    for _name, sh, sm, eh, em in SESSIONS:
        se = eh * 60 + em
        ce = se - SESSION_CUTOFF_MINS
        if ce <= t < se:
            return True
    return False


def _truthy_fvg_found(row: pd.Series) -> bool:
    if "fvg_found" in row.index and pd.notna(row["fvg_found"]):
        v = row["fvg_found"]
        if isinstance(v, str):
            return v.strip().lower() in {"true", "1", "yes"}
        return bool(v)
    if "fvg_bar" in row.index and pd.notna(row["fvg_bar"]):
        return True
    return False


def _pre_in_pre_session(row: pd.Series) -> bool:
    ts = row["pre_fvg_bar"] if "pre_fvg_bar" in row.index else None
    return bar_kind_of(ts) == "pre_session"


def _straddle_15m(sw2) -> bool:
    et = _to_et(sw2)
    if et is None:
        return False
    return et.minute % 15 == 14


def cause_removed(v87: pd.Series, sdf_times: Optional[list[pd.Timestamp]]) -> tuple[str, str]:
    details = []
    gap = sw1_gap_minutes(v87)
    s1 = sw1_cross_session(v87)
    s3 = gap is not None and 0 < gap <= S3_TOL_MINS + 1e-9
    gate, gate_d = confirmation_gate_hit(v87, sdf_times)
    phantom = fvg_window_phantom(v87["fvg_bar"] if "fvg_bar" in v87.index else None, sdf_times)
    pre_ph = fvg_window_phantom(v87["pre_fvg_bar"] if "pre_fvg_bar" in v87.index else None, sdf_times)
    pre_sess = _pre_in_pre_session(v87)
    cutoff = _fvg_in_cutoff(v87["fvg_bar"] if "fvg_bar" in v87.index else None)
    fix3 = _straddle_15m(v87["sw2_conf_time"])

    if s1:
        details.append("sw1 session != sw2 session")
    if s3:
        details.append(f"confirming vs failing sw1 {gap:.0f}m apart (v8.7 tol=2, v8.8 tol=0)")
    if gate:
        details.append(gate_d or "confirmation bar not contiguous")
    if phantom:
        details.append("post-SMT fvg_bar 3-bar window is not consecutive 1m sdf bars")
    if pre_ph:
        details.append("pre_fvg_bar 3-bar window is not consecutive 1m sdf bars")
    if pre_sess:
        details.append("pre_fvg_bar is in pre_session")
    if cutoff:
        details.append("fvg_bar sits in SESSION_CUTOFF window (10:00–10:30 / 14:30–15:00)")
    if fix3:
        details.append("sw2 is last minute of a 15m bucket (Fix 3 clock can flip 15m/5m)")

    if s1:
        return "s1_session_filter", "; ".join(details)
    if s3:
        return "s3_tolerance", "; ".join(details)
    if gate:
        return "confirmation_gate", "; ".join(details)
    if phantom or pre_ph:
        return "phantom_fvg_session_gap", "; ".join(details)
    if cutoff:
        return "session_cutoff_fvg", "; ".join(details)
    if pre_sess:
        return "pre_session_membership", "; ".join(details)
    if fix3:
        return "fix3_confluence_clock", "; ".join(details)
    return "no_clear_cause", "; ".join(details) if details else "does not match a named v8.8 fix heuristic"


def cause_added(v88: pd.Series, sdf_times: Optional[list[pd.Timestamp]]) -> tuple[str, str]:
    details = []
    etype = str(v88["entry_type"]) if "entry_type" in v88.index and pd.notna(v88["entry_type"]) else ""
    has_fvg = _truthy_fvg_found(v88)
    pre_sess = _pre_in_pre_session(v88)
    pre_ph = fvg_window_phantom(v88["pre_fvg_bar"] if "pre_fvg_bar" in v88.index else None, sdf_times)
    fix3 = _straddle_15m(v88["sw2_conf_time"])
    if etype == "SMT_IN_FVG" and not has_fvg:
        details.append("v8.8 SMT_IN_FVG does not require a post-SMT FVG; v8.7 dropped setups with no find_fvg()")
    if etype == "SMT_IN_FVG":
        details.append("v8.8 membership/entry is confirmation-bar close (sw2+1), not v8.7 scan-i close")
    if pre_sess:
        details.append("pre_fvg_bar is in pre_session")
    if pre_ph:
        details.append("pre_fvg 3-bar would have been a session-gap phantom in v8.7 indexing")
    if fix3:
        details.append("sw2 is last minute of a 15m bucket (Fix 3 evaluates 15m/5m at confirmation clock)")

    if etype == "SMT_IN_FVG" and not has_fvg:
        return "s2_smt_in_fvg_no_post_fvg", "; ".join(details)
    if etype == "SMT_IN_FVG":
        return "confirmation_gate_rescope", "; ".join(details)
    if pre_sess:
        return "pre_session_membership", "; ".join(details)
    if fix3:
        return "fix3_confluence_clock", "; ".join(details)
    return "no_clear_cause", "; ".join(details) if details else "does not match a named v8.8 fix heuristic"


def cause_changed(v87: pd.Series, v88: pd.Series, sdf_times: Optional[list[pd.Timestamp]]) -> tuple[str, str]:
    a = str(v87["entry_type"])
    b = str(v88["entry_type"])
    details = [f"{a} → {b}"]
    pre_sess = _pre_in_pre_session(v87) or _pre_in_pre_session(v88)
    pre_ph = fvg_window_phantom(v87["pre_fvg_bar"] if "pre_fvg_bar" in v87.index else None, sdf_times)
    if a == "SMT_IN_FVG" and b == "FVG_AFTER_SMT":
        details.append("v8.8 did not confirm SMT_IN_FVG at confirmation-bar close; fell through to post-SMT FVG")
        if pre_ph:
            details.append("v8.7 pre_fvg 3-bar was a session-gap phantom")
        if pre_sess:
            details.append("v8.7 pre_fvg was pre_session")
        if pre_ph:
            return "phantom_fvg_session_gap", "; ".join(details)
        if pre_sess:
            return "pre_session_membership", "; ".join(details)
        return "confirmation_gate_rescope", "; ".join(details)
    if a == "FVG_AFTER_SMT" and b == "SMT_IN_FVG":
        details.append("v8.8 found pre-FVG membership at confirmation close; v8.7 scan-i close did not tag it")
        if pre_sess:
            return "pre_session_membership", "; ".join(details)
        return "confirmation_gate_rescope", "; ".join(details)
    return "no_clear_cause", "; ".join(details)


def _cell(row: Optional[pd.Series], col: str):
    if row is None or col not in row.index:
        return pd.NA
    val = row[col]
    if col.endswith("_time") or col.endswith("_bar") or col == "sw2_conf_time":
        shown = _show_time(val)
        return shown if shown else pd.NA
    return val


def _category_row(
    category: str,
    key: tuple,
    v87: Optional[pd.Series],
    v88: Optional[pd.Series],
    cause: str,
    detail: str,
) -> dict:
    date, _id_sw2, direction, instrument = key
    src = v88 if v88 is not None else v87
    sw2_show = _show_time(src["sw2_conf_time"]) if src is not None else ""
    session = ""
    if src is not None and "session" in src.index and pd.notna(src["session"]):
        session = src["session"]
    return {
        "category": category,
        "date": date,
        "sw2_conf_time": sw2_show,
        "direction": direction,
        "instrument": instrument,
        "session": session,
        "entry_type_v87": _cell(v87, "entry_type"),
        "entry_type_v88": _cell(v88, "entry_type"),
        "cause": cause,
        "cause_detail": detail,
        "smt_time_v87": _cell(v87, "smt_time"),
        "smt_time_v88": _cell(v88, "smt_time"),
        "fvg_bar_v87": _cell(v87, "fvg_bar"),
        "fvg_bar_v88": _cell(v88, "fvg_bar"),
        "pre_fvg_bar_v87": _cell(v87, "pre_fvg_bar"),
        "pre_fvg_bar_v88": _cell(v88, "pre_fvg_bar"),
    }


def categorize(
    v87: pd.DataFrame,
    v88: pd.DataFrame,
    *,
    sdf_times: Optional[list[pd.Timestamp]] = None,
) -> pd.DataFrame:
    left = index_by_identity(v87)
    right = index_by_identity(v88)
    rows = []
    for key in sorted(set(left) & set(right)):
        a, b = left[key], right[key]
        ta = str(a["entry_type"]) if "entry_type" in a.index else ""
        tb = str(b["entry_type"]) if "entry_type" in b.index else ""
        if ta == tb:
            rows.append(_category_row("UNCHANGED", key, a, b, "same_entry_type", "matched identity, same entry_type"))
        else:
            cause, detail = cause_changed(a, b, sdf_times)
            rows.append(_category_row("CHANGED", key, a, b, cause, detail))
    for key in sorted(set(left) - set(right)):
        a = left[key]
        cause, detail = cause_removed(a, sdf_times)
        rows.append(_category_row("REMOVED", key, a, None, cause, detail))
    for key in sorted(set(right) - set(left)):
        b = right[key]
        cause, detail = cause_added(b, sdf_times)
        rows.append(_category_row("ADDED", key, None, b, cause, detail))
    if not rows:
        return pd.DataFrame(columns=CSV_COLS)
    out = pd.DataFrame(rows)[CSV_COLS]
    cat_order = {c: i for i, c in enumerate(CATEGORIES)}
    out["_ord"] = out["category"].map(cat_order)
    out = out.sort_values(["_ord", "cause", "date", "sw2_conf_time", "direction", "instrument"]).drop(columns="_ord")
    return out.reset_index(drop=True)


def _r_sum(filled: pd.DataFrame) -> float:
    if filled is None or filled.empty or "realized_R" not in filled.columns:
        return 0.0
    s = pd.to_numeric(filled["realized_R"], errors="coerce")
    return float(s.fillna(0.0).sum())


def _resolved_metrics(filled: pd.DataFrame) -> dict:
    if filled is None or filled.empty:
        empty = compute_stats(pd.DataFrame({"outcome": [], "realized_R": []}), treat_eod_as_zero=False)
        empty["total_R"] = 0.0
        empty["n_signals"] = 0
        empty["n_no_pos"] = 0
        empty["n_eod"] = 0
        return empty
    stats = compute_stats(filled, treat_eod_as_zero=False)
    stats["total_R"] = _r_sum(filled)
    stats["n_signals"] = len(filled)
    stats["n_no_pos"] = int(filled["outcome"].isin(NO_POS).sum()) if "outcome" in filled.columns else 0
    stats["n_eod"] = int((filled["outcome"] == "no_fill_by_eod").sum()) if "outcome" in filled.columns else 0
    return stats


def _fmt_pct(x) -> str:
    if x != x:
        return "nan"
    return f"{100.0 * x:.1f}%"


def _fmt_r(x) -> str:
    if x is None or x != x:
        return "nan"
    if math.isinf(x):
        return "inf"
    return f"{x:+.3f}R"


def _metrics_line(label: str, s: dict) -> str:
    return (
        f"  {label}: n_signals={s['n_signals']}  resolved n={s['n']}  "
        f"wins={s['wins']} losses={s['losses']} eod={s['n_eod']} no_pos={s['n_no_pos']}  "
        f"WR={_fmt_pct(s['win_rate'])}  E={_fmt_r(s['expectancy'])}  total_R={_fmt_r(s['total_R'])}"
    )


def _win_loss(outcome) -> Optional[str]:
    if outcome in {"win", "loss"}:
        return outcome
    return None


def _mismatch_rows(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    ia = index_by_identity(a)
    ib = index_by_identity(b)
    rows = []
    for key in sorted(set(ia) & set(ib)):
        ra, rb = ia[key], ib[key]
        oa, ob = ra.get("outcome"), rb.get("outcome")
        if _win_loss(oa) is None or _win_loss(ob) is None:
            continue
        if oa == ob:
            continue
        date, _, direction, instrument = key
        rows.append(
            {
                "date": date,
                "sw2_conf_time": _show_time(ra["sw2_conf_time"]),
                "direction": direction,
                "instrument": instrument,
                "entry_type": ra.get("entry_type"),
                "outcome_v87": oa,
                "outcome_v88": ob,
                "realized_R_v87": ra.get("realized_R"),
                "realized_R_v88": rb.get("realized_R"),
                "entry_v87": ra.get("entry_price"),
                "entry_v88": rb.get("entry_price"),
                "stop_v87": ra.get("stop_loss"),
                "stop_v88": rb.get("stop_loss"),
                "tp_v87": ra.get("take_profit"),
                "tp_v88": rb.get("take_profit"),
            }
        )
    return pd.DataFrame(rows)


def _table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "  (none)"
    return df.to_string(index=False)


def subset_by_keys(filled: pd.DataFrame, keys: set[tuple]) -> pd.DataFrame:
    if filled.empty:
        return filled.iloc[0:0]
    keep = []
    for _, row in filled.iterrows():
        keep.append(identity_key(row) in keys)
    return filled.loc[keep].copy()


def render_pnl_report(
    *,
    cats: pd.DataFrame,
    filled_v87: pd.DataFrame,
    filled_v88: pd.DataFrame,
    inverted_n: int,
) -> str:
    i87 = index_by_identity(filled_v87)
    i88 = index_by_identity(filled_v88)
    keys = {
        "UNCHANGED": set(),
        "CHANGED": set(),
        "REMOVED": set(),
        "ADDED": set(),
    }
    for _, row in cats.iterrows():
        key = identity_key(row)
        keys[row["category"]].add(key)

    u87 = subset_by_keys(filled_v87, keys["UNCHANGED"])
    u88 = subset_by_keys(filled_v88, keys["UNCHANGED"])
    c87 = subset_by_keys(filled_v87, keys["CHANGED"])
    c88 = subset_by_keys(filled_v88, keys["CHANGED"])
    r87 = subset_by_keys(filled_v87, keys["REMOVED"])
    a88 = subset_by_keys(filled_v88, keys["ADDED"])

    mu = _mismatch_rows(u87, u88)
    mc = _mismatch_rows(c87, c88)

    cause_rm = cats[cats["category"] == "REMOVED"]["cause"].value_counts()
    cause_ad = cats[cats["category"] == "ADDED"]["cause"].value_counts()
    cause_ch = cats[cats["category"] == "CHANGED"]["cause"].value_counts()

    lines = [
        GAP_DISCLOSURE,
        "",
        "=" * 72,
        "PART A/B/C — v8.7 reconstructed vs v8.8 head (Feb–Apr ES/NQ)",
        "=" * 72,
        RECONSTRUCTION_LABEL,
        STOP_RULE_FLAG,
        "",
        f"v8.7 identities: {len(i87)}   v8.8 identities: {len(i88)}",
        f"UNCHANGED={len(keys['UNCHANGED'])}  CHANGED={len(keys['CHANGED'])}  "
        f"REMOVED={len(keys['REMOVED'])}  ADDED={len(keys['ADDED'])}",
        f"reconstructed rows with inverted risk (entry on the wrong side of stop): {inverted_n}",
        "",
        "Cause counts (REMOVED):",
        cause_rm.to_string() if len(cause_rm) else "  (none)",
        "",
        "Cause counts (ADDED):",
        cause_ad.to_string() if len(cause_ad) else "  (none)",
        "",
        "Cause counts (CHANGED / entry_type flip):",
        cause_ch.to_string() if len(cause_ch) else "  (none)",
        "",
        "-" * 72,
        "PART C — P&L by category",
        "realized_R is fill-simulated on ES_feb_apr.csv / NQ_feb_apr.csv.",
        "Win rate and expectancy use resolved win/loss only (eod excluded, no-position excluded).",
        "total_R sums realized_R over win + loss + eod flatten (no-position contributes 0).",
        "",
        "1. UNCHANGED (same identity, same entry_type; different entry mechanism)",
        _metrics_line("v8.7 reconstructed", _resolved_metrics(u87)),
        _metrics_line("v8.8 actual       ", _resolved_metrics(u88)),
        f"  win-vs-loss mismatches: {len(mu)}",
        _table(mu),
        "",
        "2. CHANGED (matched identity, entry_type flip)",
        _metrics_line("v8.7 reconstructed", _resolved_metrics(c87)),
        _metrics_line("v8.8 actual       ", _resolved_metrics(c88)),
        f"  win-vs-loss mismatches: {len(mc)}",
        _table(mc),
        "",
        "3. REMOVED (v8.7 only — P&L given up)",
        _metrics_line("v8.7 reconstructed", _resolved_metrics(r87)),
        "",
        "4. ADDED (v8.8 only — P&L gained)",
        _metrics_line("v8.8 actual       ", _resolved_metrics(a88)),
        "",
        "-" * 72,
        "AGGREGATE (strategy-level shift)",
        _metrics_line("v8.7 reconstructed (all 224)", _resolved_metrics(filled_v87)),
        _metrics_line("v8.8 actual        (all 192)", _resolved_metrics(filled_v88)),
        f"  net total_R (v8.8 − v8.7 reconstructed): "
        f"{_fmt_r(_r_sum(filled_v88) - _r_sum(filled_v87))}",
        "",
        "REMOVED total_R is included in the v8.7 aggregate and absent from v8.8.",
        "ADDED total_R is included in the v8.8 aggregate and absent from v8.7.",
    ]
    return "\n".join(lines) + "\n"


def run_compare(
    *,
    v87_signals: Union[str, Path, pd.DataFrame],
    v88_signals: Union[str, Path, pd.DataFrame],
    es_bars: pd.DataFrame,
    nq_bars: pd.DataFrame,
    out_dir: Union[str, Path],
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    v87 = pd.read_csv(v87_signals) if not isinstance(v87_signals, pd.DataFrame) else v87_signals.copy()
    v88 = pd.read_csv(v88_signals) if not isinstance(v88_signals, pd.DataFrame) else v88_signals.copy()
    es_p = prepare_bars(es_bars)
    nq_p = prepare_bars(nq_bars)
    sdf_times = build_sdf_times(es_p)

    cats = categorize(v87, v88, sdf_times=sdf_times)
    cats_path = out_dir / "v87_v88_categorized_signals.csv"
    cats.to_csv(cats_path, index=False)

    recon = reconstruct_signals(v87)
    recon.to_csv(out_dir / "v87_reconstructed_levels.csv", index=False)
    inverted_n = int(recon["risk_inverted"].sum()) if "risk_inverted" in recon.columns else 0

    filled_v87 = simulate_reconstructed_v87(v87, es_bars=es_p, nq_bars=nq_p)
    filled_v87.to_csv(out_dir / "v87_reconstructed_fills.csv", index=False)

    filled_v88 = simulate_fills(v88, es_bars=es_p, nq_bars=nq_p)
    filled_v88.to_csv(out_dir / "v88_actual_fills.csv", index=False)

    report = render_pnl_report(
        cats=cats,
        filled_v87=filled_v87,
        filled_v88=filled_v88,
        inverted_n=inverted_n,
    )
    report_path = out_dir / "v87_v88_pnl_by_category.txt"
    report_path.write_text(report)
    return {
        "categorized": cats,
        "filled_v87": filled_v87,
        "filled_v88": filled_v88,
        "report": report,
        "out_dir": out_dir,
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Categorize v8.7 vs v8.8 identities, reconstruct v8.7, fill-sim P&L."
    )
    p.add_argument("--v87-signals", required=True)
    p.add_argument("--v88-signals", required=True)
    p.add_argument("--es-bars", required=True)
    p.add_argument("--nq-bars", required=True)
    p.add_argument("--out-dir", required=True)
    args = p.parse_args(argv)
    result = run_compare(
        v87_signals=args.v87_signals,
        v88_signals=args.v88_signals,
        es_bars=pd.read_csv(args.es_bars),
        nq_bars=pd.read_csv(args.nq_bars),
        out_dir=args.out_dir,
    )
    cats = result["categorized"]
    print(cats["category"].value_counts().reindex(CATEGORIES).fillna(0).astype(int).to_string())
    print()
    print(result["report"])
    print(f"wrote {result['out_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
