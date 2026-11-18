"""
Piece 3 — structural regression checks.

Mechanical correctness, not PnL. A scanner version that fails these
must not be backtested.

Checks:
  1. Python 4-bar swings match a Pine-tagged reference CSV exactly
  2. No null entry_price / stop_loss / take_profit
  3. MAX_SW_TIME_GAP_MINS and SW1_PARALLEL_TOL_MINS are exactly 0,
     and any sw1/sw2 timestamps on the signal CSV match exactly
  4. No duplicate (date, sw2_conf_time, direction, instrument)
  5. |entry - stop| never below the instrument min-stop floor
     (ES/MES 5.0 pts = 20 ticks, NQ/MNQ 10.0 pts = 40 ticks)
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional, Union

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from swing_marker_detection import detect_swings

LEVEL_COLS = ("entry_price", "stop_loss", "take_profit")
DUP_COLS = ("date", "sw2_conf_time", "direction", "instrument")
SW_TIME_PAIRS = (
    ("es_sw1_time", "nq_sw1_time"),
    ("es_sw2_time", "nq_sw2_time"),
)
MIN_STOP = {"ES": 5.0, "MES": 5.0, "NQ": 10.0, "MNQ": 10.0}
REQUIRED_TOLERANCE = 0.0


class StructuralFailure(Exception):
    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("structural checks failed:\n" + "\n".join(f"  - {f}" for f in failures))


def load_scanner(path: Union[str, Path]) -> ModuleType:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"scanner not found: {path}")
    spec = importlib.util.spec_from_file_location("scanner_under_test", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _contiguous_4bar_mask(df: pd.DataFrame) -> pd.Series:
    """True where the 4-bar swing window is 1-minute contiguous (skip weekend/session gaps)."""
    if "time" not in df.columns:
        return pd.Series(True, index=df.index)
    t = pd.to_datetime(df["time"], utc=True)
    d = t.diff().dt.total_seconds()
    step = d.between(50, 70)
    # confirmation at i uses bars i-3..i; flag is written at i-1
    confirm = step & step.shift(1, fill_value=False) & step.shift(2, fill_value=False)
    return confirm.shift(-1, fill_value=False)


def check_swings_match_pine(ref_csv: Union[str, Path, pd.DataFrame]) -> list[str]:
    """Python detect_swings() must equal the CSV's Swing High / Swing Low columns."""
    df = pd.read_csv(ref_csv) if not isinstance(ref_csv, pd.DataFrame) else ref_csv.copy()
    rename = {c: c.lower() for c in ("Open", "High", "Low", "Close", "Time") if c in df.columns}
    if rename:
        df = df.rename(columns=rename)
    for col in ("high", "low"):
        if col not in df.columns:
            return [f"swing reference missing {col}"]
    if "Swing High" not in df.columns or "Swing Low" not in df.columns:
        return ["swing reference missing Pine columns 'Swing High' / 'Swing Low'"]
    got = detect_swings(df[["high", "low"]].copy() if "time" not in df.columns else df.copy())
    pine_h = pd.to_numeric(df["Swing High"], errors="coerce").fillna(0).astype(int)
    pine_l = pd.to_numeric(df["Swing Low"], errors="coerce").fillna(0).astype(int)
    py_h = got["Swing High"].astype(int)
    py_l = got["Swing Low"].astype(int)
    mask = _contiguous_4bar_mask(df)
    fails = []
    n_h = int(((py_h != pine_h) & mask).sum())
    n_l = int(((py_l != pine_l) & mask).sum())
    n = int(mask.sum()) if mask.any() else len(df)
    if n_h:
        fails.append(f"Swing High mismatch vs Pine on {n_h} / {n} contiguous 4-bar windows")
    if n_l:
        fails.append(f"Swing Low mismatch vs Pine on {n_l} / {n} contiguous 4-bar windows")
    return fails


def check_no_null_levels(signals: pd.DataFrame) -> list[str]:
    missing = [c for c in LEVEL_COLS if c not in signals.columns]
    if missing:
        return [f"signal CSV missing {missing}; need v8.8 entry_price/stop_loss/take_profit"]
    fails = []
    for col in LEVEL_COLS:
        n = int(signals[col].isna().sum())
        if n:
            fails.append(f"{n} signals have null {col}")
    return fails


