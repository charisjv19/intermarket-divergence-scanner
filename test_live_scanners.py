"""Live v8.7 / v8.8 scanners. Fake ProjectX client — no live API."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from live.feed import pick_active_contract, snapshot
from live.relay import (
    format_text,
    identity_str,
    post_webhook,
    row_to_payload,
    send_test_webhook,
)
from live.runner import (
    V88_OOS_SHA256,
    attach_levels,
    load_local_env,
    load_scanner,
    merge_webhook_urls,
    new_rows,
    normalize_version,
    process_once,
    seconds_until_next_close,
)


UTC = timezone.utc


def _signal_row(**kwargs) -> pd.Series:
    base = {
        "date": "2026-09-29",
        "sw2_conf_time": pd.Timestamp("2026-09-29 13:24:00-04:00"),
        "smt_time": pd.Timestamp("2026-09-29 13:25:00-04:00"),
        "direction": "LONG",
        "instrument": "ES",
        "entry_type": "FVG_AFTER_SMT",
        "session": "NY Afternoon",
        "entry_50": 6700.50,
        "es_sw2_price": 6694.00,
        "nq_sw2_price": 24800.00,
        "es_close": 6701.00,
        "nq_close": 24810.00,
        "combined_15m_bias": "BULLISH",
        "es_15m_bias": "STRONGLY BULLISH",
        "nq_15m_bias": "BULLISH SLOWING",
        "smt5m_status": "none",
        "es_sw1_time": "2026-09-29 13:10",
        "es_sw1_price": 6698.00,
        "es_sw2_time": "2026-09-29 13:24",
        "nq_sw1_time": "2026-09-29 13:10",
        "nq_sw1_price": 24820.00,
        "nq_sw2_time": "2026-09-29 13:24",
        "confirmations_count": 1,
        "alt_sw1_times": "",
        "fvg_bar": "2026-09-29 13:25",
        "fvg_low": 6698.00,
        "fvg_high": 6703.00,
        "pre_fvg_bar": None,
        "pre_fvg_low": None,
        "pre_fvg_high": None,
        "stop_loss": 6689.00,
        "take_profit": 6734.00,
        "target_R": 3.0,
        "entry_price": 6700.50,
    }
    base.update(kwargs)
    return pd.Series(base)


class StubClient:
    def __init__(self, n_bars: int = 8):
        times = pd.date_range("2026-09-29 17:00", periods=n_bars, freq="min", tz="UTC")
        self._bars = [
            {
                "t": ts.isoformat(),
                "o": 100.0 + i,
                "h": 101.0 + i,
                "l": 99.0 + i,
                "c": 100.5 + i,
            }
            for i, ts in enumerate(times)
        ]
        self.fetch_calls = []

    def fetch_bars(self, contract_id, start_time, end_time, limit=20000):
        self.fetch_calls.append((contract_id, start_time, end_time))
        return list(self._bars)

    def search_contracts(self, search_text, live=False):
        symbol = "MES" if "MES" in search_text.upper() else "MNQ"
        return [
            {
                "id": f"CON.F.US.{symbol}.Z26",
                "name": f"{symbol}Z6",
                "activeContract": True,
                "symbolId": f"F.US.{symbol}",
            }
        ]


class FakeScanner:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.calls = 0
        self.paths = []

    def run(self, es_path, nq_path):
        self.calls += 1
        self.paths.append((es_path, nq_path))
        return self.frame.copy(), 0, 0, 0, 0


class TestPickContract(unittest.TestCase):
    def test_prefers_active_exact_symbol(self):
        rows = [
            {
                "id": "CON.F.US.MES.U26",
                "symbolId": "F.US.MES",
                "name": "MESU6",
                "activeContract": False,
            },
            {
                "id": "CON.F.US.MES.Z26",
                "symbolId": "F.US.MES",
                "name": "MESZ6",
                "activeContract": True,
            },
            {
                "id": "CON.F.US.MESH.Z26",
                "symbolId": "F.US.MESH",
                "name": "MESHZ6",
                "activeContract": True,
            },
        ]
        picked = pick_active_contract(rows, "MES")
        self.assertEqual(picked["id"], "CON.F.US.MES.Z26")


class TestRelay(unittest.TestCase):
    def test_identity_stable_across_timezone_strings(self):
        a = _signal_row()
        b = _signal_row(sw2_conf_time="2026-09-29T17:24:00Z")
        self.assertEqual(identity_str(a), identity_str(b))

    def test_format_includes_version_and_levels(self):
        payload = row_to_payload(_signal_row(), version="v8.8")
        text = format_text(payload)
        self.assertIn("[v8.8]", text)
        self.assertIn("FVG_AFTER_SMT LONG MES", text)
        self.assertIn("entry 6700.5", text)

    def test_format_includes_swings_and_fvg(self):
        payload = row_to_payload(_signal_row(), version="v8.8")
        text = format_text(payload)
        self.assertIn("MES  sw1 2026-09-29 13:10 @ 6698", text)
        self.assertIn("sw2 2026-09-29 13:24 @ 6694", text)
        self.assertIn("MNQ  sw1 2026-09-29 13:10 @ 24820", text)
        self.assertIn("sw1s: 1  (primary only)", text)
        self.assertIn("FVG target: after SMT  2026-09-29 13:25  6698-6703", text)

    def test_format_reports_15m_macro_regime(self):
        payload = row_to_payload(_signal_row(), version="v8.8")
        text = format_text(payload)
        self.assertIn("15m macro: BULLISH  (aligned LONG)", text)
        self.assertIn("MES STRONGLY BULLISH   MNQ BULLISH SLOWING", text)
        self.assertIn("5m SMT: none", text)
        short = row_to_payload(
            _signal_row(
                direction="SHORT",
                combined_15m_bias="BEARISH (SLOWING)",
                es_15m_bias="STRONGLY BEARISH",
                nq_15m_bias="BEARISH SLOWING",
            ),
            version="v8.8",
        )
        short_text = format_text(short)
        self.assertIn("15m macro: BEARISH (SLOWING)  (aligned SHORT)", short_text)
        self.assertIn("MES STRONGLY BEARISH   MNQ BEARISH SLOWING", short_text)

    def test_format_lists_each_sw1_when_multiple(self):
        payload = row_to_payload(
            _signal_row(confirmations_count=3, alt_sw1_times="13:16, 13:21"),
            version="v8.7",
        )
        text = format_text(payload)
        self.assertIn("sw1s: 3  13:10 (primary), 13:16, 13:21", text)
        self.assertNotIn("primary only", text)

    def test_format_smt_in_fvg_uses_preexisting_gap(self):
        payload = row_to_payload(
            _signal_row(
                entry_type="SMT_IN_FVG",
                fvg_bar=None,
                fvg_low=float("nan"),
                fvg_high=float("nan"),
                pre_fvg_bar="2026-09-29T13:18:00-04:00",
                pre_fvg_low=6699.25,
                pre_fvg_high=6701.50,
            ),
            version="v8.8",
        )
        text = format_text(payload)
        self.assertIn("FVG target: pre-existing  2026-09-29 13:18  6699.25-6701.5", text)
        self.assertNotIn("after SMT", text)

    def test_webhook_posts_slack_text(self):
        seen = {}

        def fake_post(url, json=None, timeout=None):
            seen["url"] = url
            seen["json"] = json
            seen["timeout"] = timeout

            class Resp:
                status_code = 200
                text = "ok"

            return Resp()

        with patch("live.relay.requests.post", fake_post):
            post_webhook("https://hooks.example/x", row_to_payload(_signal_row(), version="v8.7"))
        self.assertEqual(seen["url"], "https://hooks.example/x")
        self.assertIn("[v8.7]", seen["json"]["text"])
        self.assertTrue(seen["json"]["mrkdwn"])
        self.assertNotIn("payload", seen["json"])

    def test_send_test_webhook_requires_url(self):
        with self.assertRaises(RuntimeError):
            send_test_webhook("v8.8", [])

    def test_send_test_webhook_posts(self):
        posted = []

        def fake_post(url, json=None, timeout=None):
            posted.append((url, json))

            class Resp:
                status_code = 200
                text = "ok"

            return Resp()

        with patch("live.relay.requests.post", fake_post):
            send_test_webhook("v8.8", ["https://hooks.example/x"])
        self.assertEqual(posted[0][0], "https://hooks.example/x")
        text = posted[0][1]["text"]
        self.assertIn("TEST_WEBHOOK", text)
        self.assertIn("MES  sw1", text)
        self.assertIn("sw1s: 3", text)
        self.assertIn("FVG target:", text)
        self.assertIn("15m macro: BULLISH  (aligned LONG)", text)
        self.assertIn("MES STRONGLY BULLISH   MNQ BULLISH SLOWING", text)


class TestLocalEnv(unittest.TestCase):
    def test_load_dotenv_does_not_override_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("SLACK_WEBHOOK_URL=https://from-file\n")
            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://already-set"}, clear=False):
                load_local_env(root)
                self.assertEqual(os.environ["SLACK_WEBHOOK_URL"], "https://already-set")

    def test_merge_webhook_urls_combines_env_and_cli(self):
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://env", "SIGNAL_WEBHOOK_URL": ""}, clear=False):
            os.environ.pop("SIGNAL_WEBHOOK_URL", None)
            urls = merge_webhook_urls(["https://cli"])
        self.assertEqual(urls[0], "https://env")
        self.assertIn("https://cli", urls)


class TestRunner(unittest.TestCase):
    def test_normalize_version(self):
        self.assertEqual(normalize_version("8.7"), "v8.7")
        self.assertEqual(normalize_version("v88"), "v8.8")
        with self.assertRaises(ValueError):
            normalize_version("v8.9")

    def test_new_rows_skips_seen_identities(self):
        frame = pd.DataFrame([_signal_row(), _signal_row(instrument="NQ", nq_sw2_price=24810)])
        seen = {identity_str(frame.iloc[0])}
        fresh = new_rows(frame, seen)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh.iloc[0]["instrument"], "NQ")

    def test_v87_reconstruct_overwrites_missing_scanner_levels(self):
        row = _signal_row()
        row = row.drop(labels=["entry_price", "stop_loss", "take_profit", "target_R"])
        out = attach_levels(pd.DataFrame([row]), "v8.7")
        self.assertEqual(out.iloc[0]["entry_price"], 6700.50)
        self.assertEqual(out.iloc[0]["stop_loss"], 6689.00)  # 6694 - 5.0
        self.assertEqual(out.iloc[0]["target_R"], 3.0)
        self.assertEqual(out.iloc[0]["levels_source"], "reconstructed_v87_formula")

    def test_v88_keeps_scanner_levels(self):
        out = attach_levels(pd.DataFrame([_signal_row()]), "v8.8")
        self.assertEqual(out.iloc[0]["stop_loss"], 6689.00)
        self.assertEqual(out.iloc[0]["levels_source"], "scanner_v88_oos")

    def test_v88_module_is_tagged_oos_not_main(self):
        root = Path(__file__).resolve().parent
        data = (root / "smt_scanner_v8_8.py").read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(digest, V88_OOS_SHA256)
        scanner = load_scanner("v8.8")
        self.assertEqual(scanner.SMT_IN_FVG_CONFIRM_MINS, 1.0)
        self.assertTrue(hasattr(scanner, "fvg_window_is_contiguous"))
        self.assertTrue(hasattr(scanner, "merge_es_nq_1m"))

    def test_process_once_seeds_then_alerts_new(self):
        first = pd.DataFrame([_signal_row()])
        second_new = _signal_row(
            instrument="NQ",
            sw2_conf_time=pd.Timestamp("2026-09-29 13:40:00-04:00"),
            entry_50=24820.0,
            nq_sw2_price=24810.0,
        )
        scanner = FakeScanner(first)
        client = StubClient()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "bars_dir": root / "bars",
                "state_path": root / "seen.json",
                "jsonl_path": root / "alerts.jsonl",
            }
            s1 = process_once(
                "v8.8",
                client=client,
                scanner=scanner,
                paths=paths,
                seed_seen=True,
                webhook_urls=[],
                echo=False,
            )
            self.assertTrue(s1["seeded"])
            self.assertEqual(s1["n_new"], 0)
            self.assertEqual(s1["n_signals"], 1)
            self.assertFalse(paths["jsonl_path"].exists())

            scanner.frame = pd.DataFrame([_signal_row(), second_new])
            posted = []

            def fake_post(url, json=None, timeout=None):
                posted.append(json)

                class Resp:
                    status_code = 200
                    text = "ok"

                return Resp()

            with patch("live.relay.requests.post", fake_post):
                s2 = process_once(
                    "v8.8",
                    client=client,
                    scanner=scanner,
                    paths=paths,
                    seed_seen=True,
                    webhook_urls=["https://hooks.example/x"],
                    echo=False,
                )
            self.assertFalse(s2["seeded"])
            self.assertEqual(s2["n_new"], 1)
            self.assertEqual(len(posted), 1)
            self.assertIn("NQ", posted[0]["text"])
            lines = paths["jsonl_path"].read_text().strip().splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["version"], "v8.8")
            self.assertEqual(payload["instrument"], "NQ")

    def test_process_once_skips_unchanged_last_bar(self):
        scanner = FakeScanner(pd.DataFrame([_signal_row()]))
        client = StubClient()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "bars_dir": root / "bars",
                "state_path": root / "seen.json",
                "jsonl_path": root / "alerts.jsonl",
            }
            first = process_once(
                "v8.8",
                client=client,
                scanner=scanner,
                paths=paths,
                seed_seen=True,
                webhook_urls=[],
                echo=False,
            )
            skipped = process_once(
                "v8.8",
                client=client,
                scanner=scanner,
                paths=paths,
                seed_seen=True,
                webhook_urls=[],
                echo=False,
                last_bar_utc=first["last_bar_utc"],
            )
            self.assertTrue(skipped["skipped"])
            self.assertEqual(scanner.calls, 1)

    def test_snapshot_writes_scanner_columns(self):
        client = StubClient()
        with tempfile.TemporaryDirectory() as tmp:
            meta = snapshot(client, bars_dir=Path(tmp) / "bars", lookback_hours=1)
            es = pd.read_csv(meta["es_path"])
            self.assertEqual(
                list(es.columns),
                ["time", "open", "high", "low", "close", "Swing High", "Swing Low"],
            )
            self.assertGreater(len(es), 0)
            self.assertEqual(meta["es_contract"], "CON.F.US.MES.Z26")

    def test_wait_until_next_minute(self):
        now = datetime(2026, 9, 29, 14, 30, 40, tzinfo=UTC)
        wait = seconds_until_next_close(now, extra_sec=5)
        self.assertAlmostEqual(wait, 25.0, places=3)


if __name__ == "__main__":
    unittest.main()
