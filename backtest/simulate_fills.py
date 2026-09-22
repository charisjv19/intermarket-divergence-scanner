"""
Piece 2 — fill simulator.

Walks real 1-min OHLC from each signal and records which level is
touched first (bar high/low, not close). Does not use target_R.

Outcomes:
  win                  — take_profit touched first
  loss                 — stop_loss touched first
  no_impulse_exit      — SMT_IN_FVG: no directional FVG within 7 bars of entry
  no_fill_never_traded — FVG_AFTER_SMT limit never reached the price
  no_fill_timeout      — limit unfilled 20 minutes after fvg_bar
  no_fill_50pct        — price ran 50% of the way to TP before the limit filled
  no_fill_by_eod       — filled, but neither SL nor TP by flatten time

Same-bar SL and TP: stop first (conservative). Session windows match
the scanner: NY Morning 8:00–10:30 ET, NY Afternoon 1:00–3:00 PM ET.
NY Morning trades entered after 09:45 ET flatten at 11:00, not 10:30.
Afternoon flatten stays 15:00. FVG_AFTER_SMT cancels: Juliana 8.7 notes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, time
from pathlib import Path
from typing import Optional, Union
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
SESSIONS = [("NY Morning", 8, 0, 10, 30), ("NY Afternoon", 13, 0, 15, 0)]
LIMIT_TYPES = {"FVG_AFTER_SMT"}
LIMIT_FILL_MINUTES = 20
LIMIT_CANCEL_TP_FRAC = 0.5
MORNING_EXTEND_AFTER = time(9, 45)
MORNING_EXTEND_END = time(11, 0)
# Experimental SMT_IN_FVG post-entry management (not a scanner version).
# Same 3-bar contiguous-window + session-gap guards as find_fvg(); size
# bounds match the scanner. No swept-mid and no fvg_in_session cutoff —
# the trade is already live. 0 disables.
IMPULSE_CONFIRM_BARS = 7
ES_FVG_MIN = 0.5
ES_FVG_MAX = 25.0
NQ_FVG_MIN = 3.0
NQ_FVG_MAX = 150.0
OUTPUT_COLS = ["outcome", "exit_price", "exit_time", "realized_R"]
IMPULSE_COLS = ["impulse_fvg_found", "impulse_fvg_time"]

GAP_DISCLOSURE = (
    "Fill-simulator known gaps (must disclose on every report):\n"
    "  - partial exits NOT simulated (no codified rule exists)\n"
    "  - concurrent-trade limits NOT enforced (policy undecided)\n"
    "  - 5m-opposing-SMT blocking NOT implemented (evidence-gathering stage)"
)


def _to_utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(ET)
    return t.tz_convert("UTC")


def _to_et(ts) -> pd.Timestamp:
    return _to_utc(ts).tz_convert(ET)


def prepare_bars(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    rename = {c: c.lower() for c in ("Open", "High", "Low", "Close", "Time") if c in out.columns}
    if rename:
        out = out.rename(columns=rename)
    if "time" not in out.columns:
        raise ValueError("bars need a time column")
    out["time"] = pd.to_datetime(out["time"], utc=True)
    out = out.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    out["et"] = out["time"].dt.tz_convert(ET)
    return out


def session_end_utc(session: str, when) -> pd.Timestamp:
    """Normal session close: NY Morning 10:30, NY Afternoon 15:00. No 11:00 here."""
    et = _to_et(when)
    d = et.date()
    for name, _sh, _sm, eh, em in SESSIONS:
        if name == session:
            end = datetime.combine(d, time(eh, em), tzinfo=ET)
            return pd.Timestamp(end).tz_convert("UTC")
    # unknown session label: end at afternoon close that day
    end = datetime.combine(d, time(15, 0), tzinfo=ET)
    return pd.Timestamp(end).tz_convert("UTC")


def flatten_end_utc(session: str, entry_when) -> pd.Timestamp:
    """Flatten time for a filled trade.

    NY Morning entered after 09:45 ET → 11:00 that day.
    Does not apply to NY Afternoon (always 15:00).
    """
    et = _to_et(entry_when)
    if session == "NY Morning" and et.time() > MORNING_EXTEND_AFTER:
        end = datetime.combine(et.date(), MORNING_EXTEND_END, tzinfo=ET)
        return pd.Timestamp(end).tz_convert("UTC")
    return session_end_utc(session, entry_when)


def _risk(entry: float, stop: float) -> float:
    return abs(float(entry) - float(stop))


def _r(entry: float, stop: float, exit_px: float, direction: str) -> float:
    risk = _risk(entry, stop)
    if risk == 0:
        return float("nan")
    if direction == "LONG":
        return (float(exit_px) - float(entry)) / risk
    return (float(entry) - float(exit_px)) / risk


def _limit_filled(direction: str, entry: float, high: float, low: float) -> bool:
    if direction == "LONG":
        return float(low) <= float(entry)
    return float(high) >= float(entry)


def _ran_50pct_to_tp(direction: str, entry: float, tp: float, high: float, low: float) -> bool:
    """True if price reached 50% of the entry→TP distance (high/low, not close)."""
    mid = float(entry) + LIMIT_CANCEL_TP_FRAC * (float(tp) - float(entry))
    if direction == "LONG":
        return float(high) >= mid
    return float(low) <= mid


def _fvg_size_bounds(instrument: str) -> tuple[float, float]:
    inst = str(instrument).upper()
    if inst in {"MNQ", "NQ"}:
        return NQ_FVG_MIN, NQ_FVG_MAX
    return ES_FVG_MIN, ES_FVG_MAX


def _fvg_window_is_contiguous(b0, b1, b2) -> bool:
    """True iff the three bars are consecutive 1-minute clocks (session-gap guard)."""
    t0 = pd.Timestamp(b0["time"])
    t1 = pd.Timestamp(b1["time"])
    t2 = pd.Timestamp(b2["time"])
    g01 = (t1 - t0).total_seconds() / 60.0
    g12 = (t2 - t1).total_seconds() / 60.0
    return g01 == 1.0 and g12 == 1.0


def _directional_impulse_fvg(direction: str, b0, b1, b2, instrument: str) -> bool:
    """Trade-direction 3-bar FVG. LONG: prev high < next low; SHORT: prev low > next high."""
    if not _fvg_window_is_contiguous(b0, b1, b2):
        return False
    ph, pl = float(b0["high"]), float(b0["low"])
    nh, nl = float(b2["high"]), float(b2["low"])
    fvg_min, fvg_max = _fvg_size_bounds(instrument)
    if direction == "LONG" and ph < nl:
        size = round(nl - ph, 2)
        return fvg_min <= size <= fvg_max
    if direction == "SHORT" and pl > nh:
        size = round(pl - nh, 2)
        return fvg_min <= size <= fvg_max
    return False


def _touch_on_bar(direction: str, entry: float, stop: float, tp: float, o, h, l) -> Optional[str]:
    """Return 'loss', 'win', or None. Stop wins if both levels trade in the bar."""
    o, h, l = float(o), float(h), float(l)
    stop, tp = float(stop), float(tp)
    if direction == "LONG":
        stop_hit = l <= stop
        tp_hit = h >= tp
        if o <= stop:
            return "loss"
        if o >= tp:
            return "win"
        if stop_hit and tp_hit:
            return "loss"
        if stop_hit:
            return "loss"
        if tp_hit:
            return "win"
        return None
    stop_hit = h >= stop
    tp_hit = l <= tp
    if o >= stop:
        return "loss"
    if o <= tp:
        return "win"
    if stop_hit and tp_hit:
        return "loss"
    if stop_hit:
        return "loss"
    if tp_hit:
        return "win"
    return None


def _empty_result(outcome: str, path: Optional[list] = None) -> dict:
    return {
        "outcome": outcome,
        "exit_price": pd.NA,
        "exit_time": pd.NaT,
        "realized_R": float("nan"),
        "path": path or [],
        "impulse_fvg_found": pd.NA,
        "impulse_fvg_time": pd.NaT,
    }


def _bar_step(bar, action: str) -> dict:
    et = bar["et"] if "et" in bar.index else _to_et(bar["time"])
    return {
        "time": bar["time"],
        "et": et,
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "action": action,
    }


def _hit_result(
    hit: str,
    entry: float,
    stop: float,
    tp: float,
    direction: str,
    bar,
    path: list,
    *,
    impulse_fvg_found=pd.NA,
    impulse_fvg_time=pd.NaT,
) -> dict:
    exit_px = stop if hit == "loss" else tp
    path.append(_bar_step(bar, "stop" if hit == "loss" else "target"))
    return {
        "outcome": hit,
        "exit_price": exit_px,
        "exit_time": bar["time"],
        "realized_R": _r(entry, stop, exit_px, direction),
        "path": path,
        "impulse_fvg_found": impulse_fvg_found,
        "impulse_fvg_time": impulse_fvg_time,
    }


def simulate_one(
    row: pd.Series,
    bars: pd.DataFrame,
    *,
    impulse_confirm_bars: Optional[int] = IMPULSE_CONFIRM_BARS,
) -> dict:
    required = ("entry_price", "stop_loss", "take_profit", "direction")
    missing = [c for c in required if c not in row.index or pd.isna(row[c])]
    if missing:
        raise ValueError(f"signal missing {missing}; need v8.8 entry_price/stop_loss/take_profit")

    entry = float(row["entry_price"])
    stop = float(row["stop_loss"])
    tp = float(row["take_profit"])
    direction = str(row["direction"]).upper()
    entry_type = str(row["entry_type"]) if "entry_type" in row.index and pd.notna(row["entry_type"]) else ""
    instrument = str(row["instrument"]) if "instrument" in row.index and pd.notna(row.get("instrument")) else "ES"
    is_limit = entry_type in LIMIT_TYPES
    impulse_n = 0 if is_limit else (int(impulse_confirm_bars) if impulse_confirm_bars else 0)

    start_raw = row["fvg_bar"] if is_limit and "fvg_bar" in row.index and pd.notna(row.get("fvg_bar")) else row.get("smt_time")
    if start_raw is None or pd.isna(start_raw):
        raise ValueError("signal needs smt_time (and fvg_bar for FVG_AFTER_SMT when present)")
    start = _to_utc(start_raw)
    session = row["session"] if "session" in row.index and pd.notna(row.get("session")) else ""
    session_end = session_end_utc(session, start_raw)

    wait = bars[(bars["time"] >= start) & (bars["time"] < session_end)].reset_index(drop=True)
    if wait.empty:
        return _empty_result("no_fill_never_traded" if is_limit else "no_fill_by_eod")

    path: list[dict] = []
    filled = not is_limit
    fill_bar = wait.iloc[0] if filled else None

    if is_limit:
        deadline = start + pd.Timedelta(minutes=LIMIT_FILL_MINUTES)
        for _, bar in wait.iterrows():
            if bar["time"] >= deadline:
                path.append(_bar_step(bar, "cancel_timeout"))
                return _empty_result("no_fill_timeout", path)
            if _limit_filled(direction, entry, bar["high"], bar["low"]):
                filled = True
                fill_bar = bar
                path.append(_bar_step(bar, "limit_fill"))
                break
            if _ran_50pct_to_tp(direction, entry, tp, bar["high"], bar["low"]):
                path.append(_bar_step(bar, "cancel_50pct"))
                return _empty_result("no_fill_50pct", path)
            path.append(_bar_step(bar, "waiting_limit"))
        if not filled:
            return _empty_result("no_fill_never_traded", path)
        hit = _touch_on_bar(direction, entry, stop, tp, fill_bar["open"], fill_bar["high"], fill_bar["low"])
        if hit:
            path[-1] = _bar_step(fill_bar, "limit_fill_then_stop" if hit == "loss" else "limit_fill_then_target")
            exit_px = stop if hit == "loss" else tp
            return {
                "outcome": hit,
                "exit_price": exit_px,
                "exit_time": fill_bar["time"],
                "realized_R": _r(entry, stop, exit_px, direction),
                "path": path,
                "impulse_fvg_found": pd.NA,
                "impulse_fvg_time": pd.NaT,
            }
    else:
        # SMT_IN_FVG fills at the confirmation-bar CLOSE. That bar's high/low
        # already printed before the close, so SL/TP start on the NEXT bar.
        path.append(_bar_step(fill_bar, "market_fill_at_close"))

    flatten_end = flatten_end_utc(session, fill_bar["time"])
    scan = bars[(bars["time"] > fill_bar["time"]) & (bars["time"] < flatten_end)].reset_index(drop=True)

    impulse_needed = impulse_n > 0
    impulse_window = [fill_bar] if impulse_needed else []
    impulse_confirmed = False
    impulse_fvg_found = pd.NA if not impulse_needed else False
    impulse_fvg_time = pd.NaT

    last = None
    for _, bar in scan.iterrows():
        last = bar
        if impulse_needed and not impulse_confirmed:
            impulse_window.append(bar)
            if len(impulse_window) >= 3:
                w0, w1, w2 = impulse_window[-3], impulse_window[-2], impulse_window[-1]
                if _directional_impulse_fvg(direction, w0, w1, w2, instrument):
                    impulse_confirmed = True
                    impulse_fvg_found = True
                    impulse_fvg_time = bar["time"]
            if len(impulse_window) >= impulse_n and not impulse_confirmed:
                close = float(bar["close"])
                path.append(_bar_step(bar, "no_impulse_exit"))
                return {
                    "outcome": "no_impulse_exit",
                    "exit_price": close,
                    "exit_time": bar["time"],
                    "realized_R": _r(entry, stop, close, direction),
                    "path": path,
                    "impulse_fvg_found": False,
                    "impulse_fvg_time": pd.NaT,
                }
        hit = _touch_on_bar(direction, entry, stop, tp, bar["open"], bar["high"], bar["low"])
        if hit:
            return _hit_result(
                hit, entry, stop, tp, direction, bar, path,
                impulse_fvg_found=impulse_fvg_found,
                impulse_fvg_time=impulse_fvg_time,
            )
        action = "impulse_fvg" if (impulse_confirmed and impulse_fvg_time == bar["time"]) else "open"
        path.append(_bar_step(bar, action))

    if last is None:
        last = fill_bar
    close = float(last["close"])
    if not path or path[-1]["time"] != last["time"] or path[-1]["action"] in {"open", "impulse_fvg"}:
        if path and path[-1]["time"] == last["time"] and path[-1]["action"] in {"open", "impulse_fvg"}:
            path[-1]["action"] = "flatten_eod"
        else:
            path.append(_bar_step(last, "flatten_eod"))
    return {
        "outcome": "no_fill_by_eod",
        "exit_price": close,
        "exit_time": last["time"],
        "realized_R": _r(entry, stop, close, direction),
        "path": path,
        "impulse_fvg_found": impulse_fvg_found,
        "impulse_fvg_time": impulse_fvg_time,
    }


def _bars_for_instrument(instrument: str, bars, es_bars, nq_bars) -> pd.DataFrame:
    inst = str(instrument).upper()
    if inst in {"MES", "ES"}:
        src = es_bars if es_bars is not None else bars
    elif inst in {"MNQ", "NQ"}:
        src = nq_bars if nq_bars is not None else bars
    else:
        src = bars
    if src is None:
        raise ValueError(f"no bars provided for instrument {instrument}")
    return src if "et" in src.columns else prepare_bars(src)


def simulate_fills(
    signals: Union[pd.DataFrame, str, Path],
    bars: Optional[pd.DataFrame] = None,
    *,
    es_bars: Optional[pd.DataFrame] = None,
    nq_bars: Optional[pd.DataFrame] = None,
    impulse_confirm_bars: Optional[int] = IMPULSE_CONFIRM_BARS,
) -> pd.DataFrame:
    """Append outcome, exit_price, exit_time, realized_R. Walkes high/low, not target_R."""
    sigs = pd.read_csv(signals) if not isinstance(signals, pd.DataFrame) else signals.copy()
    if bars is not None:
        bars = prepare_bars(bars)
    if es_bars is not None:
        es_bars = prepare_bars(es_bars)
    if nq_bars is not None:
        nq_bars = prepare_bars(nq_bars)

    rows = []
    for _, row in sigs.iterrows():
        inst = row["instrument"] if "instrument" in row.index else "ES"
        b = _bars_for_instrument(inst, bars, es_bars, nq_bars)
        rows.append(simulate_one(row, b, impulse_confirm_bars=impulse_confirm_bars))
    extra = pd.DataFrame(rows, index=sigs.index)
    for col in OUTPUT_COLS + IMPULSE_COLS:
        if col in extra.columns:
            sigs[col] = extra[col]
    return sigs


def format_path(path: list) -> str:
    """One line per bar: ET time  O/H/L/C  action."""
    lines = []
    for step in path:
        et = pd.Timestamp(step["et"]).tz_convert(ET)
        lines.append(
            f"  {et.strftime('%Y-%m-%d %H:%M %Z')}  "
            f"O={step['open']:.2f} H={step['high']:.2f} "
            f"L={step['low']:.2f} C={step['close']:.2f}  {step['action']}"
        )
    return "\n".join(lines) if lines else "  (no bars in session window)"


def spot_check_rows(filled: pd.DataFrame, n: int = 10, seed: int = 42) -> pd.DataFrame:
    """Stratified random sample across resolved and no-fill outcomes."""
    parts = []
    outcomes = [
        "win",
        "loss",
        "no_fill_never_traded",
        "no_fill_timeout",
        "no_fill_50pct",
        "no_fill_by_eod",
        "no_impulse_exit",
    ]
    present = [o for o in outcomes if (filled["outcome"] == o).any()]
    if not present:
        return filled.head(0)
    per = max(1, n // len(present))
    leftover = n
    for o in present:
        grp = filled[filled["outcome"] == o]
        take = min(len(grp), per, leftover)
        if take:
            parts.append(grp.sample(n=take, random_state=seed))
            leftover -= take
    if leftover > 0:
        used = pd.concat(parts).index if parts else []
        rest = filled.drop(index=used, errors="ignore")
        if len(rest):
            parts.append(rest.sample(n=min(leftover, len(rest)), random_state=seed))
    out = pd.concat(parts) if parts else filled.head(0)
    return out.sample(frac=1, random_state=seed).head(n)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Simulate fills on 1-min bars for a signal CSV.")
    p.add_argument("--signals", required=True)
    p.add_argument("--bars", help="Single-instrument OHLC CSV")
    p.add_argument("--es-bars")
    p.add_argument("--nq-bars")
    p.add_argument("--out", required=True)
    p.add_argument("--spot-check", type=int, default=0, help="Print N stratified traces")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--spot-out", help="Write spot-check traces to this text file")
    p.add_argument(
        "--impulse-confirm-bars",
        type=int,
        default=IMPULSE_CONFIRM_BARS,
        help="SMT_IN_FVG: flatten at Nth bar close if no directional FVG. 0 disables.",
    )
    args = p.parse_args(argv)
    es = pd.read_csv(args.es_bars) if args.es_bars else None
    nq = pd.read_csv(args.nq_bars) if args.nq_bars else None
    bars = pd.read_csv(args.bars) if args.bars else None
    sigs = pd.read_csv(args.signals)
    out = simulate_fills(
        sigs, bars, es_bars=es, nq_bars=nq, impulse_confirm_bars=args.impulse_confirm_bars
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(out["outcome"].value_counts().to_string())
    print(GAP_DISCLOSURE)
    if args.spot_check:
        sample = spot_check_rows(out, n=args.spot_check, seed=args.seed)
        traces = []
        es_p = prepare_bars(es) if es is not None else None
        nq_p = prepare_bars(nq) if nq is not None else None
        bars_p = prepare_bars(bars) if bars is not None else None
        for idx, row in sample.iterrows():
            b = _bars_for_instrument(row.get("instrument", "ES"), bars_p, es_p, nq_p)
            traced = simulate_one(row, b, impulse_confirm_bars=args.impulse_confirm_bars)
            traces.append(_format_spot(idx, row, traced))
        text = "\n\n".join(traces)
        print("\n" + text)
        if args.spot_out:
            Path(args.spot_out).write_text(text + "\n")
    return 0


def _format_spot(idx, row, traced: dict) -> str:
    r = traced["realized_R"]
    r_s = "nan" if pd.isna(r) else f"{float(r):.3f}"
    exit_t = traced["exit_time"]
    exit_s = "" if pd.isna(exit_t) else str(pd.Timestamp(exit_t).tz_convert(ET))
    exit_px = traced["exit_price"]
    px_s = "" if pd.isna(exit_px) else f"{float(exit_px):.2f}"
    header = (
        f"#{idx} {row.get('smt_time')} {row.get('session')} "
        f"{row.get('direction')} {row.get('instrument')} {row.get('entry_type')}\n"
        f"  entry={float(row['entry_price']):.2f}  stop={float(row['stop_loss']):.2f}  "
        f"target={float(row['take_profit']):.2f}\n"
        f"  outcome={traced['outcome']}  exit_price={px_s}  exit_time={exit_s}  realized_R={r_s}"
    )
    return header + "\n" + format_path(traced.get("path") or [])


if __name__ == "__main__":
    raise SystemExit(main())
