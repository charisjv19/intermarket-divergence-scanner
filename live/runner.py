"""Poll ProjectX and emit new v8.7 or tagged OOS v8.8 signals.

Scanner detection logic stays frozen. v8.8 is the tagged OOS file, not
origin/main (confirmation clock, session-gap FVG/confirm, inner-join).
"""

from __future__ import annotations

import argparse
import importlib
import io
import json
import sys
import time
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from backtest.reconstruct_v87 import reconstruct_signals
from live.feed import DEFAULT_LOOKBACK_HOURS, snapshot
from live.relay import identity_str, relay, row_to_payload, webhook_urls_from_env
import projectx_historical_pull as px

UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parent.parent

# SHA256 of git show v8.8:smt_scanner_v8_8.py (OOS baseline, not origin/main).
V88_OOS_SHA256 = "6d3580fbfc7b896db511b6781d5f572b83e941122feb99adf3ad54775efb7e8e"

VERSIONS = {
    "v8.7": {
        "module": "smt_scanner_v8_7",
        "reconstruct": True,
        "levels_source": "reconstructed_v87_formula",
    },
    "v8.8": {
        "module": "smt_scanner_v8_8",
        "reconstruct": False,
        "levels_source": "scanner_v88_oos",
    },
}


def normalize_version(raw: str) -> str:
    key = str(raw).strip().lower().replace(" ", "")
    aliases = {
        "8.7": "v8.7",
        "v8.7": "v8.7",
        "v87": "v8.7",
        "87": "v8.7",
        "8.8": "v8.8",
        "v8.8": "v8.8",
        "v88": "v8.8",
        "88": "v8.8",
    }
    if key not in aliases:
        raise ValueError(f"unsupported scanner version {raw!r}; use v8.7 or v8.8")
    return aliases[key]


def load_scanner(version: str):
    spec = VERSIONS[normalize_version(version)]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module(spec["module"])


def load_seen(path: Path) -> set[str]:
    path = Path(path)
    if not path.exists():
        return set()
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return set(str(x) for x in data)
    if isinstance(data, dict):
        return set(str(x) for x in data.get("identities", []))
    return set()


