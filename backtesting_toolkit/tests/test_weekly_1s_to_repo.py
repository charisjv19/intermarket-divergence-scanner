"""Unit tests for the weekly 1-second pull into the data repo. No live API or git remote."""

import gzip
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from backtesting_toolkit import projectx_historical_pull as px
from backtesting_toolkit import weekly_1s_to_repo as wk

UTC = timezone.utc
ET = px.ET


class FakeClient:
    """Z26 trades 2 contracts/second, U26 trades 1; U26 has no data after Dec 1."""

    chunk_pause_sec = 0

    def __init__(self, listed=("CON.F.US.MES.Z26",)):
        self.listed = listed
        self.calls = 0

    def authenticate(self):
        return "t"

    def search_contracts(self, text, live=False):
        return [{"id": c} for c in self.listed] + [{"id": "CON.F.US.MESX.Z26"}]

    def fetch_bars(self, cid, start, end, *, unit=px.UNIT_MINUTE, unit_number=1, limit=px.BAR_LIMIT):
        self.calls += 1
        if cid.endswith("H26"):
            raise RuntimeError("contract not found")
        vol = 2 if cid.endswith("Z26") else 1
        if cid.endswith("U26") and start >= datetime(2026, 12, 1, tzinfo=UTC):
            return []
        bars, t = [], start
        while t < end and len(bars) < 60:  # 60 bars per chunk keeps tests fast
            bars.append({"t": t.isoformat(), "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": vol})
            t += timedelta(seconds=1)
        return bars


def fake_git(calls):
    def g(repo, *args):
        calls.append(args)
        return " M data_1s/x" if args[0] == "status" else ""
    return g


def git_repo(d):
    os.makedirs(os.path.join(d, ".git"))
    return Path(d)


class TestHelpers(unittest.TestCase):
    def test_previous_quarter(self):
        self.assertEqual(wk.previous_quarter("CON.F.US.MES.Z26"), "CON.F.US.MES.U26")
        self.assertEqual(wk.previous_quarter("CON.F.US.MNQ.H27"), "CON.F.US.MNQ.Z26")
        self.assertIsNone(wk.previous_quarter("junk"))

    def test_candidates_filter_and_add_previous(self):
        self.assertEqual(wk.candidate_contracts(FakeClient(), "MES"),
                         ["CON.F.US.MES.U26", "CON.F.US.MES.Z26"])

    def test_days_skip_saturday_and_today(self):
        now = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)  # Saturday
        days = wk.days_to_pull(now, 13)
        self.assertEqual(days[0].date().isoformat(), "2026-11-08")
        self.assertEqual(days[-1].date().isoformat(), "2026-11-20")
        self.assertTrue(all(d.weekday() != 5 for d in days))


