"""
Piece 1 — data layer.

Wraps projectx_historical_pull.py as:

    pull_data(contract_id, start_date, end_date) -> DataFrame

Auth, retrieveBars paging, and 4-bar swings stay in that module.
This file only adds a disk cache keyed by contract_id + date range.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

import projectx_historical_pull as px

DateLike = Union[str, date, datetime]

CACHE_DIR = Path(__file__).resolve().parent / "cache"


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
