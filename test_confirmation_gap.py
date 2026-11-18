"""Regression: SMT_IN_FVG confirmation is exactly 1 minute after sw2.

FVG_AFTER_SMT is not on this clock. Piece 3 should call
check_sw2_confirmation_gap() on every scanner output CSV.
sdf is session + pre_session only, so index+1 can jump 14:29 → next 07:30.
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from smt_scanner_v8_8 import (
    MAX_CONF_GAP_MINS,
    SESSION_CUTOFF_MINS,
    SMT_IN_FVG_CONFIRM_MINS,
    SESSIONS,
    check_sw2_confirmation_gap,
    classify_bar,
    confirmation_bar_is_contiguous,
    confirmation_gap_minutes,
    fvg_in_session,
)

ET = ZoneInfo('America/New_York')


def _et(s):
    return pd.Timestamp(s, tz=ET)


class ConfirmationGapHelperTests(unittest.TestCase):
    def test_threshold_is_exactly_one_minute(self):
        self.assertEqual(SMT_IN_FVG_CONFIRM_MINS, 1.0)
        self.assertEqual(MAX_CONF_GAP_MINS, 1.0)

    def test_next_one_minute_bar_is_contiguous(self):
        sw2 = _et('2026-03-05 14:28:00')
        conf = _et('2026-03-05 14:29:00')
        self.assertEqual(confirmation_gap_minutes(sw2, conf), 1.0)
        self.assertTrue(confirmation_bar_is_contiguous(sw2, conf))

    def test_two_minute_hole_is_rejected(self):
        sw2 = _et('2026-03-05 14:20:00')
        conf = _et('2026-03-05 14:22:00')
        self.assertFalse(confirmation_bar_is_contiguous(sw2, conf))

    def test_five_minute_hole_is_rejected(self):
        sw2 = _et('2026-03-05 14:20:00')
        conf = _et('2026-03-05 14:25:00')
        self.assertFalse(confirmation_bar_is_contiguous(sw2, conf))

    def test_same_bar_is_rejected(self):
        ts = _et('2026-03-05 14:29:00')
        self.assertFalse(confirmation_bar_is_contiguous(ts, ts))

    def test_mar5_outlier_1021_min_jump_is_rejected(self):
        sw2 = _et('2026-03-05 14:29:00')
        conf = _et('2026-03-06 07:30:00')
        self.assertEqual(confirmation_gap_minutes(sw2, conf), 1021.0)
        self.assertFalse(confirmation_bar_is_contiguous(sw2, conf))


class SessionWindowIndexJumpTests(unittest.TestCase):
    """Reproduce why sw2_conf_idx+1 is 07:30, not 14:30."""

    def test_afternoon_session_last_bar_is_1429(self):
        self.assertEqual(SESSION_CUTOFF_MINS, 30)
        names = [s[0] for s in SESSIONS]
        self.assertIn('NY Afternoon', names)
        self.assertEqual(classify_bar(14, 29), ('session', 'NY Afternoon'))
        self.assertEqual(classify_bar(14, 30), (None, None))
        self.assertEqual(classify_bar(7, 30), ('pre_session', 'NY Morning'))

    def test_fvg_in_session_matches_classify_bar_cutoff(self):
        self.assertTrue(fvg_in_session(_et('2026-03-05 09:59:00')))
        self.assertFalse(fvg_in_session(_et('2026-03-05 10:00:00')))
        self.assertTrue(fvg_in_session(_et('2026-03-05 14:29:00')))
        self.assertFalse(fvg_in_session(_et('2026-03-05 14:30:00')))
        self.assertFalse(fvg_in_session(_et('2026-03-06 07:30:00')))
        self.assertFalse(fvg_in_session(_et('2026-03-05 12:30:00')))
        self.assertTrue(fvg_in_session(_et('2026-03-05 13:00:00')))

    def test_filtered_sdf_jumps_1429_to_next_0730(self):
        times = [
            datetime(2026, 3, 5, 14, 28, tzinfo=ET),
            datetime(2026, 3, 5, 14, 29, tzinfo=ET),
            datetime(2026, 3, 5, 14, 30, tzinfo=ET),  # exists in raw 1-min; dropped
            datetime(2026, 3, 6, 7, 30, tzinfo=ET),
        ]
        raw = pd.DataFrame({'et': times})
        raw['bar_type'] = [classify_bar(t.hour, t.minute)[0] for t in times]
        sdf = raw[raw['bar_type'].notna()].reset_index(drop=True)
        self.assertEqual(list(sdf['et'].dt.strftime('%H:%M')), ['14:28', '14:29', '07:30'])
        sw2_idx = 1
        conf_idx = sw2_idx + 1
        gap = confirmation_gap_minutes(sdf.iloc[sw2_idx]['et'], sdf.iloc[conf_idx]['et'])
        self.assertEqual(gap, 1021.0)
        self.assertFalse(
            confirmation_bar_is_contiguous(sdf.iloc[sw2_idx]['et'], sdf.iloc[conf_idx]['et'])
        )


class Piece3ConfirmationGapCheckTests(unittest.TestCase):
    def test_passes_one_minute_confirmation(self):
        signals = pd.DataFrame({
            'entry_type': ['SMT_IN_FVG'],
            'sw2_conf_time': ['2026-03-05 14:28:00-05:00'],
            'smt_time': ['2026-03-05 14:29:00-05:00'],
        })
        self.assertEqual(check_sw2_confirmation_gap(signals), [])

    def test_fails_the_mar5_nq_outlier_on_smt_in_fvg(self):
        signals = pd.DataFrame({
            'entry_type': ['SMT_IN_FVG'],
            'sw2_conf_time': ['2026-03-05 14:29:00-05:00'],
            'smt_time': ['2026-03-06 07:30:00-05:00'],
            'direction': ['SHORT'],
            'instrument': ['NQ'],
        })
        fails = check_sw2_confirmation_gap(signals)
        self.assertEqual(len(fails), 1)
        self.assertIn('1 SMT_IN_FVG', fails[0])
        self.assertIn('2026-03-05 14:29', fails[0])

    def test_ignores_fvg_after_smt_clock(self):
        signals = pd.DataFrame({
            'entry_type': ['FVG_AFTER_SMT'],
            'sw2_conf_time': ['2026-03-05 14:20:00-05:00'],
            'smt_time': ['2026-03-05 14:28:00-05:00'],
        })
        self.assertEqual(check_sw2_confirmation_gap(signals), [])

    def test_scanner_does_not_gate_fvg_after_smt_on_confirmation(self):
        from pathlib import Path
        src = Path('smt_scanner_v8_8.py').read_text()
        self.assertIn('confirmation_bar_is_contiguous', src)
        self.assertIn('SMT_IN_FVG_CONFIRM_MINS', src)
        # Gate failure must not `continue` the whole candidate before find_fvg.
        gate_block = src[src.find('conf_ok = confirmation_bar_is_contiguous'):]
        gate_block = gate_block[:gate_block.find("entry_type = 'FVG_AFTER_SMT'")]
        self.assertNotIn('continue', gate_block.split('stale_filtered')[0])
        self.assertIn('find_fvg', src[src.find("entry_type = 'FVG_AFTER_SMT'"):src.find("entry_type = 'FVG_AFTER_SMT'")+800])


if __name__ == '__main__':
    unittest.main()
