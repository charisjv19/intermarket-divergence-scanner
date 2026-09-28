"""Shared clocks, identities, and leak guards for the discretion-review pack.

Nothing here loads fill outcomes. Decision bar:

  FVG_AFTER_SMT  — completing bar of the 3-bar FVG window (fvg_bar + 1m).
                   That is the last closed bar when the live-strict limit
                   would be placed (placement is the next bar, j+2).
  SMT_IN_FVG     — confirmation bar (smt_time == sw2_conf_time + 1m).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional, Union
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from PIL import Image, PngImagePlugin

from backtest.compare_versions import identity_key

ET = ZoneInfo("America/New_York")
SAMPLE_SEED = 42
ID_SEED = 43
ID_ALPHABET = "23456789abcdefghjkmnpqrstvwxyz"
ID_LEN = 8
LOOKBACK_BARS = 90
MAX_LOOKBACK_BARS = 180
CONTEXT_15M_BARS = 16
DISPLAY = {"ES": "MES", "NQ": "MNQ"}

HAS_R = ("win", "loss", "no_fill_by_eod")
NEVER_FILLED = ("no_fill_never_traded", "no_fill_timeout", "no_fill_50pct")

LEAK_PATTERNS = (
    r"realized[_ ]?r\b",
    r"\bfill_time\b",
    r"\bfill_price\b",
    r"\bfilled\b",
    r"\bno_fill\b",
    r"\boutcome\b",
    r"\btake_profit\b",
    r"\btarget_r\b",
    r"\beqmtm\b",
    r"\bwin_rate\b",
    r"\bexpectancy\b",
    r"\bpnl\b",
    r"\bprofit\b",
)
LEAK_RE = re.compile("|".join(LEAK_PATTERNS), re.IGNORECASE)

BLIND_CSV_COLS = (
    "stored_order",
    "id",
    "window",
    "date",
    "sw2_conf_time",
    "direction",
    "instrument",
    "entry_type",
    "session",
    "combined_15m_bias",
    "decision_time",
)


def to_et(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(ET)
    return t.tz_convert(ET)


def to_utc(ts) -> pd.Timestamp:
    return to_et(ts).tz_convert("UTC")


def parse_swing_et(ts) -> pd.Timestamp:
    """Signal sw1/sw2 times are naive 'YYYY-MM-DD HH:MM' in ET."""
    return to_et(ts)


def identity_tuple(row: pd.Series) -> tuple[str, str, str, str]:
    return identity_key(row)


def random_ids(n: int, *, seed: int = ID_SEED, length: int = ID_LEN) -> list[str]:
    rng = np.random.default_rng(seed)
    alphabet = list(ID_ALPHABET)
    seen: set[str] = set()
    out: list[str] = []
    while len(out) < n:
        token = "".join(rng.choice(alphabet, size=length))
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def decision_bar_time(row: pd.Series) -> pd.Timestamp:
    etype = str(row["entry_type"]).strip()
    if etype == "FVG_AFTER_SMT":
        fvg = row.get("fvg_bar")
        if fvg is None or pd.isna(fvg):
            raise ValueError("FVG_AFTER_SMT row missing fvg_bar")
        return to_et(fvg) + pd.Timedelta(minutes=1)
    if etype == "SMT_IN_FVG":
        smt = row.get("smt_time")
        if smt is None or pd.isna(smt):
            raise ValueError("SMT_IN_FVG row missing smt_time")
        return to_et(smt)
    raise ValueError(f"unknown entry_type {etype!r}")


def near_edge_price(row: pd.Series) -> Optional[float]:
    """LONG near-edge is fvg_high (completing-bar low); SHORT is fvg_low."""
    if str(row["entry_type"]) != "FVG_AFTER_SMT":
        return None
    direction = str(row["direction"]).upper()
    if direction == "LONG":
        val = row.get("fvg_high")
    elif direction == "SHORT":
        val = row.get("fvg_low")
    else:
        return None
    if val is None or pd.isna(val):
        return None
    return float(val)


def prepare_tape(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    rename = {c: c.lower() for c in ("Open", "High", "Low", "Close", "Time") if c in out.columns}
    if rename:
        out = out.rename(columns=rename)
    if "time" not in out.columns:
        raise ValueError("tape needs a time column")
    out["time"] = pd.to_datetime(out["time"], utc=True)
    out = out.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    out["et"] = out["time"].dt.tz_convert(ET)
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in ("Swing High", "Swing Low"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
        else:
            out[col] = 0
    return out


def locate_bar(tape: pd.DataFrame, when) -> int:
    et = to_et(when)
    hits = tape.index[tape["et"] == et]
    if len(hits) == 0:
        raise KeyError(f"no tape bar at {et}")
    return int(hits[0])


def clip_1m(
    tape: pd.DataFrame,
    decision,
    *,
    lookback: int = LOOKBACK_BARS,
    max_lookback: int = MAX_LOOKBACK_BARS,
    extra_times: Iterable = (),
) -> pd.DataFrame:
    """Bars up to and including the decision bar. Nothing after it."""
    dec_i = locate_bar(tape, decision)
    start = max(0, dec_i - lookback)
    for ts in extra_times:
        if ts is None or (isinstance(ts, float) and pd.isna(ts)) or pd.isna(ts):
            continue
        try:
            i = locate_bar(tape, ts)
        except KeyError:
            continue
        if i < start:
            start = max(0, min(i - 5, dec_i - max_lookback))
    clipped = tape.iloc[start : dec_i + 1].copy().reset_index(drop=True)
    if clipped.empty:
        raise ValueError("1m clip is empty")
    last = to_et(clipped["et"].iloc[-1])
    if last != to_et(decision):
        raise AssertionError(f"clip last bar {last} != decision {to_et(decision)}")
    if (clipped["et"] > to_et(decision)).any():
        raise AssertionError("1m clip leaked bars after the decision bar")
    return clipped


def confirmed_swing_mask(clipped: pd.DataFrame, decision) -> pd.Series:
    """Swing at t is confirmed when t+1m closes. Decision bar itself is unconfirmed."""
    decision_et = to_et(decision)
    confirm_at = clipped["et"] + pd.Timedelta(minutes=1)
    return confirm_at <= decision_et


def resample_15m(tape_1m: pd.DataFrame) -> pd.DataFrame:
    work = tape_1m.set_index("et")[["open", "high", "low", "close"]]
    out = work.resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out.reset_index()
    out["close_time"] = out["et"] + pd.Timedelta(minutes=15)
    return out


def clip_15m(
    tape_1m: pd.DataFrame,
    decision,
    *,
    n_bars: int = CONTEXT_15M_BARS,
) -> pd.DataFrame:
    """15m bars whose close is at or before the clock at the end of the decision 1m."""
    decision_et = to_et(decision)
    clock = decision_et + pd.Timedelta(minutes=1)
    look = tape_1m[tape_1m["et"] <= decision_et].copy()
    if look.empty:
        return look
    bars = resample_15m(look)
    closed = bars[bars["close_time"] <= clock].copy()
    if closed.empty:
        return closed
    return closed.tail(n_bars).reset_index(drop=True)


def bar_ohlc(tape: pd.DataFrame, when) -> dict:
    i = locate_bar(tape, when)
    row = tape.iloc[i]
    return {
        "et": to_et(row["et"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
    }


def nearby_shapes(tape: pd.DataFrame, when, n_before: int = 3) -> pd.DataFrame:
    i = locate_bar(tape, when)
    start = max(0, i - n_before)
    chunk = tape.iloc[start : i + 1].copy()
    chunk["body"] = chunk["close"] - chunk["open"]
    chunk["range"] = chunk["high"] - chunk["low"]
    chunk["shape"] = np.where(
        chunk["body"] > 0,
        "up",
        np.where(chunk["body"] < 0, "down", "doji"),
    )
    return chunk[["et", "open", "high", "low", "close", "body", "range", "shape"]]


def leak_hits(text: str) -> list[str]:
    return [m.group(0) for m in LEAK_RE.finditer(text or "")]


def strip_png_text(path: Union[str, Path]) -> None:
    path = Path(path)
    with Image.open(path) as im:
        cleaned = im.copy()
        cleaned.save(path, format="PNG", pnginfo=PngImagePlugin.PngInfo())


def png_text_chunks(path: Union[str, Path]) -> dict[str, str]:
    path = Path(path)
    with Image.open(path) as im:
        return {k: str(v) for k, v in (im.info or {}).items()}


def scan_path_for_leaks(path: Union[str, Path]) -> list[str]:
    path = Path(path)
    hits: list[str] = []
    name_hits = leak_hits(path.name)
    if name_hits:
        hits.append(f"{path}: filename {name_hits}")
    if path.suffix.lower() == ".png":
        meta = png_text_chunks(path)
        blob = json.dumps(meta, default=str)
        meta_hits = leak_hits(blob)
        if meta_hits:
            hits.append(f"{path}: png metadata {meta_hits}")
        return hits
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (UnicodeDecodeError, OSError):
        return hits
    text_hits = leak_hits(text)
    if text_hits:
        hits.append(f"{path}: text {text_hits}")
    return hits


def scan_tree_for_leaks(root: Union[str, Path], extra_skip: Iterable[str] = ()) -> list[str]:
    root = Path(root)
    skip = set(extra_skip)
    hits: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in skip for part in p.parts):
            continue
        hits.extend(scan_path_for_leaks(p))
    return hits
