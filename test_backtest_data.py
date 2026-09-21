"""Piece 1 cache tests. Fake client — no live API required."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from backtest.data import cache_path, pull_data
import projectx_historical_pull as px


def _bars(n=3, start="2026-09-17T13:30:00+00:00"):
    times = pd.date_range(start, periods=n, freq="min", tz="UTC")
    df = pd.DataFrame(
        {
            "time": times,
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
        }
    )
    return px.compute_swings(df)


class FakeClient:
    def __init__(self, frame=None):
        self.token = "tok"
        self.calls = 0
        self.frame = frame if frame is not None else _bars()

    def authenticate(self):
        self.token = "tok"
        return self.token

    def pull_range(self, contract_id, start, end, chunk_hours=6, progress=True):
        self.calls += 1
        self.last = (contract_id, start, end)
        return self.frame.copy()


class TestPullDataCache(unittest.TestCase):
    def test_miss_then_hit_skips_second_pull(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            a = pull_data(
                "CON.F.US.MES.Z26",
                "2026-09-17",
                "2026-09-17",
                cache_dir=tmp,
                client=client,
            )
            self.assertEqual(client.calls, 1)
            self.assertEqual(len(a), 3)
            self.assertEqual(list(a.columns), px.OUTPUT_COLUMNS)

            b = pull_data(
                "CON.F.US.MES.Z26",
                "2026-09-17",
                "2026-09-17",
                cache_dir=tmp,
                client=client,
            )
            self.assertEqual(client.calls, 1)
            pd.testing.assert_frame_equal(a, b)

    def test_different_range_or_contract_is_a_miss(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            pull_data("CON.F.US.MES.Z26", "2026-09-17", "2026-09-17", cache_dir=tmp, client=client)
            pull_data("CON.F.US.MES.Z26", "2026-09-16", "2026-09-16", cache_dir=tmp, client=client)
            pull_data("CON.F.US.MNQ.Z26", "2026-09-17", "2026-09-17", cache_dir=tmp, client=client)
            self.assertEqual(client.calls, 3)

    def test_refresh_bypasses_cache(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            pull_data("CON.F.US.MES.Z26", "2026-09-17", "2026-09-17", cache_dir=tmp, client=client)
            pull_data(
                "CON.F.US.MES.Z26",
                "2026-09-17",
                "2026-09-17",
                cache_dir=tmp,
                client=client,
                refresh=True,
            )
            self.assertEqual(client.calls, 2)

    def test_cache_filename_uses_contract_and_bounds(self):
        start = datetime(2026, 9, 17, 0, 0, tzinfo=px.ET)
        end = datetime(2026, 9, 18, 0, 0, tzinfo=px.ET)
        path = cache_path("CON.F.US.MES.Z26", px.to_utc(start), px.to_utc(end))
        name = path.name
        self.assertIn("CON.F.US.MES.Z26", name)
        self.assertTrue(name.endswith(".csv"))
        self.assertNotIn(":", name)

    def test_date_strings_match_parse_bound(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("builtins.print"):
                pull_data("CON.F.US.MES.Z26", "2026-09-17", "2026-09-17", cache_dir=tmp, client=client)
            _, start, end = client.last
            self.assertEqual(start, px.parse_bound("2026-09-17", is_end=False))
            self.assertEqual(end, px.parse_bound("2026-09-17", is_end=True))


if __name__ == "__main__":
    unittest.main()
