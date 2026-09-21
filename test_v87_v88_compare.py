"""Categorize v8.7 vs v8.8 identities. Synthetic frames — no live API."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.v87_v88_compare import (
    categorize,
    cause_removed,
    sw1_cross_session,
    sw1_gap_minutes,
)

ET = ZoneInfo("America/New_York")


def _sig(version, **kwargs):
    base = {
        "date": "2026-02-02",
        "session": "NY Morning",
        "sw2_conf_time": datetime(2026, 2, 2, 8, 20, tzinfo=ET),
        "smt_time": datetime(2026, 2, 2, 8, 21, tzinfo=ET),
        "direction": "LONG",
        "instrument": "ES",
        "entry_type": "FVG_AFTER_SMT",
        "es_sw1_time": "2026-02-02 08:10",
        "nq_sw1_time": "2026-02-02 08:10",
        "es_sw2_price": 100.0,
        "nq_sw2_price": 25000.0,
        "fvg_bar": datetime(2026, 2, 2, 8, 22, tzinfo=ET),
        "pre_fvg_bar": pd.NaT,
        "fvg_found": True,
    }
    base.update(kwargs)
    return base


class TestCategorize(unittest.TestCase):
    def test_four_buckets(self):
        shared = datetime(2026, 2, 2, 8, 20, tzinfo=ET)
        flip = datetime(2026, 2, 2, 8, 30, tzinfo=ET)
        only87 = datetime(2026, 2, 2, 9, 0, tzinfo=ET)
        only88 = datetime(2026, 2, 2, 9, 15, tzinfo=ET)
        v87 = pd.DataFrame(
            [
                _sig("87", sw2_conf_time=shared, entry_type="FVG_AFTER_SMT"),
                _sig("87", sw2_conf_time=flip, entry_type="SMT_IN_FVG"),
                _sig("87", sw2_conf_time=only87, entry_type="FVG_AFTER_SMT"),
            ]
        )
        v88 = pd.DataFrame(
            [
                _sig("88", sw2_conf_time=shared, entry_type="FVG_AFTER_SMT"),
                _sig("88", sw2_conf_time=flip, entry_type="FVG_AFTER_SMT"),
                _sig("88", sw2_conf_time=only88, entry_type="SMT_IN_FVG", fvg_found=False, fvg_bar=pd.NaT),
            ]
        )
        cats = categorize(v87, v88)
        counts = cats["category"].value_counts().to_dict()
        self.assertEqual(counts["UNCHANGED"], 1)
        self.assertEqual(counts["CHANGED"], 1)
        self.assertEqual(counts["REMOVED"], 1)
        self.assertEqual(counts["ADDED"], 1)
        self.assertEqual(cats.columns[0], "category")
        changed = cats[cats["category"] == "CHANGED"].iloc[0]
        self.assertEqual(changed["entry_type_v87"], "SMT_IN_FVG")
        self.assertEqual(changed["entry_type_v88"], "FVG_AFTER_SMT")
        added = cats[cats["category"] == "ADDED"].iloc[0]
        self.assertEqual(added["cause"], "s2_smt_in_fvg_no_post_fvg")

    def test_s3_gap(self):
        row = pd.Series(_sig("87", es_sw1_time="2026-02-02 08:10", nq_sw1_time="2026-02-02 08:12"))
        self.assertEqual(sw1_gap_minutes(row), 2.0)
        cause, _ = cause_removed(row, sdf_times=None)
        self.assertEqual(cause, "s3_tolerance")

    def test_s1_cross_session(self):
        row = pd.Series(
            _sig(
                "87",
                session="NY Afternoon",
                sw2_conf_time=datetime(2026, 2, 2, 13, 20, tzinfo=ET),
                es_sw1_time="2026-02-02 09:10",
                nq_sw1_time="2026-02-02 09:10",
            )
        )
        self.assertTrue(sw1_cross_session(row))
        cause, _ = cause_removed(row, sdf_times=None)
        self.assertEqual(cause, "s1_session_filter")

    def test_confirmation_gate_last_morning_bar(self):
        row = pd.Series(
            _sig(
                "87",
                sw2_conf_time=datetime(2026, 2, 2, 9, 59, tzinfo=ET),
            )
        )
        cause, _ = cause_removed(row, sdf_times=None)
        self.assertEqual(cause, "confirmation_gate")


if __name__ == "__main__":
    unittest.main()
