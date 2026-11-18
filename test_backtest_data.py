"""Piece 1 cache tests. Fake client — no live API required."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backtest.data import cache_path, get_bars, load_csv, pull_data
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


class TestCsvAndDualSource(unittest.TestCase):
    def _write_tv_csv(self, path, times, highs=None):
        n = len(times)
        highs = highs or [10.0 + i for i in range(n)]
        df = pd.DataFrame(
            {
                "time": times,
                "open": highs,
                "high": highs,
                "low": [h - 1 for h in highs],
                "close": highs,
                "Swing High": [0] * n,
                "Swing Low": [0] * n,
            }
        )
        df.loc[1, "Swing High"] = 1
        df.to_csv(path, index=False)
        return df

    def test_load_csv_keeps_tv_swing_flags(self):
        times = pd.date_range("2026-02-25 14:30", periods=5, freq="min", tz="UTC")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MES_tv.csv"
            self._write_tv_csv(path, times)
            loaded = load_csv(path)
            self.assertEqual(int(loaded.loc[1, "Swing High"]), 1)
            self.assertEqual(int(loaded["Swing High"].sum()), 1)
            self.assertEqual(list(loaded.columns), px.OUTPUT_COLUMNS)

    def test_load_csv_slices_et_day(self):
        times = pd.date_range("2026-02-24 00:00", periods=60 * 48, freq="min", tz="UTC")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MES_tv.csv"
            raw = pd.DataFrame(
                {
                    "time": times,
                    "open": 1.0,
                    "high": 2.0,
                    "low": 0.5,
                    "close": 1.5,
                    "Swing High": 0,
                    "Swing Low": 0,
                }
            )
            raw.to_csv(path, index=False)
            loaded = load_csv(path, "2026-02-25", "2026-02-25")
            start = px.parse_bound("2026-02-25", is_end=False)
            end = px.parse_bound("2026-02-25", is_end=True)
            self.assertTrue((loaded["time"] >= start).all())
            self.assertTrue((loaded["time"] < end).all())
            self.assertGreater(len(loaded), 0)
            self.assertLess(len(loaded), len(raw))

    def test_source_csv_does_not_call_api(self):
        client = FakeClient()
        times = pd.date_range("2026-02-25 14:30", periods=4, freq="min", tz="UTC")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MES_tv.csv"
            self._write_tv_csv(path, times)
            df = get_bars(
                source="csv",
                csv_path=path,
                start_date="2026-02-25",
                end_date="2026-02-25",
                client=client,
            )
            self.assertEqual(client.calls, 0)
            self.assertEqual(len(df), 4)

    def test_auto_uses_api_when_bars_exist(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            df = get_bars(
                "CON.F.US.MES.Z26",
                "2026-09-17",
                "2026-09-17",
                source="auto",
                cache_dir=tmp,
                client=client,
            )
            self.assertEqual(client.calls, 1)
            self.assertEqual(len(df), 3)

    def test_auto_falls_back_to_csv_when_api_empty(self):
        client = FakeClient(frame=px.compute_swings(
            pd.DataFrame(columns=["time", "open", "high", "low", "close"])
        ))
        times = pd.date_range("2026-02-25 14:30", periods=4, freq="min", tz="UTC")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MES_tv.csv"
            self._write_tv_csv(path, times)
            df = get_bars(
                "CON.F.US.MES.H26",
                "2026-02-25",
                "2026-02-25",
                source="auto",
                csv_path=path,
                cache_dir=tmp,
                client=client,
            )
            self.assertEqual(client.calls, 1)
            self.assertEqual(len(df), 4)

    def test_auto_raises_if_api_empty_and_no_csv(self):
        client = FakeClient(frame=px.compute_swings(
            pd.DataFrame(columns=["time", "open", "high", "low", "close"])
        ))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as ctx:
                get_bars(
                    "CON.F.US.MES.H26",
                    "2026-02-25",
                    "2026-02-25",
                    source="auto",
                    cache_dir=tmp,
                    client=client,
                )
            self.assertIn("csv_path", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
