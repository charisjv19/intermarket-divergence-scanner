"""ProjectX closed-bar snapshot for the live scanners.

Uses retrieveBars with live=False and includePartialBar=False — the same
closed-bar contract as the historical pull. Does not change scanner logic.

After the first poll, each snapshot reuses the on-disk 36h OHLC cache and
fetches only new closed bars plus a short overlap, then recomputes swings.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

import projectx_historical_pull as px

UTC = timezone.utc

# 15m bias looks back MACRO_LOOKBACK_BARS_15M = 144 bars ≈ 36 hours.
DEFAULT_LOOKBACK_HOURS = 36.0
# Re-fetch a few already-cached minutes so a late tick revision can merge.
INCREMENTAL_OVERLAP_MINS = 5.0
OHLC_COLS = ["time", "open", "high", "low", "close"]
SYMBOLS = {"ES": "MES", "NQ": "MNQ"}


def pick_active_contract(contracts: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    """Pick the active micro contract (MES / MNQ) from a Contract/search list."""
    symbol = symbol.upper()
    matches = []
    for row in contracts:
        cid = str(row.get("id") or "")
        sid = str(row.get("symbolId") or "")
        name = str(row.get("name") or "").upper()
        if f".{symbol}." in cid or sid.endswith(f".{symbol}") or name.startswith(symbol):
            matches.append(row)
    if not matches:
        raise RuntimeError(f"Contract/search returned no {symbol} contracts")
    active = [row for row in matches if row.get("activeContract")]
    pool = active or matches
    exact = [row for row in pool if row.get("symbolId") == f"F.US.{symbol}"]
    return (exact or pool)[0]


def resolve_front_month(
    client: px.ProjectXClient,
    *,
    es_contract: Optional[str] = None,
    nq_contract: Optional[str] = None,
    live: bool = px.LIVE_BARS,
) -> dict[str, str]:
    """Return {ES: MES contract id, NQ: MNQ contract id}."""
    out = {}
    if es_contract:
        out["ES"] = es_contract
    else:
        found = pick_active_contract(client.search_contracts("MES", live=live), "MES")
        out["ES"] = str(found["id"])
    if nq_contract:
        out["NQ"] = nq_contract
    else:
        found = pick_active_contract(client.search_contracts("MNQ", live=live), "MNQ")
        out["NQ"] = str(found["id"])
    return out


def _utc_ts(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize(UTC)
    return ts.tz_convert(UTC)


def load_cached_ohlc(path: Optional[Path]) -> pd.DataFrame:
    """Read scanner-format CSV OHLC if present. Swing columns are ignored."""
    if path is None:
        return pd.DataFrame(columns=OHLC_COLS)
    path = Path(path)
    if not path.is_file():
        return pd.DataFrame(columns=OHLC_COLS)
    df = pd.read_csv(path)
    if df.empty or "time" not in df.columns:
        return pd.DataFrame(columns=OHLC_COLS)
    out = pd.DataFrame(
        {
            "time": pd.to_datetime(df["time"], utc=True),
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "high": pd.to_numeric(df["high"], errors="coerce"),
            "low": pd.to_numeric(df["low"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
        }
    )
    out = out.dropna(subset=OHLC_COLS)
    return out.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)


def merge_ohlc(cached: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Concat OHLC; overlapping timestamps keep the freshly fetched row."""
    frames = []
    for frame in (cached, fresh):
        if frame is None or frame.empty:
            continue
        frames.append(frame[OHLC_COLS].copy())
    if not frames:
        return pd.DataFrame(columns=OHLC_COLS)
    both = pd.concat(frames, ignore_index=True)
    both["time"] = pd.to_datetime(both["time"], utc=True)
    for col in ("open", "high", "low", "close"):
        both[col] = pd.to_numeric(both[col], errors="coerce")
    both = both.dropna(subset=OHLC_COLS)
    return (
        both.drop_duplicates(subset=["time"], keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )


def trim_lookback(
    df: pd.DataFrame,
    end: datetime,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLC_COLS)
    cutoff = px.to_utc(end) - timedelta(hours=lookback_hours)
    times = pd.to_datetime(df["time"], utc=True)
    return df.loc[times >= cutoff].reset_index(drop=True)


def incremental_fetch_start(
    cached: pd.DataFrame,
    *,
    end: datetime,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    overlap_mins: float = INCREMENTAL_OVERLAP_MINS,
) -> datetime:
    """Start of retrieveBars window: full lookback, or last cached bar minus overlap."""
    lookback_start = px.to_utc(end) - timedelta(hours=lookback_hours)
    if cached is None or cached.empty:
        return lookback_start
    last = _utc_ts(cached["time"].iloc[-1]).to_pydatetime()
    return max(lookback_start, last - timedelta(minutes=overlap_mins))


def pull_closed_1m(
    client: px.ProjectXClient,
    contract_id: str,
    *,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    now: Optional[datetime] = None,
    cache_path: Optional[Path] = None,
    overlap_mins: float = INCREMENTAL_OVERLAP_MINS,
) -> pd.DataFrame:
    """Closed 1-minute bars for one contract, then 4-bar swing tags.

    Cold start pulls the full lookback. Later polls merge a short incremental
    retrieveBars window onto the cached OHLC, trim to lookback, and retag swings.
    """
    end = px.to_utc(now or datetime.now(tz=UTC))
    cached = load_cached_ohlc(cache_path)
    start = incremental_fetch_start(
        cached, end=end, lookback_hours=lookback_hours, overlap_mins=overlap_mins
    )
    bars = client.fetch_bars(contract_id, start, end)
    fresh = px.bars_to_dataframe(bars)
    merged = trim_lookback(
        merge_ohlc(cached, fresh), end, lookback_hours=lookback_hours
    )
    return px.compute_swings(merged)


def write_scanner_csv(df: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    px.to_scanner_csv(df, str(path))
    return path


def snapshot(
    client: px.ProjectXClient,
    *,
    bars_dir: Path,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    es_contract: Optional[str] = None,
    nq_contract: Optional[str] = None,
    now: Optional[datetime] = None,
    contracts: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Pull MES + MNQ, write scanner-format CSVs, return paths and last-bar time."""
    contracts = contracts or resolve_front_month(
        client, es_contract=es_contract, nq_contract=nq_contract
    )
    bars_dir = Path(bars_dir)
    es_path = bars_dir / "MES.csv"
    nq_path = bars_dir / "MNQ.csv"
    es = pull_closed_1m(
        client,
        contracts["ES"],
        lookback_hours=lookback_hours,
        now=now,
        cache_path=es_path,
    )
    nq = pull_closed_1m(
        client,
        contracts["NQ"],
        lookback_hours=lookback_hours,
        now=now,
        cache_path=nq_path,
    )
    es_path = write_scanner_csv(es, es_path)
    nq_path = write_scanner_csv(nq, nq_path)
    last = None
    if not es.empty:
        last = pd.Timestamp(es["time"].iloc[-1])
        if last.tzinfo is None:
            last = last.tz_localize(UTC)
        else:
            last = last.tz_convert(UTC)
    meta = {
        "es_path": es_path,
        "nq_path": nq_path,
        "es_contract": contracts["ES"],
        "nq_contract": contracts["NQ"],
        "es_bars": int(len(es)),
        "nq_bars": int(len(nq)),
        "last_bar_utc": last.isoformat() if last is not None else None,
    }
    serial = dict(meta)
    serial["es_path"] = str(es_path)
    serial["nq_path"] = str(nq_path)
    (bars_dir / "snapshot.json").write_text(json.dumps(serial, indent=2) + "\n")
    return meta
