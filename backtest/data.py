"""
Piece 1 — data layer.

Two sources, same output shape (scanner load() columns):

  pull_data(contract_id, start, end)   — ProjectX API + disk cache
  load_csv(path, start, end)           — TradingView / scanner CSV
  get_bars(..., source='auto'|'api'|'csv')

auto tries the API first. If that returns no bars (expired front month),
it uses csv_path when given. Do not mix API prices from the live month
with an old session — that is the J4 roll offset.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

import projectx_historical_pull as px

DateLike = Union[str, date, datetime]

CACHE_DIR = Path(__file__).resolve().parent / "cache"

_COL_ALIASES = {
    "Time": "time",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "swing high": "Swing High",
    "swing low": "Swing Low",
    "SwingHigh": "Swing High",
    "SwingLow": "Swing Low",
}


def _bound(value: DateLike, *, is_end: bool) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=px.ET)
        return px.to_utc(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return px.parse_bound(value.isoformat(), is_end=is_end)
    return px.parse_bound(str(value), is_end=is_end)


def cache_path(contract_id: str, start: datetime, end: datetime, cache_dir: Path = CACHE_DIR) -> Path:
    cid = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in contract_id)
    a = px.iso_z(start).replace(":", "")
    b = px.iso_z(end).replace(":", "")
    return Path(cache_dir) / f"{cid}_{a}_{b}.csv"


def _read_cache(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def _slice(df: pd.DataFrame, start_date: Optional[DateLike], end_date: Optional[DateLike]) -> pd.DataFrame:
    if start_date is None and end_date is None:
        return df
    start = _bound(start_date, is_end=False) if start_date is not None else None
    end = _bound(end_date, is_end=True) if end_date is not None else None
    out = df
    if start is not None:
        out = out[out["time"] >= start]
    if end is not None:
        out = out[out["time"] < end]
    return out.reset_index(drop=True)


def load_csv(
    csv_path: Union[str, Path],
    start_date: Optional[DateLike] = None,
    end_date: Optional[DateLike] = None,
) -> pd.DataFrame:
    """Load a TradingView / scanner 1-min CSV. Swing columns are ground truth."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path}")
    print(f"csv LOAD {path}", flush=True)
    df = pd.read_csv(path)
    df = df.rename(columns={c: _COL_ALIASES.get(c, c) for c in df.columns})
    if "time" not in df.columns:
        raise ValueError(f"{path} has no time column (got {list(df.columns)})")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"{path} missing {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    has_swings = "Swing High" in df.columns and "Swing Low" in df.columns
    if has_swings:
        df["Swing High"] = pd.to_numeric(df["Swing High"], errors="coerce").fillna(0).astype(int)
        df["Swing Low"] = pd.to_numeric(df["Swing Low"], errors="coerce").fillna(0).astype(int)
    else:
        df = px.compute_swings(df)
    df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    df = _slice(df, start_date, end_date)
    return df[px.OUTPUT_COLUMNS].copy()


def pull_data(
    contract_id: str,
    start_date: DateLike,
    end_date: DateLike,
    *,
    cache_dir: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    client: Optional[px.ProjectXClient] = None,
) -> pd.DataFrame:
    """Return 1-min scanner-format bars for one contract, using disk cache.

    start_date / end_date: YYYY-MM-DD (inclusive ET calendar days) or datetimes.
    Cache key is contract_id + normalized [start, end). Pass refresh=True to
    ignore a cached file and pull again.
    """
    start = _bound(start_date, is_end=False)
    end = _bound(end_date, is_end=True)
    dest = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    path = cache_path(contract_id, start, end, dest)

    if path.exists() and not refresh:
        print(f"cache HIT  {path.name}", flush=True)
        return _read_cache(path)

    print(f"cache MISS {path.name}", flush=True)
    dest.mkdir(parents=True, exist_ok=True)
    sess = client or px.ProjectXClient()
    if sess.token is None:
        sess.authenticate()
    df = sess.pull_range(contract_id, start, end, progress=True)
    px.to_scanner_csv(df, str(path))
    return _read_cache(path)


def get_bars(
    contract_id: Optional[str] = None,
    start_date: Optional[DateLike] = None,
    end_date: Optional[DateLike] = None,
    *,
    source: str = "auto",
    csv_path: Optional[Union[str, Path]] = None,
    cache_dir: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    client: Optional[px.ProjectXClient] = None,
) -> pd.DataFrame:
    """Load bars from ProjectX, a TV CSV, or API-then-CSV fallback.

    source:
      api  — pull_data only (0 bars if Gateway dropped that month)
      csv  — load_csv only
      auto — API first; if empty, csv_path (expired-month path)
    """
    source = source.lower().strip()
    if source not in {"auto", "api", "csv"}:
        raise ValueError(f"source must be auto|api|csv, got {source!r}")

    if source == "csv":
        if not csv_path:
            raise ValueError("source='csv' requires csv_path")
        return load_csv(csv_path, start_date, end_date)

    if not contract_id or start_date is None or end_date is None:
        raise ValueError("API pull requires contract_id, start_date, and end_date")

    df = pull_data(
        contract_id,
        start_date,
        end_date,
        cache_dir=cache_dir,
        refresh=refresh,
        client=client,
    )
    if source == "api":
        return df
    if not df.empty:
        return df
    if csv_path:
        print(
            f"API returned 0 bars for {contract_id}; falling back to CSV (expired month?)",
            flush=True,
        )
        return load_csv(csv_path, start_date, end_date)
    raise RuntimeError(
        f"API returned 0 bars for {contract_id} {start_date} → {end_date}. "
        "Gateway does not keep 1-min history on expired front months. "
        "Pass csv_path= to a TradingView export for that session."
    )
