"""
ProjectX / TopstepX historical 1-minute bar pull.

smt_live_scanner_v8.py is not in this repo. authenticate() and fetch_bars()
below follow the same Gateway calls that file uses:

  POST /api/Auth/loginKey
      body: {"userName": ..., "apiKey": ...}
      token -> Authorization: Bearer <token>

  POST /api/History/retrieveBars
      unit: 2, unitNumber: 1, live: False, includePartialBar: False
      (live: True returns errors, not clean data)

The live scanner's fetch_bars() only covers a 4-hour lookback. This script
pages an arbitrary range in ~6-hour chunks, concatenates, then tags
Swing High / Swing Low with the same 4-bar pattern as
swing_marker_detection.detect_swings / _detect_5m_swings_with_index.

Output CSV columns (scanner load() format):
  time, open, high, low, close, Swing High, Swing Low

Credentials (never commit these):
  PROJECTX_USERNAME  / PROJECTX_API_KEY
  PROJECT_X_USERNAME / PROJECT_X_API_KEY   (also accepted)
  TOPSTEPX_USERNAME  / TOPSTEPX_API_KEY    (also accepted)

Example (one ET calendar day, then compare against a known TV export):

  export PROJECTX_USERNAME=...
  export PROJECTX_API_KEY=...
  python projectx_historical_pull.py \\
      --contract-id CON.F.US.MES.U26 \\
      --start 2026-02-25 --end 2026-02-25 \\
      --out MES_2026-02-25.csv

Date-only --start/--end are inclusive America/New_York calendar days.
Pass full ISO datetimes to control the window exactly.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests

API_ROOT = os.environ.get("PROJECTX_BASE_URL", "https://api.topstepx.com/api").rstrip("/")
ET = ZoneInfo("America/New_York")
UTC = timezone.utc

UNIT_MINUTE = 2
UNIT_NUMBER_1M = 1
LIVE_BARS = False  # live: True returns errors, not clean data
BAR_LIMIT = 20_000
CHUNK_HOURS = 6
CHUNK_PAUSE_SEC = 0.75  # stay under retrieveBars 50 req / 30s
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30

OUTPUT_COLUMNS = ["time", "open", "high", "low", "close", "Swing High", "Swing Low"]


# ── credentials ───────────────────────────────────────────────────────────────

def _first_env(*names: str) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def load_credentials() -> tuple[str, str]:
    user = _first_env("PROJECTX_USERNAME", "PROJECT_X_USERNAME", "TOPSTEPX_USERNAME")
    key = _first_env("PROJECTX_API_KEY", "PROJECT_X_API_KEY", "TOPSTEPX_API_KEY")
    if not user or not key:
        raise RuntimeError(
            "Missing TopstepX credentials. Set PROJECTX_USERNAME and "
            "PROJECTX_API_KEY (or PROJECT_X_* / TOPSTEPX_*)."
        )
    return user, key


# ── time helpers ──────────────────────────────────────────────────────────────

def to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def iso_z(ts: datetime) -> str:
    return to_utc(ts).isoformat().replace("+00:00", "Z")


def parse_bound(value: str, *, is_end: bool) -> datetime:
    """Parse a CLI date or datetime.

    Date-only values are America/New_York calendar days. --end is inclusive,
    so '2026-02-25' becomes 2026-02-26 00:00 ET (exclusive upper bound).
    """
    raw = value.strip()
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        day = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=ET)
        return day + timedelta(days=1) if is_end else day
    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    return ts


def iter_chunks(start: datetime, end: datetime, hours: float = CHUNK_HOURS) -> list[tuple[datetime, datetime]]:
    """Half-open [start, end) windows of `hours` (last window may be shorter)."""
    start = to_utc(start)
    end = to_utc(end)
    if end <= start:
        raise ValueError(f"end must be after start: {start} >= {end}")
    step = timedelta(hours=hours)
    chunks = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + step, end)
        chunks.append((cursor, nxt))
        cursor = nxt
    return chunks


# ── 4-bar swings (same as swing_marker_detection / _detect_5m_swings_with_index)

def compute_swings(df: pd.DataFrame) -> pd.DataFrame:
    """Flag 4-bar swing highs/lows on the bar where the swing *occurs* (bar[1]).

    Confirmed when bar[0] closes:
      swing high: high[1] > high[2] and high[1] > high[3] and high[1] > high[0]
      swing low:  low[1]  < low[2]  and low[1]  < low[3]  and low[1]  < low[0]
    """
    if df.empty:
        out = df.copy()
        out["Swing High"] = pd.Series(dtype="int64")
        out["Swing Low"] = pd.Series(dtype="int64")
        return out

    h = df["high"].values
    l = df["low"].values
    n = len(df)
    swing_high = [0] * n
    swing_low = [0] * n
    for i in range(3, n):
        if h[i - 1] > h[i - 2] and h[i - 1] > h[i - 3] and h[i - 1] > h[i]:
            swing_high[i - 1] = 1
        if l[i - 1] < l[i - 2] and l[i - 1] < l[i - 3] and l[i - 1] < l[i]:
            swing_low[i - 1] = 1
    out = df.copy()
    out["Swing High"] = swing_high
    out["Swing Low"] = swing_low
    return out


# ── HTTP client ───────────────────────────────────────────────────────────────

class ProjectXClient:
    """Raw-requests TopstepX client. No project-x-py (uvloop / Windows)."""

    def __init__(
        self,
        username: Optional[str] = None,
        api_key: Optional[str] = None,
        api_root: str = API_ROOT,
        session: Optional[requests.Session] = None,
        chunk_pause_sec: float = CHUNK_PAUSE_SEC,
    ):
        if username is None or api_key is None:
            username, api_key = load_credentials()
        self.username = username
        self.api_key = api_key
        self.api_root = api_root.rstrip("/")
        self.session = session or requests.Session()
        self.chunk_pause_sec = chunk_pause_sec
        self.token: Optional[str] = None

    def authenticate(self) -> str:
        """POST /api/Auth/loginKey. Same payload as the live scanner."""
        payload = {"userName": self.username, "apiKey": self.api_key}
        data = self._post("/Auth/loginKey", payload, auth=False)
        if not data.get("success") or not data.get("token"):
            raise RuntimeError(
                f"loginKey failed: errorCode={data.get('errorCode')} "
                f"errorMessage={data.get('errorMessage')}"
            )
        self.token = data["token"]
        return self.token

    def fetch_bars(
        self,
        contract_id: str,
        start_time: datetime,
        end_time: datetime,
        *,
        limit: int = BAR_LIMIT,
    ) -> list[dict[str, Any]]:
        """Single /History/retrieveBars call (live scanner does a 4-hour window)."""
        if self.token is None:
            self.authenticate()
        payload = {
            "contractId": contract_id,
            "live": LIVE_BARS,
            "startTime": iso_z(start_time),
            "endTime": iso_z(end_time),
            "unit": UNIT_MINUTE,
            "unitNumber": UNIT_NUMBER_1M,
            "limit": limit,
            "includePartialBar": False,
        }
        data = self._post("/History/retrieveBars", payload, auth=True)
        if not data.get("success", False):
            raise RuntimeError(
                f"retrieveBars failed: errorCode={data.get('errorCode')} "
                f"errorMessage={data.get('errorMessage')}"
            )
        return list(data.get("bars") or [])

    def pull_range(
        self,
        contract_id: str,
        start_time: datetime,
        end_time: datetime,
        *,
        chunk_hours: float = CHUNK_HOURS,
        progress: bool = True,
    ) -> pd.DataFrame:
        """Page fetch_bars() across [start, end) in ~6-hour chunks, then swing-tag."""
        chunks = iter_chunks(start_time, end_time, hours=chunk_hours)
        if self.token is None:
            self.authenticate()
        rows: list[dict[str, Any]] = []
        for i, (c0, c1) in enumerate(chunks, start=1):
            if progress:
                print(
                    f"  chunk {i}/{len(chunks)}  {iso_z(c0)} → {iso_z(c1)}",
                    flush=True,
                )
            bars = self.fetch_bars(contract_id, c0, c1)
            rows.extend(bars)
            if i < len(chunks) and self.chunk_pause_sec > 0:
                time.sleep(self.chunk_pause_sec)
        df = bars_to_dataframe(rows)
        df = compute_swings(df)
        return df

    def _post(self, path: str, payload: dict[str, Any], *, auth: bool) -> dict[str, Any]:
        url = f"{self.api_root}{path}"
        reauthed = False
        last_err: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if auth:
                if not self.token:
                    self.authenticate()
                headers["Authorization"] = f"Bearer {self.token}"
            try:
                resp = self.session.post(
                    url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT
                )
            except requests.RequestException as exc:
                last_err = exc
                _backoff(attempt)
                continue

            if resp.status_code == 401 and auth and not reauthed:
                # Token expiry: same recovery as the live scanner — login again, retry once.
                self.authenticate()
                reauthed = True
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 2.0 ** (attempt + 1)
                except ValueError:
                    wait = 2.0 ** (attempt + 1)
                time.sleep(min(wait, 60.0))
                continue

            if 500 <= resp.status_code < 600:
                last_err = RuntimeError(f"HTTP {resp.status_code} from {path}: {resp.text[:200]}")
                _backoff(attempt)
                continue

            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code} from {path}: {resp.text[:400]}")

            try:
                data = resp.json()
            except ValueError as exc:
                raise RuntimeError(f"Non-JSON response from {path}: {resp.text[:200]}") from exc

            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected JSON from {path}: {type(data)}")
            return data

        if last_err:
            raise RuntimeError(f"{path} failed after {MAX_RETRIES} retries") from last_err
        raise RuntimeError(f"{path} failed after {MAX_RETRIES} retries")


def _backoff(attempt: int) -> None:
    time.sleep(min(1.0 * (2 ** attempt), 16.0))


# ── frame / CSV ───────────────────────────────────────────────────────────────

def bars_to_dataframe(bars: Iterable[dict[str, Any]]) -> pd.DataFrame:
    records = []
    for bar in bars:
        ts = bar.get("t")
        if ts is None:
            continue
        records.append(
            {
                "time": ts,
                "open": bar.get("o"),
                "high": bar.get("h"),
                "low": bar.get("l"),
                "close": bar.get("c"),
            }
        )
    if not records:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close"])
    df = pd.DataFrame.from_records(records)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["time", "open", "high", "low", "close"])
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df


def to_scanner_csv(df: pd.DataFrame, path: str) -> None:
    out = df.copy()
    if "Swing High" not in out.columns or "Swing Low" not in out.columns:
        out = compute_swings(out)
    out = out[OUTPUT_COLUMNS].copy()
    out["time"] = pd.to_datetime(out["time"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    out["Swing High"] = out["Swing High"].astype(int)
    out["Swing Low"] = out["Swing Low"].astype(int)
    out.to_csv(path, index=False)


def pull_historical(
    contract_id: str,
    start: datetime,
    end: datetime,
    out_path: str,
    *,
    client: Optional[ProjectXClient] = None,
    chunk_hours: float = CHUNK_HOURS,
) -> pd.DataFrame:
    client = client or ProjectXClient()
    print(f"Auth: loginKey as {client.username}", flush=True)
    client.authenticate()
    print(
        f"Pull {contract_id}  {iso_z(start)} → {iso_z(end)}  "
        f"({chunk_hours:g}h chunks, 1-min, live=False)",
        flush=True,
    )
    df = client.pull_range(contract_id, start, end, chunk_hours=chunk_hours)
    to_scanner_csv(df, out_path)
    n_sh = int(df["Swing High"].sum()) if not df.empty else 0
    n_sl = int(df["Swing Low"].sum()) if not df.empty else 0
    print(
        f"Wrote {len(df)} bars  swing highs={n_sh}  swing lows={n_sl}  → {out_path}",
        flush=True,
    )
    return df


# Module-level aliases matching the live scanner's authenticate()/fetch_bars() names.
_default_client: Optional[ProjectXClient] = None


def _client() -> ProjectXClient:
    global _default_client
    if _default_client is None:
        _default_client = ProjectXClient()
    return _default_client


def authenticate() -> str:
    return _client().authenticate()


def fetch_bars(contract_id: str, start_time: datetime, end_time: datetime) -> list[dict[str, Any]]:
    return _client().fetch_bars(contract_id, start_time, end_time)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pull ProjectX 1-min OHLC and write a scanner-format CSV."
    )
    p.add_argument("--contract-id", required=True, help="e.g. CON.F.US.MES.U26 (front month rolls)")
    p.add_argument("--start", required=True, help="YYYY-MM-DD or ISO datetime (ET if naive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive, or ISO datetime")
    p.add_argument("--out", required=True, help="Output CSV path")
    p.add_argument("--chunk-hours", type=float, default=CHUNK_HOURS)
    p.add_argument(
        "--username",
        default=None,
        help="Override PROJECTX_USERNAME (prefer env vars)",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="Override PROJECTX_API_KEY (prefer env vars)",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    start = parse_bound(args.start, is_end=False)
    end = parse_bound(args.end, is_end=True)
    client = None
    if args.username or args.api_key:
        env_user, env_key = (None, None)
        try:
            env_user, env_key = load_credentials()
        except RuntimeError:
            pass
        user = args.username or env_user
        key = args.api_key or env_key
        if not user or not key:
            raise RuntimeError("Need both username and API key (flags or env).")
        client = ProjectXClient(username=user, api_key=key)
    pull_historical(
        args.contract_id,
        start,
        end,
        args.out,
        client=client,
        chunk_hours=args.chunk_hours,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
