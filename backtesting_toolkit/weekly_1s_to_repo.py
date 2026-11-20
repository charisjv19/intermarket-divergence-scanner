"""
Weekly 1-second MES/MNQ pull into the private historical-data repo.

TopstepX keeps 1-second bars for a rolling 14 days, so this is meant to run
once a week (Saturday, market closed) from cron on the Cursor machine:

  1. git pull the local clone of the private data repo
  2. for each symbol and each ET day in the last --days days (default 13),
     skip days already saved; otherwise pull the day's 1-second bars from
     every candidate contract and keep the one with the most volume
     (= front month, which handles roll weeks)
  3. save each new day as data_1s/{SYM}/{SYM}_1s_{YYYY-MM-DD}.csv.gz
     (columns: time, open, high, low, close, Volume; UTC; raw contract prices)
  4. append one row per new day to data_1s/pull_log.csv
     (date, symbol, contract, bars, volume, first_bar, last_bar, pulled_at)
  5. git commit and push

Credentials: PROJECTX_USERNAME / PROJECTX_API_KEY from the environment or the
scanner repo's .env (same file the live scanner uses).

  python3 -m backtesting_toolkit.weekly_1s_to_repo \\
      --data-repo ~/intermarket-divergence-data

Use --no-push to pull and save without committing (dry run); the next normal
run commits those files. First run only: add --import-from data_1s to bring in
daily CSVs already pulled with projectx_historical_pull --unit second.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from backtesting_toolkit import projectx_historical_pull as px

UTC = timezone.utc
ET = px.ET
SYMBOLS = ["MES", "MNQ"]
DEFAULT_DAYS = 13
QUARTER_MONTHS = "HMUZ"
LOG_FIELDS = ["date", "symbol", "contract", "bars", "volume", "first_bar", "last_bar", "pulled_at"]
REPO_ROOT = Path(__file__).resolve().parent.parent

_CONTRACT_RE = re.compile(r"^(CON\.F\.US\.[A-Z0-9]+\.)([HMUZ])(\d{2})$")


def previous_quarter(contract_id: str) -> Optional[str]:
    """CON.F.US.MES.Z26 -> CON.F.US.MES.U26; CON.F.US.MES.H27 -> CON.F.US.MES.Z26."""
    m = _CONTRACT_RE.match(contract_id)
    if not m:
        return None
    prefix, month, yy = m.group(1), m.group(2), int(m.group(3))
    i = QUARTER_MONTHS.index(month)
    if i == 0:
        return f"{prefix}Z{(yy - 1) % 100:02d}"
    return f"{prefix}{QUARTER_MONTHS[i - 1]}{yy:02d}"


def candidate_contracts(client, symbol: str) -> list[str]:
    """Contracts TopstepX lists for the symbol, plus each one's previous quarter."""
    prefix = f"CON.F.US.{symbol}."
    listed = [c.get("id", "") for c in client.search_contracts(symbol)]
    listed = [cid for cid in listed if cid.startswith(prefix) and _CONTRACT_RE.match(cid)]
    if not listed:
        raise RuntimeError(f"No {symbol} contracts returned by Contract/search")
    out = set(listed)
    for cid in listed:
        prev = previous_quarter(cid)
        if prev:
            out.add(prev)
    return sorted(out)


def pull_day(client, contract_id: str, day: datetime) -> pd.DataFrame:
    """All 1-second bars for one ET calendar day from one contract (empty on error)."""
    rows = []
    try:
        for c0, c1 in px.iter_chunks(day, day + timedelta(days=1), hours=px.SECONDS_CHUNK_HOURS):
            rows.extend(px.fetch_seconds_window(client, contract_id, c0, c1))
    except RuntimeError as exc:  # e.g. an expired contract the API no longer serves
        print(f"    {contract_id}: {exc}", flush=True)
        return px.seconds_bars_to_dataframe([])
    return px.seconds_bars_to_dataframe(rows)