def check_timestamp_tolerances(
    scanner: Optional[ModuleType] = None,
    signals: Optional[pd.DataFrame] = None,
) -> list[str]:
    fails = []
    if scanner is not None:
        for name in ("MAX_SW_TIME_GAP_MINS", "SW1_PARALLEL_TOL_MINS"):
            if not hasattr(scanner, name):
                fails.append(f"scanner missing {name}")
                continue
            val = getattr(scanner, name)
            if val != REQUIRED_TOLERANCE:
                fails.append(f"{name}={val}; must be {REQUIRED_TOLERANCE} (exact match only)")
    if signals is None:
        return fails
    for left, right in SW_TIME_PAIRS:
        if left not in signals.columns or right not in signals.columns:
            continue
        a = pd.to_datetime(signals[left], errors="coerce")
        b = pd.to_datetime(signals[right], errors="coerce")
        both = a.notna() & b.notna()
        n = int((a[both] != b[both]).sum())
        if n:
            fails.append(
                f"{n} rows have {left} != {right} (SW1_PARALLEL_TOL_MINS / "
                f"MAX_SW_TIME_GAP_MINS require exact timestamp match)"
            )
    return fails


def check_no_duplicate_signals(signals: pd.DataFrame) -> list[str]:
    work = signals.copy()
    if "sw2_conf_time" not in work.columns:
        return ["signal CSV missing sw2_conf_time; cannot check duplicates"]
    if "date" not in work.columns:
        ts = pd.to_datetime(work["sw2_conf_time"], utc=True, errors="coerce")
        work["date"] = ts.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    missing = [c for c in DUP_COLS if c not in work.columns]
    if missing:
        return [f"signal CSV missing {missing}; cannot check duplicates"]
    dups = work[list(DUP_COLS)].duplicated()
    n = int(dups.sum())
    if n:
        return [f"{n} duplicate signals on {DUP_COLS}"]
    return []


def check_min_stop_floor(signals: pd.DataFrame) -> list[str]:
    for col in ("entry_price", "stop_loss", "instrument"):
        if col not in signals.columns:
            return [f"signal CSV missing {col}; cannot check min-stop floor"]
    fails = []
    risk = (signals["entry_price"] - signals["stop_loss"]).abs()
    inst = signals["instrument"].astype(str).str.upper()
    for name, floor in MIN_STOP.items():
        mask = inst == name
        if not mask.any():
            continue
        n = int((risk[mask] + 1e-9 < floor).sum())
        if n:
            fails.append(f"{n} {name} signals have risk < min-stop floor {floor}")
    unknown = ~inst.isin(MIN_STOP)
    if unknown.any():
        fails.append(f"{int(unknown.sum())} signals have unknown instrument for min-stop check")
    return fails


def run_structural_tests(
    *,
    signals: Optional[pd.DataFrame] = None,
    pine_csv: Optional[Union[str, Path, pd.DataFrame]] = None,
    scanner: Optional[ModuleType] = None,
    scanner_path: Optional[Union[str, Path]] = None,
) -> list[str]:
    """Return a list of failure strings. Empty list = pass."""
    fails: list[str] = []
    if pine_csv is not None:
        fails.extend(check_swings_match_pine(pine_csv))
    mod = scanner
    if mod is None and scanner_path is not None:
        mod = load_scanner(scanner_path)
    if mod is not None or signals is not None:
        fails.extend(check_timestamp_tolerances(mod, signals))
    if signals is not None:
        fails.extend(check_no_null_levels(signals))
        fails.extend(check_no_duplicate_signals(signals))
        fails.extend(check_min_stop_floor(signals))
    if not any([pine_csv is not None, mod is not None, signals is not None]):
        fails.append("nothing to check: pass signals, pine_csv, and/or scanner_path")
    return fails


def assert_structural_ok(**kwargs) -> None:
    fails = run_structural_tests(**kwargs)
    if fails:
        raise StructuralFailure(fails)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Structural regression checks. Fail before any backtest.")
    p.add_argument("--signals", help="Scanner output CSV")
    p.add_argument("--pine-csv", help="OHLC CSV with Pine Swing High / Swing Low columns")
    p.add_argument("--scanner", help="Path to smt_scanner_v*.py")
    args = p.parse_args(argv)
    sigs = pd.read_csv(args.signals) if args.signals else None
    fails = run_structural_tests(signals=sigs, pine_csv=args.pine_csv, scanner_path=args.scanner)
    if fails:
        print("STRUCTURAL CHECKS FAILED")
        for f in fails:
            print(f"  FAIL  {f}")
        return 1
    print("STRUCTURAL CHECKS PASSED")
    if args.signals:
        print(f"  signals: {len(sigs)}")
    if args.pine_csv:
        print(f"  pine csv: {args.pine_csv}")
    if args.scanner:
        print(f"  scanner: {args.scanner}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
