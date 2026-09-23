"""v8.9 SMT_IN_FVG membership: confirming-instrument sw1/sw2 wicks in pre-FVG.

v8.8 used the confirmation-bar close. These cases are the TEST 1 rule:
  - swing wick inside, close outside → membership hits (now fires)
  - close inside, neither swing inside → membership misses (excluded)
"""
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from smt_scanner_v8_9 import (
    find_preexisting_fvg,
    fvg_in_session,
    fvg_in_session_or_pre_session,
)

ET = ZoneInfo('America/New_York')


def _et(y, m, d, hh, mm):
    return pd.Timestamp(datetime(y, m, d, hh, mm, tzinfo=ET))


def _session_long_fvg_sdf():
    """08:10/11/12 session LONG FVG: high[08:10]=100 < low[08:12]=100.6.

    Confirmation bar is 08:13 close 101.5 (outside the FVG).
    """
    times = [
        _et(2026, 2, 5, 8, 10),
        _et(2026, 2, 5, 8, 11),
        _et(2026, 2, 5, 8, 12),
        _et(2026, 2, 5, 8, 13),
    ]
    return pd.DataFrame({
        'et': times,
        'high_es': [100.0, 101.0, 102.0, 101.8],
        'low_es':  [99.5,  100.2, 100.6, 101.2],
        'close_es': [99.8, 100.8, 101.4, 101.5],
    })


def _session_short_fvg_sdf():
    """08:10/11/12 session SHORT FVG: low[08:10]=101.5 > high[08:12]=100.5."""
    times = [
        _et(2026, 2, 5, 8, 10),
        _et(2026, 2, 5, 8, 11),
        _et(2026, 2, 5, 8, 12),
        _et(2026, 2, 5, 8, 13),
    ]
    return pd.DataFrame({
        'et': times,
        'high_es': [102.0, 101.4, 100.5, 99.8],
        'low_es':  [101.5, 100.8, 99.9,  99.4],
        'close_es': [101.6, 101.0, 100.0, 99.5],
    })


class SwingWickMembershipTests(unittest.TestCase):
    def test_swing_inside_close_outside_matches(self):
        sdf = _session_long_fvg_sdf()
        close = float(sdf.iloc[3]['close_es'])
        self.assertFalse(100.0 <= close <= 100.6)
        got = find_preexisting_fvg(
            sdf, smt_idx=3, direction='LONG', instrument='ES',
            lookback=30, prices=(100.3, 99.0),
        )
        self.assertIsNotNone(got)
        self.assertTrue(got['pre_fvg_found'])
        self.assertEqual(got['pre_fvg_low'], 100.0)
        self.assertEqual(got['pre_fvg_high'], 100.6)

    def test_close_inside_neither_swing_does_not_match(self):
        sdf = _session_long_fvg_sdf()
        # Confirmation close forced inside the FVG; both swing wicks outside.
        sdf = sdf.copy()
        sdf.loc[3, 'close_es'] = 100.3
        close = float(sdf.iloc[3]['close_es'])
        self.assertTrue(100.0 <= close <= 100.6)
        sw1, sw2 = 99.0, 102.0
        self.assertFalse(100.0 <= sw1 <= 100.6)
        self.assertFalse(100.0 <= sw2 <= 100.6)
        swing_hit = find_preexisting_fvg(
            sdf, smt_idx=3, direction='LONG', instrument='ES',
            lookback=30, prices=(sw1, sw2),
        )
        close_hit = find_preexisting_fvg(
            sdf, smt_idx=3, direction='LONG', instrument='ES',
            lookback=30, prices=(close,),
        )
        self.assertIsNone(swing_hit)
        self.assertIsNotNone(close_hit)

    def test_sw2_only_is_enough(self):
        sdf = _session_long_fvg_sdf()
        got = find_preexisting_fvg(
            sdf, smt_idx=3, direction='LONG', instrument='ES',
            lookback=30, prices=(99.0, 100.3),
        )
        self.assertIsNotNone(got)

    def test_sw1_only_is_enough(self):
        sdf = _session_long_fvg_sdf()
        got = find_preexisting_fvg(
            sdf, smt_idx=3, direction='LONG', instrument='ES',
            lookback=30, prices=(100.3, 102.0),
        )
        self.assertIsNotNone(got)

    def test_short_swing_wick_inside(self):
        sdf = _session_short_fvg_sdf()
        # SHORT FVG [100.5, 101.5]; confirmation close 99.5 is outside.
        close = float(sdf.iloc[3]['close_es'])
        self.assertFalse(100.5 <= close <= 101.5)
        got = find_preexisting_fvg(
            sdf, smt_idx=3, direction='SHORT', instrument='ES',
            lookback=30, prices=(101.0, 102.5),
        )
        self.assertIsNotNone(got)
        self.assertEqual(got['pre_fvg_low'], 100.5)
        self.assertEqual(got['pre_fvg_high'], 101.5)

    def test_pre_session_fvg_still_valid_for_swing_wick(self):
        times = [
            _et(2026, 2, 5, 7, 40),
            _et(2026, 2, 5, 7, 41),
            _et(2026, 2, 5, 7, 42),
            _et(2026, 2, 5, 8, 0),
        ]
        sdf = pd.DataFrame({
            'et': times,
            'high_es': [100.0, 101.0, 102.0, 101.0],
            'low_es':  [99.5,  100.2, 100.6, 100.2],
        })
        self.assertFalse(fvg_in_session(sdf.iloc[1]['et']))
        self.assertTrue(fvg_in_session_or_pre_session(sdf.iloc[1]['et']))
        got = find_preexisting_fvg(
            sdf, smt_idx=3, direction='LONG', instrument='ES',
            lookback=30, prices=(100.3, 99.0),
        )
        self.assertIsNotNone(got)
        self.assertEqual(pd.Timestamp(got['pre_fvg_bar']), sdf.iloc[1]['et'])


class V89RunWiringTests(unittest.TestCase):
    def test_run_passes_confirming_swing_wicks_not_close(self):
        src = Path('smt_scanner_v8_9.py').read_text()
        body = src.split("for c in candidates:")[1]
        self.assertIn("sw1, sw2 = c['es_sw1_price'], c['es_sw2_price']", body)
        self.assertIn("sw1, sw2 = c['nq_sw1_price'], c['nq_sw2_price']", body)
        self.assertIn('PRE_FVG_LOOKBACK, (sw1, sw2)', body)
        self.assertNotIn('PRE_FVG_LOOKBACK, entry_price\n', body)

    def test_close_only_does_not_fall_through_to_fvg_after_smt(self):
        src = Path('smt_scanner_v8_9.py').read_text()
        body = src.split("for c in candidates:")[1].split("if pre_fvg:")[0]
        self.assertIn('close_only_no_swings', body)
        self.assertIn('continue', body)
        self.assertIn('(entry_price,)', body)


if __name__ == '__main__':
    unittest.main()