class TestRun(unittest.TestCase):
    def test_saves_front_month_logs_and_commits(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            repo = git_repo(d)
            rows = wk.run(repo, days=3, symbols=["MES"], client=FakeClient(),
                          now=datetime(2026, 11, 21, 15, tzinfo=UTC),
                          git=fake_git(calls))
            self.assertEqual([r["date"] for r in rows], ["2026-11-18", "2026-11-19", "2026-11-20"])
            self.assertTrue(all(r["contract"] == "CON.F.US.MES.Z26" for r in rows))
            f = repo / "data_1s" / "MES" / "MES_1s_2026-11-19.csv.gz"
            with gzip.open(f, "rt") as fh:
                df = pd.read_csv(fh)
            self.assertEqual(list(df.columns), px.SECONDS_COLUMNS)
            self.assertTrue((df["Volume"] == 2).all())
            log = pd.read_csv(repo / "data_1s" / "pull_log.csv")
            self.assertEqual(len(log), 3)
            self.assertEqual(calls[0], ("pull", "--ff-only"))
            self.assertEqual([c[0] for c in calls[1:]], ["status", "add", "commit", "push"])

    def test_resume_skips_saved_days_and_no_commit_when_nothing_new(self):
        calls = []
        clean = lambda r, *a: calls.append(a) or ""  # git status reports nothing to commit
        now = datetime(2026, 11, 21, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as d:
            repo = git_repo(d)
            wk.run(repo, days=3, symbols=["MES"], client=FakeClient(), now=now, git=fake_git([]))
            c = FakeClient()
            rows = wk.run(repo, days=3, symbols=["MES"], client=c, now=now, git=clean)
            self.assertEqual(rows, [])
            self.assertEqual(c.calls, 0)
            self.assertEqual(calls, [("pull", "--ff-only"), ("status", "--porcelain", "data_1s")])

    def test_dry_run_files_get_committed_by_next_real_run(self):
        now = datetime(2026, 11, 21, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as d:
            repo = git_repo(d)
            wk.run(repo, days=2, symbols=["MES"], client=FakeClient(), now=now, push=False)
            calls = []
            rows = wk.run(repo, days=2, symbols=["MES"], client=FakeClient(), now=now, git=fake_git(calls))
            self.assertEqual(rows, [])
            self.assertIn(("commit", "-m", "Weekly 1s pull: commit previously saved files"), calls)
            self.assertEqual(calls[-1], ("push",))

    def test_import_existing_daily_csvs(self):
        now = datetime(2026, 11, 21, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src:
            os.makedirs(os.path.join(src, "MES"))
            pd.DataFrame({"time": ["2026-11-05T23:55:35+00:00", "2026-11-05T23:55:36+00:00"],
                          "open": [1, 1], "high": [2, 2], "low": [0.5, 0.5], "close": [1, 1],
                          "Volume": [33, 7]}).to_csv(os.path.join(src, "MES", "MES_1s_2026-11-05.csv"), index=False)
            repo = git_repo(d)
            rows = wk.run(repo, days=1, symbols=["MES"], client=FakeClient(), now=now, push=False,
                          import_dir=Path(src))
            imp = [r for r in rows if r["contract"].startswith("imported:")]
            self.assertEqual(len(imp), 1)
            self.assertEqual(imp[0]["volume"], 40)
            self.assertTrue((repo / "data_1s" / "MES" / "MES_1s_2026-11-05.csv.gz").exists())

    def test_roll_week_picks_higher_volume_contract(self):
        # After the roll only H27 is listed; Z26 (previous quarter) is still the front month.
        class Roll(FakeClient):
            def fetch_bars(self, cid, start, end, **k):
                if cid.endswith("H27"):
                    return [{"t": start.isoformat(), "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]
                return super().fetch_bars(cid, start, end, **k)
        with tempfile.TemporaryDirectory() as d:
            repo = git_repo(d)
            rows = wk.run(repo, days=1, symbols=["MES"], client=Roll(listed=("CON.F.US.MES.H27",)),
                          now=datetime(2026, 12, 15, 15, tzinfo=UTC), push=False)
            self.assertEqual(rows[0]["contract"], "CON.F.US.MES.Z26")

    def test_expired_contract_error_is_tolerated(self):
        with tempfile.TemporaryDirectory() as d:
            repo = git_repo(d)
            rows = wk.run(repo, days=1, symbols=["MES"], client=FakeClient(listed=("CON.F.US.MES.M26",)),
                          now=datetime(2026, 11, 20, 15, tzinfo=UTC), push=False)
            self.assertEqual(rows[0]["contract"], "CON.F.US.MES.M26")  # H26 raised, M26 kept

    def test_one_symbol_failing_still_commits_the_other(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            repo = git_repo(d)
            with self.assertRaises(RuntimeError):
                wk.run(repo, days=1, symbols=["MES", "MNQ"], client=FakeClient(),  # no MNQ contracts listed
                       now=datetime(2026, 11, 21, 15, tzinfo=UTC), git=fake_git(calls))
            self.assertTrue((repo / "data_1s" / "MES" / "MES_1s_2026-11-20.csv.gz").exists())
            self.assertEqual(calls[-1], ("push",))

    def test_requires_git_clone(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                wk.run(Path(d), client=FakeClient(), push=False)


if __name__ == "__main__":
    unittest.main()
