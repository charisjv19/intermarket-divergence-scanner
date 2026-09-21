"""Piece 3 structural regression tests. No live API."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backtest.structural_tests import (
    StructuralFailure,
    assert_structural_ok,
    check_min_stop_floor,
    check_no_duplicate_signals,
    check_no_null_levels,
    check_swings_match_pine,
    check_timestamp_tolerances,
    load_scanner,
    run_structural_tests,
)

FIXTURE = Path(__file__).resolve().parent / "backtest" / "fixtures" / "pine_swings_ref.csv"

PASSING_SCANNER = """
MAX_SW_TIME_GAP_MINS = 0
SW1_PARALLEL_TOL_MINS = 0
"""

FAILING_SCANNER = """
MAX_SW_TIME_GAP_MINS = 0
SW1_PARALLEL_TOL_MINS = 2
"""


def _signals(**kwargs):
    base = {
        "date": "2026-02-25",
        "sw2_conf_time": "2026-02-25 09:31:00-05:00",
        "direction": "LONG",
        "instrument": "ES",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 107.5,
        "es_sw1_time": "2026-02-25 09:20:00-05:00",
        "nq_sw1_time": "2026-02-25 09:20:00-05:00",
        "es_sw2_time": "2026-02-25 09:31:00-05:00",
        "nq_sw2_time": "2026-02-25 09:31:00-05:00",
    }
    base.update(kwargs)
    return pd.DataFrame([base])


class TestStructuralTests(unittest.TestCase):
    def test_swings_match_pine_fixture(self):
        self.assertEqual(check_swings_match_pine(FIXTURE), [])

    def test_swings_mismatch_is_loud(self):
        df = pd.read_csv(FIXTURE)
        df.loc[2, "Swing High"] = 0
        fails = check_swings_match_pine(df)
        self.assertTrue(any("Swing High mismatch" in f for f in fails))

    def test_null_levels(self):
        ok = _signals()
        self.assertEqual(check_no_null_levels(ok), [])
        bad = _signals()
        bad.loc[0, "stop_loss"] = None
        fails = check_no_null_levels(bad)
        self.assertTrue(any("stop_loss" in f for f in fails))

    def test_duplicates(self):
        a = _signals()
        b = _signals()
        fails = check_no_duplicate_signals(pd.concat([a, b], ignore_index=True))
        self.assertTrue(any("duplicate" in f for f in fails))
        self.assertEqual(check_no_duplicate_signals(a), [])

    def test_missing_sw2_conf_time_is_loud(self):
        df = _signals().drop(columns=["sw2_conf_time"])
        fails = check_no_duplicate_signals(df)
        self.assertTrue(any("sw2_conf_time" in f for f in fails))

    def test_min_stop_floor(self):
        self.assertEqual(check_min_stop_floor(_signals()), [])
        tight = _signals(stop_loss=99.0)  # 1.0 pt ES, floor is 5.0
        fails = check_min_stop_floor(tight)
        self.assertTrue(any("min-stop" in f for f in fails))
        nq_ok = _signals(instrument="NQ", stop_loss=90.0)
        self.assertEqual(check_min_stop_floor(nq_ok), [])
        nq_bad = _signals(instrument="NQ", stop_loss=95.0)  # 5 pt, floor 10
        self.assertTrue(check_min_stop_floor(nq_bad))

    def test_timestamp_constants_and_rows(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good.py"
            bad = Path(td) / "bad.py"
            good.write_text(PASSING_SCANNER)
            bad.write_text(FAILING_SCANNER)
            self.assertEqual(check_timestamp_tolerances(load_scanner(good)), [])
            fails = check_timestamp_tolerances(load_scanner(bad))
            self.assertTrue(any("SW1_PARALLEL_TOL_MINS" in f for f in fails))
        skewed = _signals(nq_sw1_time="2026-02-25 09:22:00-05:00")
        fails = check_timestamp_tolerances(signals=skewed)
        self.assertTrue(any("es_sw1_time != nq_sw1_time" in f for f in fails))

    def test_assert_structural_ok_raises(self):
        with self.assertRaises(StructuralFailure):
            assert_structural_ok(signals=_signals(stop_loss=99.0))

    def test_run_all_on_clean_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            scanner = Path(td) / "s.py"
            scanner.write_text(PASSING_SCANNER)
            fails = run_structural_tests(
                signals=_signals(),
                pine_csv=FIXTURE,
                scanner_path=scanner,
            )
            self.assertEqual(fails, [])


if __name__ == "__main__":
    unittest.main()
