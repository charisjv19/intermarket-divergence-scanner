"""Piece 4 tests. Synthetic signals/bars — no live API, no full scanner."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.compare_versions import (
    GAP_DISCLOSURE,
    compare_versions,
    diff_filled,
    identity_key,
    main,
    materialize_scanner,
)

ET = ZoneInfo("America/New_York")


def _bars(rows):
    recs = []
    for t, o, h, l, c in rows:
        ts = pd.Timestamp(t)
        if ts.tzinfo is None:
            ts = ts.tz_localize(ET)
        recs.append({"time": ts.tz_convert("UTC"), "open": o, "high": h, "low": l, "close": c})
    return pd.DataFrame(recs)


def _sig(**kwargs):
    base = {
        "date": "2026-09-17",
        "sw2_conf_time": datetime(2026, 9, 17, 9, 0, tzinfo=ET),
        "smt_time": datetime(2026, 9, 17, 9, 1, tzinfo=ET),
        "session": "NY Morning",
        "direction": "LONG",
        "instrument": "ES",
        "entry_type": "SMT_IN_FVG",
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 103.0,
        "target_R": 99.0,
    }
    base.update(kwargs)
    return base


SESSION_BARS = _bars(
    [
        ("2026-09-17 09:00", 100.0, 100.2, 99.8, 100.0),
        ("2026-09-17 09:01", 100.0, 100.2, 98.5, 99.0),
        ("2026-09-17 09:02", 99.0, 99.2, 98.8, 99.1),
    ]
)


class TestDiffFilled(unittest.TestCase):
    def test_added_removed_only(self):
        kept = _sig()
        extra_b = _sig(
            sw2_conf_time=datetime(2026, 9, 17, 9, 10, tzinfo=ET),
            smt_time=datetime(2026, 9, 17, 9, 11, tzinfo=ET),
            instrument="NQ",
            entry_price=20000.0,
            stop_loss=19990.0,
            take_profit=20030.0,
        )
        extra_a = _sig(
            sw2_conf_time=datetime(2026, 9, 17, 9, 20, tzinfo=ET),
            smt_time=datetime(2026, 9, 17, 9, 21, tzinfo=ET),
            direction="SHORT",
            entry_price=100.0,
            stop_loss=101.0,
            take_profit=97.0,
        )
        a = pd.DataFrame([kept, extra_a])
        b = pd.DataFrame([kept, extra_b])
        a["outcome"] = ["loss", "loss"]
        a["realized_R"] = [-1.0, -1.0]
        b["outcome"] = ["loss", "win"]
        b["realized_R"] = [-1.0, 1.5]
        review = diff_filled(a, b)
        self.assertEqual(sorted(review["change"].tolist()), ["added", "removed"])
        added = review[review["change"] == "added"].iloc[0]
        removed = review[review["change"] == "removed"].iloc[0]
        self.assertEqual(added["instrument"], "NQ")
        self.assertEqual(removed["direction"], "SHORT")
        self.assertEqual(len(review), 2)

    def test_material_field_rows_omit_unchanged(self):
        a = pd.DataFrame([_sig(stop_loss=99.0, take_profit=103.0, realized_R=-1.0, outcome="loss")])
        b = pd.DataFrame([_sig(stop_loss=98.5, take_profit=103.0, realized_R=-1.5, outcome="loss")])
        review = diff_filled(a, b)
        self.assertEqual(sorted(review["change"].tolist()), ["realized_R", "stop_loss"])
        sl = review[review["change"] == "stop_loss"].iloc[0]
        self.assertEqual(float(sl["a_value"]), 99.0)
        self.assertEqual(float(sl["b_value"]), 98.5)
        self.assertNotIn("entry_price", set(review["change"]))
        self.assertNotIn("entry_type", set(review["change"]))
        self.assertNotIn("take_profit", set(review["change"]))

    def test_identical_is_empty(self):
        row = _sig(realized_R=-1.0, outcome="loss")
        review = diff_filled(pd.DataFrame([row]), pd.DataFrame([row]))
        self.assertEqual(len(review), 0)
        self.assertEqual(list(review.columns)[0], "change")

    def test_entry_type_flip_is_material(self):
        a = pd.DataFrame([_sig(entry_type="SMT_IN_FVG", realized_R=1.5, outcome="win")])
        b = pd.DataFrame([_sig(entry_type="FVG_AFTER_SMT", realized_R=1.5, outcome="win")])
        review = diff_filled(a, b)
        self.assertEqual(list(review["change"]), ["entry_type"])

    def test_identity_normalizes_tz(self):
        et = _sig()
        utc = dict(et)
        utc["sw2_conf_time"] = pd.Timestamp(et["sw2_conf_time"]).tz_convert("UTC")
        self.assertEqual(identity_key(pd.Series(et)), identity_key(pd.Series(utc)))


class TestCompareVersionsE2E(unittest.TestCase):
    def _serial(self, rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            item = dict(row)
            for k, v in item.items():
                if isinstance(v, datetime):
                    item[k] = v.isoformat()
            out.append(item)
        return out

    def _write_scanner(self, folder: Path, name: str, rows: list[dict]) -> Path:
        payload = repr(self._serial(rows))
        path = folder / name
        path.write_text(
            "import pandas as pd\n"
            f"ROWS = {payload}\n"
            "def run(es_path, nq_path):\n"
            "    return pd.DataFrame(ROWS)\n"
        )
        return path

    def test_two_scanners_and_fills(self):
        bars_es = SESSION_BARS
        bars_nq = _bars(
            [
                ("2026-09-17 09:00", 20000.0, 20010.0, 19990.0, 20000.0),
                ("2026-09-17 09:01", 20000.0, 20080.0, 19990.0, 20050.0),
                ("2026-09-17 09:02", 20050.0, 20060.0, 20040.0, 20055.0),
            ]
        )
        kept = _sig()
        added = _sig(
            sw2_conf_time=datetime(2026, 9, 17, 9, 0, tzinfo=ET),
            smt_time=datetime(2026, 9, 17, 9, 0, tzinfo=ET),
            instrument="NQ",
            entry_price=20000.0,
            stop_loss=19960.0,
            take_profit=20080.0,
        )
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            a_py = self._write_scanner(d, "a.py", [kept])
            b_py = self._write_scanner(d, "b.py", [kept, added])
            es_csv = d / "es.csv"
            nq_csv = d / "nq.csv"
            bars_es.to_csv(es_csv, index=False)
            bars_nq.to_csv(nq_csv, index=False)
            review = compare_versions(
                a_scanner=a_py,
                b_scanner=b_py,
                es_path=es_csv,
                nq_path=nq_csv,
            )
            self.assertEqual(list(review["change"]), ["added"])
            self.assertEqual(review.iloc[0]["instrument"], "NQ")
            self.assertEqual(review.iloc[0]["outcome_b"], "win")

    def test_cli_precomputed_signals_writes_review_these(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            a = pd.DataFrame([_sig(stop_loss=99.0)])
            b = pd.DataFrame([_sig(stop_loss=98.0)])
            a.to_csv(d / "a.csv", index=False)
            b.to_csv(d / "b.csv", index=False)
            es = d / "es.csv"
            nq = d / "nq.csv"
            SESSION_BARS.to_csv(es, index=False)
            SESSION_BARS.to_csv(nq, index=False)
            out = d / "review_these.csv"
            rc = main(
                [
                    "--a-signals",
                    str(d / "a.csv"),
                    "--b-signals",
                    str(d / "b.csv"),
                    "--es",
                    str(es),
                    "--nq",
                    str(nq),
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            review = pd.read_csv(out)
            self.assertIn("stop_loss", set(review["change"]))
            self.assertIn("realized_R", set(review["change"]))
            self.assertTrue(out.read_text().startswith("change,"))

    def test_git_ref_materialize(self):
        with tempfile.TemporaryDirectory() as d:
            got = materialize_scanner("backtest/simulate_fills.py", "HEAD", Path(d))
            text = got.read_text()
            self.assertIn("def simulate_fills", text)
            self.assertIn("Piece 2", text)


class TestGapDisclosure(unittest.TestCase):
    def test_mentions_three_gaps(self):
        self.assertIn("partial exits NOT simulated", GAP_DISCLOSURE)
        self.assertIn("concurrent-trade limits NOT enforced", GAP_DISCLOSURE)
        self.assertIn("5m-opposing-SMT blocking NOT implemented", GAP_DISCLOSURE)


if __name__ == "__main__":
    unittest.main()
