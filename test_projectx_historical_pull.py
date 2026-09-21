"""Unit tests for projectx_historical_pull.py. No live API calls."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import projectx_historical_pull as px
import swing_marker_detection as smd


ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def _ohlc(highs, lows):
    n = len(highs)
    times = pd.date_range("2026-02-25 14:30", periods=n, freq="min", tz="UTC")
    return pd.DataFrame(
        {
            "time": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": highs,
        }
    )


class FakeResponse:
    def __init__(self, status_code, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class TestComputeSwings(unittest.TestCase):
    def test_matches_swing_marker_detection(self):
        highs = [10, 11, 12, 15, 14, 13, 16, 17, 12, 11, 10, 9]
        lows = [9, 10, 11, 12, 11, 10, 12, 13, 9, 8, 7, 6]
        df = _ohlc(highs, lows)
        got = px.compute_swings(df)
        expected = smd.detect_swings(df)
        self.assertTrue((got["Swing High"] == expected["Swing High"]).all())
        self.assertTrue((got["Swing Low"] == expected["Swing Low"]).all())

    def test_matches_detect_5m_index_rule(self):
        # Same 4-bar rule as _detect_5m_swings_with_index: flag lives on bar i-1.
        highs = [1.0, 2.0, 3.0, 5.0, 4.0, 4.5]
        lows = [0.5, 1.0, 1.5, 2.0, 1.0, 1.2]
        df = _ohlc(highs, lows)
        tagged = px.compute_swings(df)
        # i=4 confirms a high at i=3 (5 > 3, 5 > 2, 5 > 4)
        self.assertEqual(int(tagged.loc[3, "Swing High"]), 1)
        self.assertEqual(int(tagged["Swing High"].sum()), 1)
        # i=4 confirms a low at i=3? 2 < 1.5 is false, so no.
        self.assertEqual(int(tagged.loc[3, "Swing Low"]), 0)

    def test_empty_frame(self):
        df = pd.DataFrame(columns=["time", "open", "high", "low", "close"])
        tagged = px.compute_swings(df)
        self.assertIn("Swing High", tagged.columns)
        self.assertIn("Swing Low", tagged.columns)
        self.assertEqual(len(tagged), 0)


class TestChunksAndBounds(unittest.TestCase):
    def test_six_hour_chunks_cover_range_without_gaps(self):
        start = datetime(2026, 2, 25, 0, 0, tzinfo=UTC)
        end = datetime(2026, 2, 26, 0, 0, tzinfo=UTC)
        chunks = px.iter_chunks(start, end, hours=6)
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[0][0], start)
        self.assertEqual(chunks[-1][1], end)
        for i in range(len(chunks) - 1):
            self.assertEqual(chunks[i][1], chunks[i + 1][0])
            self.assertEqual(chunks[i][1] - chunks[i][0], timedelta(hours=6))

    def test_short_last_chunk(self):
        start = datetime(2026, 2, 25, 0, 0, tzinfo=UTC)
        end = datetime(2026, 2, 25, 7, 0, tzinfo=UTC)
        chunks = px.iter_chunks(start, end, hours=6)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0][1] - chunks[0][0], timedelta(hours=6))
        self.assertEqual(chunks[1][1] - chunks[1][0], timedelta(hours=1))

    def test_date_only_is_inclusive_et_day(self):
        start = px.parse_bound("2026-02-25", is_end=False)
        end = px.parse_bound("2026-02-25", is_end=True)
        self.assertEqual(start.tzinfo, ET)
        self.assertEqual(start, datetime(2026, 2, 25, 0, 0, tzinfo=ET))
        self.assertEqual(end, datetime(2026, 2, 26, 0, 0, tzinfo=ET))

    def test_rejects_inverted_range(self):
        start = datetime(2026, 2, 26, tzinfo=UTC)
        end = datetime(2026, 2, 25, tzinfo=UTC)
        with self.assertRaises(ValueError):
            px.iter_chunks(start, end)


class TestBarsToFrameAndCsv(unittest.TestCase):
    def test_maps_projectx_keys_sorts_and_dedupes(self):
        bars = [
            {"t": "2026-02-25T15:01:00+00:00", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
            {"t": "2026-02-25T15:00:00+00:00", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9},
            {"t": "2026-02-25T15:00:00+00:00", "o": 9, "h": 9, "l": 9, "c": 9, "v": 1},
        ]
        df = px.bars_to_dataframe(bars)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["time"], pd.Timestamp("2026-02-25T15:00:00+00:00"))
        self.assertEqual(list(df.columns), ["time", "open", "high", "low", "close"])

    def test_csv_columns_match_scanner_load(self):
        df = _ohlc([10, 11, 12, 15, 14], [9, 10, 10, 11, 10])
        df = px.compute_swings(df)
        path = "/tmp/test_projectx_pull.csv"
        px.to_scanner_csv(df, path)
        loaded = pd.read_csv(path)
        self.assertEqual(list(loaded.columns), px.OUTPUT_COLUMNS)
        loaded["time"] = pd.to_datetime(loaded["time"], utc=True)
        self.assertTrue(loaded["time"].dt.tz is not None)
        self.assertEqual(set(loaded["Swing High"].unique()).issubset({0, 1}), True)


class TestAuthAndFetch(unittest.TestCase):
    def _client(self, poster):
        session = requests.Session()
        session.post = poster
        return px.ProjectXClient(
            username="user",
            api_key="key",
            api_root="https://api.topstepx.com/api",
            session=session,
            chunk_pause_sec=0,
        )

    def test_authenticate_posts_loginKey_payload(self):
        calls = []

        def poster(url, json=None, headers=None, timeout=None):
            calls.append((url, json, headers))
            return FakeResponse(200, {"success": True, "errorCode": 0, "token": "tok-1"})

        client = self._client(poster)
        token = client.authenticate()
        self.assertEqual(token, "tok-1")
        self.assertEqual(calls[0][0], "https://api.topstepx.com/api/Auth/loginKey")
        self.assertEqual(calls[0][1], {"userName": "user", "apiKey": "key"})
        self.assertNotIn("Authorization", calls[0][2])

    def test_fetch_bars_payload_matches_live_scanner_contract(self):
        calls = []

        def poster(url, json=None, headers=None, timeout=None):
            calls.append((url, json, headers))
            if url.endswith("/Auth/loginKey"):
                return FakeResponse(200, {"success": True, "errorCode": 0, "token": "tok-1"})
            return FakeResponse(
                200,
                {
                    "success": True,
                    "errorCode": 0,
                    "bars": [
                        {"t": "2026-02-25T15:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3}
                    ],
                },
            )

        client = self._client(poster)
        start = datetime(2026, 2, 25, 15, 0, tzinfo=UTC)
        end = datetime(2026, 2, 25, 19, 0, tzinfo=UTC)
        bars = client.fetch_bars("CON.F.US.MES.U26", start, end)
        self.assertEqual(len(bars), 1)
        url, payload, headers = calls[1]
        self.assertTrue(url.endswith("/History/retrieveBars"))
        self.assertEqual(payload["contractId"], "CON.F.US.MES.U26")
        self.assertIs(payload["live"], False)
        self.assertEqual(payload["unit"], 2)
        self.assertEqual(payload["unitNumber"], 1)
        self.assertEqual(payload["limit"], 20_000)
        self.assertIs(payload["includePartialBar"], False)
        self.assertEqual(payload["startTime"], "2026-02-25T15:00:00Z")
        self.assertEqual(payload["endTime"], "2026-02-25T19:00:00Z")
        self.assertEqual(headers["Authorization"], "Bearer tok-1")

    def test_401_reauthenticates_and_retries(self):
        calls = []

        def poster(url, json=None, headers=None, timeout=None):
            calls.append(url)
            if url.endswith("/Auth/loginKey"):
                n = sum(1 for u in calls if u.endswith("/Auth/loginKey"))
                return FakeResponse(200, {"success": True, "errorCode": 0, "token": f"tok-{n}"})
            if sum(1 for u in calls if u.endswith("/History/retrieveBars")) == 1:
                return FakeResponse(401, text="expired")
            return FakeResponse(200, {"success": True, "errorCode": 0, "bars": []})

        client = self._client(poster)
        start = datetime(2026, 2, 25, 15, 0, tzinfo=UTC)
        end = datetime(2026, 2, 25, 16, 0, tzinfo=UTC)
        bars = client.fetch_bars("CON.F.US.MES.U26", start, end)
        self.assertEqual(bars, [])
        login_calls = [u for u in calls if u.endswith("/Auth/loginKey")]
        bar_calls = [u for u in calls if u.endswith("/History/retrieveBars")]
        self.assertEqual(len(login_calls), 2)
        self.assertEqual(len(bar_calls), 2)
        self.assertEqual(client.token, "tok-2")

    def test_pull_range_pages_six_hour_chunks(self):
        windows = []

        def poster(url, json=None, headers=None, timeout=None):
            if url.endswith("/Auth/loginKey"):
                return FakeResponse(200, {"success": True, "errorCode": 0, "token": "tok"})
            windows.append((json["startTime"], json["endTime"]))
            start = json["startTime"]
            return FakeResponse(
                200,
                {
                    "success": True,
                    "errorCode": 0,
                    "bars": [
                        {"t": start, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}
                    ],
                },
            )

        client = self._client(poster)
        start = datetime(2026, 2, 25, 0, 0, tzinfo=UTC)
        end = datetime(2026, 2, 26, 0, 0, tzinfo=UTC)
        with patch("builtins.print"):
            df = client.pull_range("CON.F.US.MES.U26", start, end, chunk_hours=6, progress=False)
        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[0], ("2026-02-25T00:00:00Z", "2026-02-25T06:00:00Z"))
        self.assertEqual(windows[-1], ("2026-02-25T18:00:00Z", "2026-02-26T00:00:00Z"))
        self.assertEqual(len(df), 4)
        self.assertIn("Swing High", df.columns)

    def test_login_failure_raises(self):
        def poster(url, json=None, headers=None, timeout=None):
            return FakeResponse(
                200,
                {"success": False, "errorCode": 3, "errorMessage": "InvalidCredentials", "token": None},
            )

        client = self._client(poster)
        with self.assertRaises(RuntimeError) as ctx:
            client.authenticate()
        self.assertIn("loginKey failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
