"""
Piece 4 — version diff.

Run two scanner versions (two files, one file twice, or the same path at
two git refs), simulate fills on both, write ONLY the differences to
review_these.csv.

Identity is the scanner dedup key used for manual comparisons:
  (date, sw2_conf_time, direction, instrument)

Material fields: entry_price, stop_loss, take_profit, entry_type, realized_R.
Unchanged matches are omitted. Does not change scanner logic.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Optional, Union

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest.simulate_fills import prepare_bars, simulate_fills

ET = "America/New_York"
IDENTITY = ("date", "sw2_conf_time", "direction", "instrument")
MATERIAL_NUM = ("entry_price", "stop_loss", "take_profit", "realized_R")
MATERIAL_STR = ("entry_type",)
MATERIAL = MATERIAL_STR + MATERIAL_NUM
SIDE_COLS = (
    "session",
    "smt_time",
    "entry_type",
    "entry_price",
    "stop_loss",
    "take_profit",
    "realized_R",
    "outcome",
)
ABS_TOL = 1e-6

GAP_DISCLOSURE = (
    "Fill-simulator known gaps (must disclose on every report):\n"
    "  - partial exits NOT simulated (no codified rule exists)\n"
    "  - concurrent-trade limits NOT enforced (policy undecided)\n"
    "  - 5m-opposing-SMT blocking NOT implemented (evidence-gathering stage)"
)

REVIEW_COLS = [
    "change",
    "date",
    "sw2_conf_time",
    "direction",
    "instrument",
    "session",
    "smt_time",
    "entry_type_a",
    "entry_type_b",
    "entry_price_a",
    "entry_price_b",
    "stop_loss_a",
    "stop_loss_b",
    "take_profit_a",
    "take_profit_b",
    "realized_R_a",
    "realized_R_b",
    "outcome_a",
    "outcome_b",
    "a_value",
    "b_value",
]


def _to_et(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(ET)
    return t.tz_convert(ET)


def _id_time(ts) -> str:
    """UTC-normalized match key so ET-offset vs UTC strings still join."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(ET)
    return t.tz_convert("UTC").isoformat()


def _show_time(ts) -> str:
    # Match this session's manual diffs: "2026-02-13 13:13:00-05:00"
    return _to_et(ts).isoformat(sep=" ", timespec="seconds")


def _show_date(value, sw2) -> str:
    if pd.notna(value) and str(value) not in {"", "NaT", "nan"}:
        s = str(value)
        return s[:10] if len(s) >= 10 else s
    return _to_et(sw2).strftime("%Y-%m-%d")


def identity_key(row: pd.Series) -> tuple[str, str, str, str]:
    sw2 = row["sw2_conf_time"]
    if pd.isna(sw2):
        raise ValueError("signal missing sw2_conf_time; cannot diff versions")
    date = _show_date(row["date"] if "date" in row.index else None, sw2)
    direction = str(row["direction"]).upper()
    instrument = str(row["instrument"]).upper()
    return date, _id_time(sw2), direction, instrument


def _num_eq(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=ABS_TOL)


