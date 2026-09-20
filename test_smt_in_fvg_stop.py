"""Unit tests for v8.8 SMT_IN_FVG option-C stop + shared min-stop floor."""
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


class OptionCSmtInFvgStopTests(unittest.TestCase):
    def test_long_inside_gap_uses_fvg_low(self):
        # FVG 100-102, entry 101 (still in gap). Anchor = min(100, 101) = 100.
        stop, risk = compute_smt_in_fvg_stop(
            101.0, 100.0, 102.0, 'LONG', ES_SMT_IN_FVG_SL_BUFFER, 5.0
        )
        # 100 - 1.0 = 99, risk 2.0 → floor to 5.0
        self.assertEqual(stop, 96.0)
        self.assertEqual(risk, 5.0)
        self.assertLess(stop, 101.0)

    def test_long_straddle_anchors_on_entry_not_fvg(self):
        # Signal tagged FVG 100-102; confirmation close fell through to 98.5.
        # Old SL = 100 - 1 = 99, which is ABOVE entry. Option C: min(100, 98.5) - 1.
        old_sl = round(100.0 - ES_SMT_IN_FVG_SL_BUFFER, 2)
        self.assertGreater(old_sl, 98.5)

        stop, risk = compute_smt_in_fvg_stop(
            98.5, 100.0, 102.0, 'LONG', ES_SMT_IN_FVG_SL_BUFFER, 5.0
        )
        self.assertLess(stop, 98.5)
        self.assertGreaterEqual(risk, 5.0)
        self.assertEqual(stop, 93.5)  # 98.5 - 5.0 floor (raw 97.5 was only 1.0 risk)

    def test_short_inside_gap_uses_fvg_high(self):
        stop, risk = compute_smt_in_fvg_stop(
            101.0, 100.0, 102.0, 'SHORT', ES_SMT_IN_FVG_SL_BUFFER, 5.0
        )
        # max(102, 101) + 1 = 103, risk 2.0 → floor to 5.0
        self.assertEqual(stop, 106.0)
        self.assertEqual(risk, 5.0)
        self.assertGreater(stop, 101.0)

    def test_short_straddle_anchors_on_entry_not_fvg(self):
        # FVG 100-102; confirmation close rallied through to 103.5.
        old_sl = round(102.0 + ES_SMT_IN_FVG_SL_BUFFER, 2)
        self.assertLess(old_sl, 103.5)

        stop, risk = compute_smt_in_fvg_stop(
            103.5, 100.0, 102.0, 'SHORT', ES_SMT_IN_FVG_SL_BUFFER, 5.0
        )
        self.assertGreater(stop, 103.5)
        self.assertGreaterEqual(risk, 5.0)
        self.assertEqual(stop, 108.5)

    def test_nq_min_stop_is_40_ticks(self):
        stop, risk = compute_smt_in_fvg_stop(
            20000.0, 19999.0, 20004.0, 'LONG',
            NQ_SMT_IN_FVG_SL_BUFFER, min_stop_points('NQ'),
        )
        self.assertEqual(risk, 10.0)
        self.assertEqual(stop, 19990.0)
        self.assertLess(stop, 20000.0)

    def test_wide_fvg_does_not_shrink_to_floor(self):
        # FVG far below entry: 90-92, entry 101. Anchor 90 - 1 = 89, risk 12 > 5.
        stop, risk = compute_smt_in_fvg_stop(
            101.0, 90.0, 92.0, 'LONG', ES_SMT_IN_FVG_SL_BUFFER, 5.0
        )
        self.assertEqual(stop, 89.0)
        self.assertEqual(risk, 12.0)


if __name__ == '__main__':
    unittest.main()
