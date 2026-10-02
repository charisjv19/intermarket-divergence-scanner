"""ProjectX closed-bar snapshot for the live scanners.

Uses retrieveBars with live=False and includePartialBar=False — the same
closed-bar contract as the historical pull. Does not change scanner logic.
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


def pull_closed_1m(
    client: px.ProjectXClient,
    contract_id: str,
    *,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    now: Optional[datetime] = None,
) -> pd.DataFrame:
    """Single retrieveBars window of closed 1-minute bars, then 4-bar swing tags."""
    end = px.to_utc(now or datetime.now(tz=UTC))
    start = end - timedelta(hours=lookback_hours)
    bars = client.fetch_bars(contract_id, start, end)
    df = px.bars_to_dataframe(bars)
    return px.compute_swings(df)


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
    es = pull_closed_1m(
        client, contracts["ES"], lookback_hours=lookback_hours, now=now
    )
    nq = pull_closed_1m(
        client, contracts["NQ"], lookback_hours=lookback_hours, now=now
    )
    bars_dir = Path(bars_dir)
    es_path = write_scanner_csv(es, bars_dir / "MES.csv")
    nq_path = write_scanner_csv(nq, bars_dir / "MNQ.csv")
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
