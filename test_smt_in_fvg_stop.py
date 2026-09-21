"""Unit tests for v8.8 SMT_IN_FVG stop (FVG-edge ± buffer + shared min-tick floor)."""
import unittest

from smt_scanner_v8_8 import (
    ES_MIN_SL_TICKS,
    ES_SMT_IN_FVG_SL_BUFFER,
    ES_TICK_SIZE,
    NQ_MIN_SL_TICKS,
    NQ_SMT_IN_FVG_SL_BUFFER,
    NQ_TICK_SIZE,
    apply_min_stop,
    compute_smt_in_fvg_stop,
    min_stop_points,
)


class MinStopFloorTests(unittest.TestCase):
    def test_tick_floors(self):
        self.assertEqual(min_stop_points('ES'), ES_MIN_SL_TICKS * ES_TICK_SIZE)
        self.assertEqual(min_stop_points('NQ'), NQ_MIN_SL_TICKS * NQ_TICK_SIZE)
        self.assertEqual(min_stop_points('ES'), 5.0)
        self.assertEqual(min_stop_points('NQ'), 10.0)

    def test_apply_min_stop_widens_long(self):
        stop, risk = apply_min_stop(100.0, 98.0, 'LONG', 5.0)
        self.assertEqual(stop, 95.0)
        self.assertEqual(risk, 5.0)

    def test_apply_min_stop_keeps_wider_short(self):
        stop, risk = apply_min_stop(100.0, 112.0, 'SHORT', 10.0)
        self.assertEqual(stop, 112.0)
        self.assertEqual(risk, 12.0)


class SmtInFvgStopTests(unittest.TestCase):
    def test_long_uses_fvg_low_then_min_floor(self):
        # FVG 100-102, entry 101 (inside). SL = 100 - 1.0 = 99, risk 2 → floor 5.
        stop, risk = compute_smt_in_fvg_stop(
            101.0, 100.0, 102.0, 'LONG', ES_SMT_IN_FVG_SL_BUFFER, 5.0
        )
        self.assertEqual(stop, 96.0)
        self.assertEqual(risk, 5.0)
        self.assertLess(stop, 101.0)

    def test_short_uses_fvg_high_then_min_floor(self):
        stop, risk = compute_smt_in_fvg_stop(
            101.0, 100.0, 102.0, 'SHORT', ES_SMT_IN_FVG_SL_BUFFER, 5.0
        )
        self.assertEqual(stop, 106.0)
        self.assertEqual(risk, 5.0)
        self.assertGreater(stop, 101.0)

    def test_nq_min_stop_is_40_ticks(self):
        stop, risk = compute_smt_in_fvg_stop(
            20000.0, 19999.0, 20004.0, 'LONG',
            NQ_SMT_IN_FVG_SL_BUFFER, min_stop_points('NQ'),
        )
        self.assertEqual(risk, 10.0)
        self.assertEqual(stop, 19990.0)
        self.assertLess(stop, 20000.0)

    def test_wide_fvg_does_not_shrink_to_floor(self):
        # Entry 101 is inside a wide FVG 90-110. SL = 90 - 1 = 89, risk 12 > 5.
        stop, risk = compute_smt_in_fvg_stop(
            101.0, 90.0, 110.0, 'LONG', ES_SMT_IN_FVG_SL_BUFFER, 5.0
        )
        self.assertEqual(stop, 89.0)
        self.assertEqual(risk, 12.0)


class SmtTimeAnchorTests(unittest.TestCase):
    def test_smt_time_is_confirmation_bar_not_scan_row(self):
        from pathlib import Path
        src = Path('smt_scanner_v8_8.py').read_text()
        self.assertNotIn("'smt_time':   row['et']", src)
        self.assertIn("conf_row = sdf.iloc[conf_bar_idx]", src)
        self.assertIn("'smt_time':   conf_row['et']", src)


if __name__ == '__main__':
    unittest.main()
