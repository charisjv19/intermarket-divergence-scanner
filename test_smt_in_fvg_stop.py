"""Unit tests for v8.8 SMT_IN_FVG stop (FVG-edge ± buffer + shared min-tick floor)."""
import unittest

import pandas as pd

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


class FvgAfterSmtStopAnchorTests(unittest.TestCase):
    def test_prior_swing_is_strictly_before_fvg_timestamp(self):
        from smt_scanner_v8_8 import prior_swing_before_timestamp
        hist = [
            (100.0, 10, pd.Timestamp('2026-03-11 14:20:00-05:00')),
            (101.0, 20, pd.Timestamp('2026-03-11 14:25:00-05:00')),
            (102.0, 30, pd.Timestamp('2026-03-11 14:28:00-05:00')),
        ]
        fvg = pd.Timestamp('2026-03-11 14:28:00-05:00')
        prior = prior_swing_before_timestamp(hist, fvg)
        self.assertEqual(prior[0], 101.0)
        self.assertEqual(prior[1], 20)

    def test_empty_or_all_after_returns_none(self):
        from smt_scanner_v8_8 import prior_swing_before_timestamp
        fvg = pd.Timestamp('2026-03-11 14:00:00-05:00')
        self.assertIsNone(prior_swing_before_timestamp([], fvg))
        hist = [(100.0, 10, pd.Timestamp('2026-03-11 14:01:00-05:00'))]
        self.assertIsNone(prior_swing_before_timestamp(hist, fvg))

    def test_scanner_uses_fvg_bar_hist_not_scan_i_index(self):
        from pathlib import Path
        src = Path('smt_scanner_v8_8.py').read_text()
        self.assertIn('prior_swing_before_timestamp', src)
        self.assertIn("fvg_row = sdf.iloc[fvg['fvg_bar_idx']]", src)
        sl_block = src[src.find("entry_type = 'FVG_AFTER_SMT'"):]
        sl_block = sl_block[:sl_block.find('clock_row = fvg_row')]
        self.assertNotIn('s[1] < fvg', sl_block)
        self.assertIn('prior_swing_before_timestamp', sl_block)


class SmtTimeAnchorTests(unittest.TestCase):
    def test_smt_time_is_confirmation_bar_not_scan_row(self):
        from pathlib import Path
        src = Path('smt_scanner_v8_8.py').read_text()
        self.assertNotIn("'smt_time':   row['et']", src)
        self.assertIn("conf_row = sdf.iloc[conf_bar_idx]", src)
        self.assertIn("clock_row = conf_row", src)
        self.assertIn("'smt_time':   clock_row['et']", src)


class ConfluenceClockTests(unittest.TestCase):
    def test_macro_and_5m_use_clock_row_not_scan_row(self):
        from pathlib import Path
        src = Path('smt_scanner_v8_8.py').read_text()
        self.assertNotIn("get_15m_macro_bias(es15, nq15, row['et'])", src)
        self.assertNotIn("check_5m_smt_confluence(es5, nq5, row['et']", src)
        self.assertIn("es15, nq15, clock_et", src)
        self.assertIn("check_5m_smt_confluence(es5, nq5, clock_et, direction)", src)
        self.assertIn("clock_et = clock_row['et']", src)


if __name__ == '__main__':
    unittest.main()