def _str_eq(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return str(a) == str(b)


def _field_eq(name: str, a, b) -> bool:
    if name in MATERIAL_STR:
        return _str_eq(a, b)
    return _num_eq(a, b)


def _cell(row: Optional[pd.Series], col: str):
    if row is None or col not in row.index:
        return pd.NA
    return row[col]


def _side_snapshot(row: Optional[pd.Series]) -> dict:
    out = {}
    for col in SIDE_COLS:
        out[col] = _cell(row, col)
    return out


def _review_row(change: str, key: tuple, a: Optional[pd.Series], b: Optional[pd.Series]) -> dict:
    date, _id_sw2, direction, instrument = key
    src = b if b is not None else a
    sw2_show = _show_time(src["sw2_conf_time"]) if src is not None else ""
    snap_a = _side_snapshot(a)
    snap_b = _side_snapshot(b)
    a_val = snap_a[change] if change in snap_a else pd.NA
    b_val = snap_b[change] if change in snap_b else pd.NA
    if change in {"added", "removed"}:
        a_val = pd.NA if a is None else snap_a.get("entry_type", pd.NA)
        b_val = pd.NA if b is None else snap_b.get("entry_type", pd.NA)
    session = snap_b["session"] if b is not None else snap_a["session"]
    smt = snap_b["smt_time"] if b is not None else snap_a["smt_time"]
    return {
        "change": change,
        "date": date,
        "sw2_conf_time": sw2_show,
        "direction": direction,
        "instrument": instrument,
        "session": session,
        "smt_time": smt,
        "entry_type_a": snap_a["entry_type"],
        "entry_type_b": snap_b["entry_type"],
        "entry_price_a": snap_a["entry_price"],
        "entry_price_b": snap_b["entry_price"],
        "stop_loss_a": snap_a["stop_loss"],
        "stop_loss_b": snap_b["stop_loss"],
        "take_profit_a": snap_a["take_profit"],
        "take_profit_b": snap_b["take_profit"],
        "realized_R_a": snap_a["realized_R"],
        "realized_R_b": snap_b["realized_R"],
        "outcome_a": snap_a["outcome"],
        "outcome_b": snap_b["outcome"],
        "a_value": a_val,
        "b_value": b_val,
    }


def index_by_identity(signals: pd.DataFrame) -> dict[tuple, pd.Series]:
    out: dict[tuple, pd.Series] = {}
    for _, row in signals.iterrows():
        key = identity_key(row)
        if key not in out:
            out[key] = row
    return out


def diff_filled(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Return only added / removed / material-field rows. No unchanged matches."""
    left = index_by_identity(a)
    right = index_by_identity(b)
    rows = []
    for key in sorted(set(left) - set(right)):
        rows.append(_review_row("removed", key, left[key], None))
    for key in sorted(set(right) - set(left)):
        rows.append(_review_row("added", key, None, right[key]))
    for key in sorted(set(left) & set(right)):
        ra, rb = left[key], right[key]
        for field in MATERIAL:
            va = ra[field] if field in ra.index else pd.NA
            vb = rb[field] if field in rb.index else pd.NA
            if not _field_eq(field, va, vb):
                rows.append(_review_row(field, key, ra, rb))
    if not rows:
        return pd.DataFrame(columns=REVIEW_COLS)
    return pd.DataFrame(rows)[REVIEW_COLS]


def load_scanner(path: Union[str, Path], name: str) -> ModuleType:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"scanner not found: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def materialize_scanner(path: Union[str, Path], ref: Optional[str], dest_dir: Path) -> Path:
    path = Path(path)
    if not ref:
        return path
    rel = path if not path.is_absolute() else path.relative_to(_ROOT)
    spec = f"{ref}:{rel.as_posix()}"
    proc = subprocess.run(
        ["git", "-C", str(_ROOT), "show", spec],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise FileNotFoundError(f"git show {spec} failed: {err}")
    out = dest_dir / f"{ref.replace('/', '_')}_{rel.name}"
    out.write_bytes(proc.stdout)
    return out


def run_scanner(mod: ModuleType, es_path: Union[str, Path], nq_path: Union[str, Path]) -> pd.DataFrame:
    if not hasattr(mod, "run"):
        raise ValueError(f"{mod} has no run(es_path, nq_path)")
    result = mod.run(str(es_path), str(nq_path))
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], pd.DataFrame):
        return result[0]
    raise TypeError(f"{mod}.run() must return a DataFrame or (DataFrame, ...)")


def _load_signals(path: Union[str, Path]) -> pd.DataFrame:
    return pd.read_csv(path)


def _fill(
    signals: pd.DataFrame,
    es_bars: Optional[pd.DataFrame],
    nq_bars: Optional[pd.DataFrame],
) -> pd.DataFrame:
    return simulate_fills(signals, es_bars=es_bars, nq_bars=nq_bars)


def compare_versions(
    *,
    a_scanner: Optional[Union[str, Path]] = None,
    b_scanner: Optional[Union[str, Path]] = None,
    a_ref: Optional[str] = None,
    b_ref: Optional[str] = None,
    a_signals: Optional[Union[str, Path, pd.DataFrame]] = None,
    b_signals: Optional[Union[str, Path, pd.DataFrame]] = None,
    es_path: Optional[Union[str, Path]] = None,
    nq_path: Optional[Union[str, Path]] = None,
    a_es_path: Optional[Union[str, Path]] = None,
    a_nq_path: Optional[Union[str, Path]] = None,
    b_es_path: Optional[Union[str, Path]] = None,
    b_nq_path: Optional[Union[str, Path]] = None,
    es_bars: Optional[pd.DataFrame] = None,
    nq_bars: Optional[pd.DataFrame] = None,
    a_es_bars: Optional[pd.DataFrame] = None,
    a_nq_bars: Optional[pd.DataFrame] = None,
    b_es_bars: Optional[pd.DataFrame] = None,
    b_nq_bars: Optional[pd.DataFrame] = None,
    work_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Run/load A and B, simulate fills, return review_these rows."""
    a_es = a_es_path or es_path
    a_nq = a_nq_path or nq_path
    b_es = b_es_path or es_path
    b_nq = b_nq_path or nq_path

    def _bars(df, path, fallback):
        if df is not None:
            return prepare_bars(df)
        if path is not None:
            return prepare_bars(pd.read_csv(path))
        if fallback is not None:
            return fallback if "et" in fallback.columns else prepare_bars(fallback)
        return None

    shared_es = _bars(es_bars, es_path, None)
    shared_nq = _bars(nq_bars, nq_path, None)
    fill_a_es = _bars(a_es_bars, a_es_path, shared_es)
    fill_a_nq = _bars(a_nq_bars, a_nq_path, shared_nq)
    fill_b_es = _bars(b_es_bars, b_es_path, shared_es)
    fill_b_nq = _bars(b_nq_bars, b_nq_path, shared_nq)
    if fill_a_es is None or fill_a_nq is None or fill_b_es is None or fill_b_nq is None:
        raise ValueError("need ES and NQ bars for both sides ( --es / --nq or per-side paths )")

    tmp_ctx = tempfile.TemporaryDirectory() if work_dir is None else None
    dest = Path(work_dir) if work_dir is not None else Path(tmp_ctx.name)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if a_signals is None:
            if a_scanner is None:
                raise ValueError("side A needs --a scanner or --a-signals")
            path_a = materialize_scanner(a_scanner, a_ref, dest)
            if a_es is None or a_nq is None:
                raise ValueError("side A scan needs ES/NQ CSV paths")
            sig_a = run_scanner(load_scanner(path_a, "scanner_a"), a_es, a_nq)
        else:
            sig_a = a_signals if isinstance(a_signals, pd.DataFrame) else _load_signals(a_signals)

        if b_signals is None:
            if b_scanner is None:
                raise ValueError("side B needs --b scanner or --b-signals")
            path_b = materialize_scanner(b_scanner, b_ref, dest)
            if b_es is None or b_nq is None:
                raise ValueError("side B scan needs ES/NQ CSV paths")
            sig_b = run_scanner(load_scanner(path_b, "scanner_b"), b_es, b_nq)
        else:
            sig_b = b_signals if isinstance(b_signals, pd.DataFrame) else _load_signals(b_signals)
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    filled_a = _fill(sig_a, fill_a_es, fill_a_nq)
    filled_b = _fill(sig_b, fill_b_es, fill_b_nq)
    return diff_filled(filled_a, filled_b)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Diff two scanner runs after fill simulation. Writes review_these.csv."
    )
    p.add_argument("--a", dest="a_scanner", help="Scanner A .py")
    p.add_argument("--b", dest="b_scanner", help="Scanner B .py (omit to reuse --a)")
    p.add_argument("--a-ref", help="Git ref for scanner A (git show REF:path)")
    p.add_argument("--b-ref", help="Git ref for scanner B")
    p.add_argument("--a-signals", help="Skip scan A; load this signal CSV")
    p.add_argument("--b-signals", help="Skip scan B; load this signal CSV")
    p.add_argument("--es", help="Shared ES 1m CSV")
    p.add_argument("--nq", help="Shared NQ 1m CSV")
    p.add_argument("--a-es", dest="a_es", help="ES CSV for side A (defaults to --es)")
    p.add_argument("--a-nq", dest="a_nq", help="NQ CSV for side A (defaults to --nq)")
    p.add_argument("--b-es", dest="b_es", help="ES CSV for side B (defaults to --es)")
    p.add_argument("--b-nq", dest="b_nq", help="NQ CSV for side B (defaults to --nq)")
    p.add_argument("--out", required=True, help="review_these.csv path")
    args = p.parse_args(argv)

    b_scanner = args.b_scanner or args.a_scanner
    review = compare_versions(
        a_scanner=args.a_scanner,
        b_scanner=b_scanner,
        a_ref=args.a_ref,
        b_ref=args.b_ref,
        a_signals=args.a_signals,
        b_signals=args.b_signals,
        es_path=args.es,
        nq_path=args.nq,
        a_es_path=args.a_es,
        a_nq_path=args.a_nq,
        b_es_path=args.b_es,
        b_nq_path=args.b_nq,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    review.to_csv(out, index=False)
    counts = review["change"].value_counts() if len(review) else pd.Series(dtype=int)
    print(f"review_these rows: {len(review)}")
    if len(counts):
        print(counts.to_string())
    else:
        print("no differences")
    print(GAP_DISCLOSURE)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