def day_path(data_repo: Path, symbol: str, day: datetime) -> Path:
    return data_repo / "data_1s" / symbol / f"{symbol}_1s_{day.date().isoformat()}.csv.gz"


def write_day(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    tmp = path.with_suffix(path.suffix + ".part")
    with gzip.open(tmp, "wt", newline="") as fh:
        out.to_csv(fh, index=False)
    os.replace(tmp, path)


def append_log(data_repo: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path = data_repo / "data_1s" / "pull_log.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def days_to_pull(now: datetime, days: int) -> list[datetime]:
    """Finished ET calendar days in the last `days` days, Saturdays excluded."""
    today = now.astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for k in range(days, 0, -1):
        d = today - timedelta(days=k)
        if d.weekday() != 5:
            out.append(d)
    return out


def _run_git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout


def load_env() -> None:
    """Load the scanner repo's .env (like live_scanners.runner). Existing env wins."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    path = REPO_ROOT / ".env"
    if path.exists():
        load_dotenv(path, override=False)


def import_daily_csvs(data_repo: Path, import_dir: Path, symbols: list[str], pulled_at: str) -> list[dict]:
    """One-time import of daily CSVs from projectx_historical_pull --unit second
    (import_dir/{SYM}/{SYM}_1s_YYYY-MM-DD.csv). Days already in the data repo are skipped."""
    rows = []
    for symbol in symbols:
        for f in sorted((import_dir / symbol).glob(f"{symbol}_1s_*.csv")):
            day = datetime.strptime(f.stem[-10:], "%Y-%m-%d").replace(tzinfo=ET)
            path = day_path(data_repo, symbol, day)
            if path.exists():
                continue
            df = pd.read_csv(f)
            if df.empty:
                continue
            df["time"] = pd.to_datetime(df["time"], utc=True)
            write_day(df[px.SECONDS_COLUMNS], path)
            rows.append({
                "date": day.date().isoformat(), "symbol": symbol, "contract": f"imported:{f.name}",
                "bars": len(df), "volume": int(df["Volume"].sum()),
                "first_bar": df["time"].iloc[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "last_bar": df["time"].iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "pulled_at": pulled_at,
            })
            print(f"  imported {f.name}  {len(df)} bars", flush=True)
    return rows


def pull_symbol(client, data_repo: Path, symbol: str, days: list[datetime], pulled_at: str) -> list[dict]:
    contracts = candidate_contracts(client, symbol)
    print(f"{symbol}: candidate contracts {contracts}", flush=True)
    rows = []
    for day in days:
        path = day_path(data_repo, symbol, day)
        if path.exists():
            print(f"  {day.date()}  already saved", flush=True)
            continue
        best, best_cid = None, None
        for cid in contracts:
            df = pull_day(client, cid, day)
            if not df.empty and (best is None or df["Volume"].sum() > best["Volume"].sum()):
                best, best_cid = df, cid
        if best is None:
            print(f"  {day.date()}  no bars (holiday, or outside the 14-day window)", flush=True)
            continue
        write_day(best, path)
        row = {
            "date": day.date().isoformat(), "symbol": symbol, "contract": best_cid,
            "bars": len(best), "volume": int(best["Volume"].sum()),
            "first_bar": pd.Timestamp(best["time"].iloc[0]).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_bar": pd.Timestamp(best["time"].iloc[-1]).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pulled_at": pulled_at,
        }
        rows.append(row)
        print(f"  {day.date()}  {row['bars']:>6} bars  volume={row['volume']}  {best_cid}", flush=True)
    return rows


def rebuild_delta(data_repo: Path, symbols: list[str]) -> list[Path]:
    """Rebuild delta_1m/{SYM}_1m_delta_{first}_{last}.csv.gz from every daily file in
    data_1s/{SYM}/, replacing the symbol's previous delta file."""
    from backtesting_toolkit.volume_delta import minute_delta

    written = []
    for symbol in symbols:
        files = sorted((data_repo / "data_1s" / symbol).glob(f"{symbol}_1s_*.csv.gz"))
        if not files:
            continue
        first, last = files[0].name[-17:-7], files[-1].name[-17:-7]
        df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
        m = minute_delta(df)
        m["time"] = m["time"].dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        out_dir = data_repo / "delta_1m"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{symbol}_1m_delta_{first}_{last}.csv.gz"
        for old in out_dir.glob(f"{symbol}_1m_delta_*.csv.gz"):
            if old != out:
                old.unlink()
        tmp = out.with_suffix(out.suffix + ".part")
        with gzip.open(tmp, "wt", newline="") as fh:
            m.to_csv(fh, index=False)
        os.replace(tmp, out)
        written.append(out)
        print(f"{symbol}: delta rebuilt, {len(m)} minutes -> {out.relative_to(data_repo)}", flush=True)
    return written


CHANGELOG_HEADER = "## Change log"


def add_changelog_row(data_repo: Path, date: str, change: str, files: str) -> bool:
    """Insert a row at the top of the DATA_MANIFEST.md change-log table."""
    path = data_repo / "DATA_MANIFEST.md"
    if not path.exists():
        print("WARNING: DATA_MANIFEST.md not found; change log not updated", flush=True)
        return False
    lines = path.read_text().splitlines(keepends=True)
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == CHANGELOG_HEADER)
        sep = next(i for i in range(start, len(lines)) if lines[i].startswith("|---"))
    except StopIteration:
        print("WARNING: change-log table not found in DATA_MANIFEST.md", flush=True)
        return False
    lines.insert(sep + 1, f"| {date} | {change} | {files} |\n")
    path.write_text("".join(lines))
    return True


def summarize(log_rows: list[dict]) -> str:
    parts = []
    for symbol in sorted({r["symbol"] for r in log_rows}):
        rows = [r for r in log_rows if r["symbol"] == symbol]
        parts.append(f"{symbol} {len(rows)} days ({min(r['date'] for r in rows)} to {max(r['date'] for r in rows)}, "
                     f"{sum(r['bars'] for r in rows):,} seconds)")
    return "; ".join(parts)


def run(
    data_repo: Path,
    *,
    import_dir: Optional[Path] = None,
    branch: Optional[str] = None,
    update_delta: bool = False,
    update_manifest: bool = False,
    days: int = DEFAULT_DAYS,
    symbols: list[str] = SYMBOLS,
    push: bool = True,
    client=None,
    now: Optional[datetime] = None,
    git: Callable[..., str] = _run_git,
) -> list[dict]:
    data_repo = Path(data_repo).expanduser().resolve()
    if not (data_repo / ".git").exists():
        raise SystemExit(f"{data_repo} is not a git clone of the data repo")
    if push:
        git(data_repo, "pull", "--ff-only")
        if branch:
            git(data_repo, "checkout", "-b", branch)
    now = now or datetime.now(tz=UTC)
    client = client or px.ProjectXClient()
    client.authenticate()
    pulled_at = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_rows: list[dict] = []
    if import_dir is not None:
        log_rows.extend(import_daily_csvs(data_repo, Path(import_dir).expanduser(), symbols, pulled_at))
    errors: list[str] = []
    for symbol in symbols:
        try:
            log_rows.extend(pull_symbol(client, data_repo, symbol, days_to_pull(now, days), pulled_at))
        except Exception as exc:  # keep going so other symbols still get saved and committed
            errors.append(f"{symbol}: {exc}")
            print(f"{symbol}: FAILED {exc}", flush=True)
    append_log(data_repo, log_rows)
    if not log_rows:
        print("Nothing new pulled.", flush=True)
    else:
        if update_delta:
            rebuild_delta(data_repo, sorted({r["symbol"] for r in log_rows}))
        if update_manifest:
            add_changelog_row(
                data_repo, now.astimezone(ET).date().isoformat(),
                f"Weekly 1s pull: {summarize(log_rows)}" + ("; delta rebuilt" if update_delta else ""),
                "`data_1s/`" + (", `delta_1m/`" if update_delta else ""),
            )
    tracked = ["data_1s"] + (["delta_1m"] if (data_repo / "delta_1m").exists() else []) \
        + (["DATA_MANIFEST.md"] if (data_repo / "DATA_MANIFEST.md").exists() else [])
    if not push:
        print(f"Saved {len(log_rows)} new symbol-days (not committed: --no-push)", flush=True)
        if errors:
            raise RuntimeError("; ".join(errors))
        return log_rows
    # Commit anything uncommitted under data_1s, including files saved by an
    # earlier --no-push run, so a dry run never blocks a later real one.
    if git(data_repo, "status", "--porcelain", *tracked).strip():
        git(data_repo, "add", *tracked)
        if log_rows:
            span = f"{min(r['date'] for r in log_rows)} to {max(r['date'] for r in log_rows)}"
            msg = f"Weekly 1s pull: {len(log_rows)} new symbol-days ({span})"
        else:
            msg = "Weekly 1s pull: commit previously saved files"
        git(data_repo, "commit", "-m", msg)
        if branch:
            git(data_repo, "push", "-u", "origin", branch)
        else:
            git(data_repo, "push")
        print(f"Committed and pushed: {msg}", flush=True)
        print(f"RESULT: new_symbol_days={len(log_rows)} branch={branch or 'current'} pushed=yes "
              f"summary=\"{summarize(log_rows)}\"", flush=True)
    else:
        if branch:
            git(data_repo, "checkout", "-")
            git(data_repo, "branch", "-D", branch)
        print(f"RESULT: new_symbol_days=0 pushed=no", flush=True)
    if errors:
        raise RuntimeError("Some symbols failed (saved data was still committed): " + "; ".join(errors))
    return log_rows


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Weekly 1-second MES/MNQ pull into the private data repo.")
    p.add_argument("--data-repo", required=True, help="Local clone of the private historical-data repo")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS, help="How many days back to check (max useful: 13)")
    p.add_argument("--symbols", nargs="+", default=SYMBOLS)
    p.add_argument("--no-push", action="store_true", help="Save files but don't git pull/commit/push")
    p.add_argument("--branch", default=None,
                   help="Commit to a new branch with this name and push it (for a pull request) "
                        "instead of pushing to the current branch")
    p.add_argument("--update-delta", action="store_true",
                   help="Rebuild delta_1m/ from all of data_1s/ when new days were pulled")
    p.add_argument("--update-manifest", action="store_true",
                   help="Add a change-log row to DATA_MANIFEST.md when new days were pulled")
    p.add_argument("--import-from", default=None,
                   help="One-time: also import daily CSVs from a projectx_historical_pull --unit second "
                        "folder (e.g. data_1s) before pulling")
    args = p.parse_args(argv)
    load_env()
    if not (os.environ.get("PROJECTX_USERNAME") or os.environ.get("PROJECT_X_USERNAME")
            or os.environ.get("TOPSTEPX_USERNAME")) or not (
            os.environ.get("PROJECTX_API_KEY") or os.environ.get("PROJECT_X_API_KEY")
            or os.environ.get("TOPSTEPX_API_KEY")):
        raise SystemExit("MISSING CREDENTIALS: PROJECTX_USERNAME / PROJECTX_API_KEY are not set "
                         "(environment, Cursor secrets, or the scanner repo's .env). Nothing was pulled.")
    run(Path(args.data_repo), days=args.days, symbols=args.symbols, push=not args.no_push,
        import_dir=Path(args.import_from) if args.import_from else None,
        branch=args.branch, update_delta=args.update_delta, update_manifest=args.update_manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