def save_seen(path: Path, keys: set[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(sorted(keys), indent=2) + "\n")
    tmp.replace(path)


def attach_levels(signals: pd.DataFrame, version: str) -> pd.DataFrame:
    spec = VERSIONS[normalize_version(version)]
    out = signals.copy()
    if out.empty:
        out["levels_source"] = spec["levels_source"]
        return out
    if spec["reconstruct"]:
        out = reconstruct_signals(out)
        out["levels_source"] = spec["levels_source"]
        return out
    out["levels_source"] = spec["levels_source"]
    return out


def run_scanner(scanner, es_path: Path, nq_path: Path) -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = scanner.run(str(es_path), str(nq_path))
    signals = result[0] if isinstance(result, tuple) else result
    if signals is None:
        return pd.DataFrame()
    return signals


def new_rows(signals: pd.DataFrame, seen: set[str]) -> pd.DataFrame:
    if signals is None or signals.empty:
        return pd.DataFrame()
    keys = [identity_str(row) for _, row in signals.iterrows()]
    mask = [key not in seen for key in keys]
    return signals.loc[mask].copy()


def seconds_until_next_close(now: Optional[datetime] = None, extra_sec: float = 5.0) -> float:
    current = now or datetime.now(tz=UTC)
    nxt = (current.replace(second=0, microsecond=0) + timedelta(minutes=1))
    wait = (nxt - current).total_seconds() + extra_sec
    return max(1.0, wait)


def default_paths(version: str, *, root: Optional[Path] = None) -> dict[str, Path]:
    version = normalize_version(version)
    root = Path(root) if root is not None else REPO_ROOT
    slug = version.replace(".", "")
    return {
        "bars_dir": root / "live" / "bars" / slug,
        "state_path": root / "live" / "state" / f"{slug}_seen.json",
        "jsonl_path": root / "live" / "state" / f"{slug}_alerts.jsonl",
    }


def process_once(
    version: str,
    *,
    client: Optional[px.ProjectXClient] = None,
    scanner=None,
    paths: Optional[dict[str, Path]] = None,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    seed_seen: bool = True,
    webhook_urls: Optional[list[str]] = None,
    es_contract: Optional[str] = None,
    nq_contract: Optional[str] = None,
    contracts: Optional[dict[str, str]] = None,
    last_bar_utc: Optional[str] = None,
    echo: bool = True,
    artifacts_jsonl: Optional[Path] = None,
) -> dict[str, Any]:
    """One poll: snapshot → scan → seed or relay new identities."""
    version = normalize_version(version)
    paths = paths or default_paths(version)
    scanner = scanner or load_scanner(version)
    client = client or px.ProjectXClient()
    meta = snapshot(
        client,
        bars_dir=paths["bars_dir"],
        lookback_hours=lookback_hours,
        es_contract=es_contract,
        nq_contract=nq_contract,
        contracts=contracts,
    )
    current_last = meta.get("last_bar_utc")
    if last_bar_utc and current_last == last_bar_utc:
        return {
            "version": version,
            "skipped": True,
            "reason": "no_new_bar",
            "last_bar_utc": current_last,
            "n_signals": None,
            "n_new": 0,
            "seeded": False,
            "contracts": {"ES": meta["es_contract"], "NQ": meta["nq_contract"]},
        }

    raw = run_scanner(scanner, meta["es_path"], meta["nq_path"])
    signals = attach_levels(raw, version) if not raw.empty else raw
    seen = load_seen(paths["state_path"])
    seeded = False
    outgoing = pd.DataFrame()
    if not seen and seed_seen:
        seen = {identity_str(row) for _, row in signals.iterrows()} if not signals.empty else set()
        save_seen(paths["state_path"], seen)
        seeded = True
    else:
        outgoing = new_rows(signals, seen)
        for _, row in outgoing.iterrows():
            payload = row_to_payload(row, version=version)
            relay(
                payload,
                jsonl_path=paths["jsonl_path"],
                webhook_urls=webhook_urls,
                echo=echo,
            )
            if artifacts_jsonl is not None:
                relay(
                    payload,
                    jsonl_path=artifacts_jsonl,
                    webhook_urls=[],
                    echo=False,
                )
            seen.add(identity_str(row))
        save_seen(paths["state_path"], seen)

    return {
        "version": version,
        "skipped": False,
        "reason": "seeded" if seeded else "scanned",
        "last_bar_utc": current_last,
        "n_signals": 0 if signals is None or signals.empty else int(len(signals)),
        "n_new": 0 if outgoing.empty else int(len(outgoing)),
        "seeded": seeded,
        "es_bars": meta["es_bars"],
        "nq_bars": meta["nq_bars"],
        "contracts": {"ES": meta["es_contract"], "NQ": meta["nq_contract"]},
    }


def loop(
    version: str,
    *,
    once: bool = False,
    seed_seen: bool = True,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    extra_close_sec: float = 5.0,
    webhook_urls: Optional[list[str]] = None,
    es_contract: Optional[str] = None,
    nq_contract: Optional[str] = None,
    artifacts_jsonl: Optional[Path] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    client: Optional[px.ProjectXClient] = None,
    scanner=None,
    paths: Optional[dict[str, Path]] = None,
    max_polls: Optional[int] = None,
) -> None:
    version = normalize_version(version)
    webhook_urls = webhook_urls if webhook_urls is not None else webhook_urls_from_env()
    client = client or px.ProjectXClient()
    scanner = scanner or load_scanner(version)
    paths = paths or default_paths(version)
    contracts = resolve_contracts_once(
        client, es_contract=es_contract, nq_contract=nq_contract
    )
    print(
        f"{version} live scanner  MES={contracts['ES']}  MNQ={contracts['NQ']}  "
        f"lookback={lookback_hours:g}h  webhooks={len(webhook_urls)}",
        flush=True,
    )
    last_bar = None
    polls = 0
    while True:
        try:
            summary = process_once(
                version,
                client=client,
                scanner=scanner,
                paths=paths,
                lookback_hours=lookback_hours,
                seed_seen=seed_seen,
                webhook_urls=webhook_urls,
                contracts=contracts,
                last_bar_utc=last_bar,
                artifacts_jsonl=artifacts_jsonl,
            )
        except Exception as exc:
            print(f"{version} poll error: {exc}", flush=True)
            if once:
                raise
            sleep_fn(30.0)
            continue
        last_bar = summary.get("last_bar_utc")
        polls += 1
        print(
            f"{version} poll={polls} last={last_bar} bars={summary.get('es_bars')}/"
            f"{summary.get('nq_bars')} signals={summary.get('n_signals')} "
            f"new={summary.get('n_new')} {summary.get('reason')}",
            flush=True,
        )
        if once or (max_polls is not None and polls >= max_polls):
            return
        sleep_fn(seconds_until_next_close(extra_sec=extra_close_sec))


def resolve_contracts_once(
    client: px.ProjectXClient,
    *,
    es_contract: Optional[str] = None,
    nq_contract: Optional[str] = None,
) -> dict[str, str]:
    from live.feed import resolve_front_month

    return resolve_front_month(client, es_contract=es_contract, nq_contract=nq_contract)


def build_parser(version: Optional[str] = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live SMT scanner: ProjectX closed 1m bars → v8.7 or v8.8 signals."
    )
    parser.add_argument(
        "--version",
        default=version,
        help="v8.7 or v8.8 (required unless using python -m live.v87 / live.v88)",
    )
    parser.add_argument("--once", action="store_true", help="One poll then exit")
    parser.add_argument(
        "--alert-existing",
        action="store_true",
        help="Do not seed; relay every identity not already in the seen file",
    )
    parser.add_argument("--lookback-hours", type=float, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--es-contract", default=None)
    parser.add_argument("--nq-contract", default=None)
    parser.add_argument("--webhook-url", action="append", default=None)
    parser.add_argument(
        "--artifacts-jsonl",
        default="/opt/cursor/artifacts/live_signals/alerts.jsonl",
        help="Also append alerts here (empty string to disable)",
    )
    return parser


def main(argv: Optional[list[str]] = None, *, version: Optional[str] = None) -> int:
    parser = build_parser(version=version)
    args = parser.parse_args(argv)
    if not args.version:
        parser.error("--version is required")
    artifacts = Path(args.artifacts_jsonl) if args.artifacts_jsonl else None
    loop(
        args.version,
        once=args.once,
        seed_seen=not args.alert_existing,
        lookback_hours=args.lookback_hours,
        webhook_urls=args.webhook_url,
        es_contract=args.es_contract,
        nq_contract=args.nq_contract,
        artifacts_jsonl=artifacts,
    )
    return 0
