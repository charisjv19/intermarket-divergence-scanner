"""Relay live scanner alerts: stdout, JSONL, optional Slack/webhook POST."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

from backtest.compare_versions import identity_key

UTC = timezone.utc
DISPLAY = {"ES": "MES", "NQ": "MNQ", "MES": "MES", "MNQ": "MNQ"}

ALERT_FIELDS = (
    "smt_time",
    "date",
    "session",
    "direction",
    "instrument",
    "entry_type",
    "entry_price",
    "stop_loss",
    "take_profit",
    "target_R",
    "entry_50",
    "es_close",
    "nq_close",
    "es_sw1_time",
    "es_sw1_price",
    "es_sw2_time",
    "es_sw2_price",
    "nq_sw1_time",
    "nq_sw1_price",
    "nq_sw2_time",
    "nq_sw2_price",
    "fvg_bar",
    "fvg_low",
    "fvg_high",
    "pre_fvg_bar",
    "pre_fvg_low",
    "pre_fvg_high",
    "es_15m_bias",
    "nq_15m_bias",
    "combined_15m_bias",
    "smt5m_status",
    "levels_source",
)


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def identity_str(row: pd.Series) -> str:
    date, sw2, direction, instrument = identity_key(row)
    return f"{date}|{sw2}|{direction}|{instrument}"


def row_to_payload(row: pd.Series, *, version: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": version,
        "identity": identity_str(row),
        "relayed_at": datetime.now(tz=UTC).isoformat(),
    }
    for col in ALERT_FIELDS:
        if col in row.index:
            payload[col] = _jsonable(row[col])
    inst = str(row.get("instrument", "")).upper()
    payload["display_instrument"] = DISPLAY.get(inst, inst)
    return payload


def format_text(payload: dict[str, Any]) -> str:
    version = payload.get("version", "?")
    setup = payload.get("entry_type", "?")
    direction = payload.get("direction", "?")
    inst = payload.get("display_instrument") or payload.get("instrument", "?")
    session = payload.get("session") or ""
    smt = str(payload.get("smt_time") or "")[:19].replace("T", " ")
    levels_note = ""
    source = payload.get("levels_source")
    if source:
        levels_note = f"  [{source}]"
    lines = [
        f"[{version}] {setup} {direction} {inst}{levels_note}",
        f"{session}  smt {smt}".strip(),
        (
            f"entry {payload.get('entry_price')}   "
            f"stop {payload.get('stop_loss')}   "
            f"tp {payload.get('take_profit')}   "
            f"({payload.get('target_R')}R)"
        ),
        (
            f"15m {payload.get('combined_15m_bias')}   "
            f"5m {payload.get('smt5m_status')}"
        ),
        f"id {payload.get('identity')}",
    ]
    return "\n".join(lines)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def post_webhook(url: str, payload: dict[str, Any], *, timeout: float = 15.0) -> None:
    text = format_text(payload)
    body = {
        "text": text,
        "username": f"SMT {payload.get('version', 'live')}",
    }
    resp = requests.post(url, json=body, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"webhook HTTP {resp.status_code}: {resp.text[:300]}")


def webhook_urls_from_env(env: Optional[dict[str, str]] = None) -> list[str]:
    import os

    src = env if env is not None else os.environ
    urls = []
    for name in ("SIGNAL_WEBHOOK_URL", "SLACK_WEBHOOK_URL"):
        value = (src.get(name) or "").strip()
        if value and value not in urls:
            urls.append(value)
    return urls


def relay(
    payload: dict[str, Any],
    *,
    jsonl_path: Path,
    webhook_urls: Optional[list[str]] = None,
    echo: bool = True,
) -> None:
    append_jsonl(jsonl_path, payload)
    text = format_text(payload)
    if echo:
        print("\n" + text + "\n", flush=True)
    for url in webhook_urls or []:
        post_webhook(url, payload)
