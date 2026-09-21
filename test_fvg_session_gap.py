"""Regression: a 3-bar FVG must not span an sdf session gap.

Piece 3 should call check_fvg_windows_contiguous(signals, sdf) whenever
both the signal CSV and the session-filtered bars are available.
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from smt_scanner_v8_8 import (
    check_fvg_windows_contiguous,
    find_fvg,
    fvg_window_is_contiguous,
)

ET = ZoneInfo('America/New_York')


def _et(y, m, d, hh, mm):
    return pd.Timestamp(datetime(y, m, d, hh, mm, tzinfo=ET))


def _gap_sdf():
    """14:27-14:29 afternoon, then 07:30 next morning — the real sdf jump."""
    times = [
        _et(2026, 2, 2, 14, 27),
        _et(2026, 2, 2, 14, 28),
        _et(2026, 2, 2, 14, 29),
        _et(2026, 2, 3, 7, 30),
        _et(2026, 2, 3, 7, 31),
    ]
    # LONG FVG across the gap: high[j-1]=14:28 < low[j+1]=07:30
    # Also a contiguous LONG FVG at j=14:28: high[14:27] < low[14:29]
    return pd.DataFrame({
        'et': times,
        'high_es': [7060.0, 7066.75, 7068.0, 7071.0, 7072.0],
        'low_es':  [7058.0, 7065.0,  7064.0, 7070.0, 7069.0],
    })


class FvgWindowContiguousTests(unittest.TestCase):
    def test_contiguous_triplet_is_two_one_minute_steps(self):
        sdf = _gap_sdf()
        self.assertTrue(fvg_window_is_contiguous(sdf, 1))   # 14:27/28/29
        self.assertFalse(fvg_window_is_contiguous(sdf, 2))  # 14:28/29 / 07:30
        self.assertFalse(fvg_window_is_contiguous(sdf, 3))  # 14:29 / 07:30/31
        span = (sdf.iloc[3]['et'] - sdf.iloc[1]['et']).total_seconds() / 60.0
        self.assertEqual(span, 1022.0)  # 14:28 → next 07:30, not 2 minutes


class FindFvgSessionGapTests(unittest.TestCase):
    def test_gap_spanning_triplet_is_rejected(self):
        sdf = _gap_sdf()
        # start at 14:27 so lookahead includes the 14:29/07:30 phantom
        got = find_fvg(sdf, start_idx=0, direction='LONG', instrument='ES',
                       lookahead=15, swept_extreme=7000.0)
        self.assertTrue(got['fvg_found'])
        # earliest valid is the contiguous 14:27/28/29 window, not 14:29/07:30
        self.assertEqual(pd.Timestamp(got['fvg_bar']), sdf.iloc[1]['et'])
        self.assertEqual(got['fvg_low'], 7060.0)   # high of 14:27
        self.assertEqual(got['fvg_high'], 7064.0)  # low of 14:29

    def test_only_gap_triplet_yields_no_fvg(self):
        sdf = _gap_sdf()
        # start at 14:28: only j=14:29 is in range, and it spans the gap
        got = find_fvg(sdf, start_idx=1, direction='LONG', instrument='ES',
                       lookahead=15, swept_extreme=7000.0)
        self.assertFalse(got['fvg_found'])

    def test_short_phantom_like_mar11_is_rejected(self):
        times = [
            _et(2026, 3, 11, 14, 28),
            _et(2026, 3, 11, 14, 29),
            _et(2026, 3, 12, 7, 30),
        ]
        sdf = pd.DataFrame({
            'et': times,
            'high_es': [6822.5, 6821.0, 6804.5],
            'low_es':  [6818.0, 6817.0, 6798.5],
        })
        got = find_fvg(sdf, start_idx=0, direction='SHORT', instrument='ES',
                       lookahead=15, swept_extreme=6900.0)
        self.assertFalse(got.get('fvg_found'))


class Piece3FvgWindowCheckTests(unittest.TestCase):
    def test_fails_when_signal_fvg_bar_is_the_gap_middle(self):
        sdf = _gap_sdf()
        signals = pd.DataFrame({
            'entry_type': ['FVG_AFTER_SMT'],
            'fvg_bar': [sdf.iloc[2]['et']],  # 14:29, j+1 is 07:30
        })
        fails = check_fvg_windows_contiguous(signals, sdf)
        self.assertEqual(len(fails), 1)
        self.assertIn('1 FVG_AFTER_SMT', fails[0])

    def test_passes_contiguous_fvg_bar(self):
        sdf = _gap_sdf()
        signals = pd.DataFrame({
            'entry_type': ['FVG_AFTER_SMT'],
            'fvg_bar': [sdf.iloc[1]['et']],  # 14:28, neighbors 14:27 and 14:29
        })
        self.assertEqual(check_fvg_windows_contiguous(signals, sdf), [])

    def test_find_fvg_calls_contiguous_check(self):
        from pathlib import Path
        src = Path('smt_scanner_v8_8.py').read_text()
        self.assertIn('if not fvg_window_is_contiguous(sdf, j):', src)
        body = src.split('def find_fvg(')[1].split('def fmt_ts')[0]
        self.assertIn('fvg_window_is_contiguous', body)


if __name__ == '__main__':
    unittest.main()
