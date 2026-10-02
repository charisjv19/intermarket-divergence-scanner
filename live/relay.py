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
    "confirmations_count",
    "alt_sw1_times",
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


def _present(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text not in ("", "nan", "None", "NaT", "<NA>")


def _fmt_time(value: Any) -> str:
    if not _present(value):
        return "—"
    text = str(value).replace("T", " ")
    return text[:16]


def _fmt_px(value: Any) -> str:
    if not _present(value):
        return "—"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _clock(value: Any) -> str:
    text = _fmt_time(value)
    if text == "—" or len(text) < 16:
        return text
    return text[11:16]


def _parse_alt_sw1_times(raw: Any) -> list[str]:
    if not _present(raw):
        return []
    parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
    return [p for p in parts if p]


def _primary_sw1_time(payload: dict[str, Any]) -> Any:
    inst = str(payload.get("instrument") or "").upper()
    if inst in ("NQ", "MNQ"):
        return payload.get("nq_sw1_time")
    return payload.get("es_sw1_time")


def _sw1_count_line(payload: dict[str, Any]) -> str:
    alts = _parse_alt_sw1_times(payload.get("alt_sw1_times"))
    raw_count = payload.get("confirmations_count")
    try:
        count = int(float(raw_count)) if _present(raw_count) else 1 + len(alts)
    except (TypeError, ValueError):
        count = 1 + len(alts)
    primary_clock = _clock(_primary_sw1_time(payload))
    stamps: list[str] = []
    if primary_clock != "—":
        stamps.append(f"{primary_clock} (primary)")
    for alt in alts:
        if alt and alt not in {primary_clock} and alt not in stamps:
            stamps.append(alt)
    if count <= 1 and not alts:
        return "sw1s: 1  (primary only)"
    listed = ", ".join(stamps) if stamps else "—"
    return f"sw1s: {max(count, len(stamps))}  {listed}"


def _swing_line(label: str, t1: Any, p1: Any, t2: Any, p2: Any) -> str:
    return (
        f"{label}  sw1 {_fmt_time(t1)} @ {_fmt_px(p1)}   "
        f"sw2 {_fmt_time(t2)} @ {_fmt_px(p2)}"
    )


def _fvg_line(payload: dict[str, Any]) -> str:
    entry = str(payload.get("entry_type") or "")
    use_pre = entry == "SMT_IN_FVG"
    if use_pre:
        bar, lo, hi = (
            payload.get("pre_fvg_bar"),
            payload.get("pre_fvg_low"),
            payload.get("pre_fvg_high"),
        )
        kind = "pre-existing"
    else:
        bar, lo, hi = (
            payload.get("fvg_bar"),
            payload.get("fvg_low"),
            payload.get("fvg_high"),
        )
        kind = "after SMT"
        if not (_present(bar) or _present(lo) or _present(hi)) and (
            _present(payload.get("pre_fvg_bar"))
            or _present(payload.get("pre_fvg_low"))
        ):
            bar = payload.get("pre_fvg_bar")
            lo = payload.get("pre_fvg_low")
            hi = payload.get("pre_fvg_high")
            kind = "pre-existing"
    if not (_present(bar) or _present(lo) or _present(hi)):
        return "FVG target: (none in payload)"
    return f"FVG target: {kind}  {_fmt_time(bar)}  {_fmt_px(lo)}-{_fmt_px(hi)}"


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
        f"*[{version}] {setup} {direction} {inst}*{levels_note}",
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
        _swing_line(
            "MES",
            payload.get("es_sw1_time"),
            payload.get("es_sw1_price"),
            payload.get("es_sw2_time"),
            payload.get("es_sw2_price"),
        ),
        _swing_line(
            "MNQ",
            payload.get("nq_sw1_time"),
            payload.get("nq_sw1_price"),
            payload.get("nq_sw2_time"),
            payload.get("nq_sw2_price"),
        ),
        _sw1_count_line(payload),
        _fvg_line(payload),
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
        "icon_emoji": ":chart_with_upwards_trend:",
        "mrkdwn": True,
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


def test_alert_payload(version: str) -> dict[str, Any]:
    """Synthetic alert used by --test-webhook. Not a market signal."""
    now = datetime.now(tz=UTC).isoformat()
    return {
        "version": version,
        "identity": f"TEST|{now}|LONG|ES",
        "relayed_at": now,
        "smt_time": now,
        "date": now[:10],
        "session": "TEST",
        "direction": "LONG",
        "instrument": "ES",
        "display_instrument": "MES",
        "entry_type": "TEST_WEBHOOK",
        "entry_price": 6700.5,
        "stop_loss": 6689.0,
        "take_profit": 6734.0,
        "target_R": 3.0,
        "combined_15m_bias": "n/a",
        "smt5m_status": "n/a",
        "levels_source": "test_webhook",
        "es_sw1_time": "2026-09-29 13:10",
        "es_sw1_price": 6694.00,
        "es_sw2_time": "2026-09-29 13:24",
        "es_sw2_price": 6690.25,
        "nq_sw1_time": "2026-09-29 13:10",
        "nq_sw1_price": 24810.00,
        "nq_sw2_time": "2026-09-29 13:24",
        "nq_sw2_price": 24802.50,
        "confirmations_count": 3,
        "alt_sw1_times": "13:16, 13:21",
        "fvg_bar": "2026-09-29 13:25",
        "fvg_low": 6698.00,
        "fvg_high": 6703.00,
    }


def send_test_webhook(version: str, webhook_urls: list[str]) -> None:
    if not webhook_urls:
        raise RuntimeError(
            "No Slack webhook. Set SLACK_WEBHOOK_URL in .env "
            "(or SIGNAL_WEBHOOK_URL / --webhook-url)."
        )
    payload = test_alert_payload(version)
    for url in webhook_urls:
        post_webhook(url, payload)
    print(format_text(payload), flush=True)
    print(f"Posted test alert for {version} to {len(webhook_urls)} webhook(s).", flush=True)


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
