"""Unit tests for the 1-second daily pull. No live API calls."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from backtesting_toolkit import projectx_historical_pull as px

UTC = timezone.utc
ET = px.ET


class FakeClient:
    """Stands in for ProjectXClient: serves one bar per second with v = second-of-minute."""

    def __init__(self, cap=px.BAR_LIMIT):
        self.calls = []
        self.cap = cap
        self.chunk_pause_sec = 0

    def authenticate(self):
        return "token"

    def fetch_bars(self, contract_id, start, end, *, unit=px.UNIT_MINUTE, unit_number=1, limit=px.BAR_LIMIT):
        self.calls.append((start, end, unit, unit_number))
        n = int((end - start).total_seconds())
        bars = []
        for k in range(min(n, self.cap)):
            t = start + timedelta(seconds=k)
            bars.append({"t": t.isoformat(), "o": 100, "h": 101, "l": 99, "c": 100.25, "v": t.second + 1})
        return bars


class TestSecondsFrame(unittest.TestCase):
    def test_keeps_volume_sorts_dedupes(self):
        bars = [
            {"t": "2026-10-01T13:30:01+00:00", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 7},
            {"t": "2026-10-01T13:30:00+00:00", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3},
            {"t": "2026-10-01T13:30:00+00:00", "o": 9, "h": 9, "l": 9, "c": 9, "v": 1},
        ]
        df = px.seconds_bars_to_dataframe(bars)
        self.assertEqual(list(df.columns), px.SECONDS_COLUMNS)
        self.assertEqual(len(df), 2)
        self.assertEqual(df["Volume"].tolist(), [3, 7])

    def test_empty(self):
        self.assertEqual(list(px.seconds_bars_to_dataframe([]).columns), px.SECONDS_COLUMNS)


class TestSecondsWindow(unittest.TestCase):
    def test_requests_one_second_unit(self):
        c = FakeClient()
        s = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
        px.fetch_seconds_window(c, "CON.F.US.MES.Z26", s, s + timedelta(hours=1))
        self.assertTrue(all(u == px.UNIT_SECOND and n == 1 for _, _, u, n in c.calls))

    def test_splits_window_when_response_hits_limit(self):
        c = FakeClient(cap=1000)  # pretend the API caps at 1000 bars
        orig = px.BAR_LIMIT
        px.BAR_LIMIT = 1000
        try:
            s = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
            bars = px.fetch_seconds_window(c, "X", s, s + timedelta(seconds=3000),
                                           min_window=timedelta(seconds=1))
        finally:
            px.BAR_LIMIT = orig
        self.assertEqual(len(px.seconds_bars_to_dataframe(bars)), 3000)
        self.assertGreater(len(c.calls), 1)


class TestDailyPull(unittest.TestCase):
    def test_writes_one_file_per_day_and_resumes(self):
        c = FakeClient()
        with tempfile.TemporaryDirectory() as d:
            start = datetime(2026, 10, 2, tzinfo=ET)          # Friday
            end = datetime(2026, 10, 5, tzinfo=ET)            # through Sunday (exclusive Monday)
            now = datetime(2026, 11, 19, tzinfo=UTC)
            written = px.pull_seconds_daily("X", start, end, d, "MES", client=c, now=now)
            names = sorted(os.listdir(d))
            self.assertEqual(names, ["MES_1s_2026-10-02.csv", "MES_1s_2026-10-04.csv"])  # Saturday skipped
            self.assertEqual(len(written), 2)
            df = pd.read_csv(os.path.join(d, names[0]))
            self.assertEqual(list(df.columns), px.SECONDS_COLUMNS)
            self.assertEqual(len(df), 86400)
            n_calls = len(c.calls)
            again = px.pull_seconds_daily("X", start, end, d, "MES", client=c, now=now)
            self.assertEqual(again, [])
            self.assertEqual(len(c.calls), n_calls)  # nothing re-fetched

    def test_skips_unfinished_day(self):
        c = FakeClient()
        with tempfile.TemporaryDirectory() as d:
            now = datetime(2026, 11, 19, 20, 0, tzinfo=UTC)
            px.pull_seconds_daily("X", datetime(2026, 11, 19, tzinfo=ET), datetime(2026, 11, 20, tzinfo=ET),
                                  d, "MES", client=c, now=now)
            self.assertEqual(os.listdir(d), [])


class TestEmptyDay(unittest.TestCase):
    def test_empty_day_writes_nothing(self):
        class Empty(FakeClient):
            def fetch_bars(self, *a, **k):
                self.calls.append(a)
                return []
        c = Empty()
        with tempfile.TemporaryDirectory() as d:
            w = px.pull_seconds_daily("X", datetime(2026, 10, 1, tzinfo=ET), datetime(2026, 10, 2, tzinfo=ET),
                                      d, "MES", client=c, now=datetime(2026, 11, 19, tzinfo=UTC))
            self.assertEqual(w, [])
            self.assertEqual(os.listdir(d), [])


if __name__ == "__main__":
    unittest.main()
