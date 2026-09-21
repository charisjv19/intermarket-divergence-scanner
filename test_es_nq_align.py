"""ES/NQ 1m alignment: strict inner join, never fill a missing minute."""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from smt_scanner_v8_8 import (
    exclusive_bar_times,
    merge_es_nq_1m,
    resample_to_15m_and_5m,
)


def _bars(times, high=None):
    t = pd.to_datetime(times, utc=True)
    n = len(t)
    h = list(high) if high is not None else [100.0 + i for i in range(n)]
    return pd.DataFrame({
        'time': t,
        'open': h,
        'high': h,
        'low': [x - 1 for x in h],
        'close': h,
        'Swing High': [0] * n,
        'Swing Low': [0] * n,
    })


class ExclusiveBarTimesTests(unittest.TestCase):
    def test_reports_one_sided_minutes(self):
        es = pd.to_datetime(['2026-04-29 13:30:00Z', '2026-04-29 13:31:00Z'], utc=True)
        nq = pd.to_datetime(['2026-04-29 13:30:00Z'], utc=True)
        only_es, only_nq = exclusive_bar_times(es, nq)
        self.assertEqual(list(only_es), [pd.Timestamp('2026-04-29 13:31:00Z')])
        self.assertEqual(list(only_nq), [])

    def test_aligned_is_empty(self):
        t = pd.to_datetime(['2026-04-29 13:30:00Z'], utc=True)
        only_es, only_nq = exclusive_bar_times(t, t)
        self.assertEqual(len(only_es), 0)
        self.assertEqual(len(only_nq), 0)


class InnerJoinTests(unittest.TestCase):
    def test_drops_exclusive_minutes_and_leaves_no_nan(self):
        es = _bars(['2026-04-29 13:30:00Z', '2026-04-29 13:31:00Z', '2026-04-29 13:32:00Z'])
        nq = _bars(['2026-04-29 13:30:00Z', '2026-04-29 13:32:00Z', '2026-04-29 13:33:00Z'])
        merged = merge_es_nq_1m(es, nq)
        times = list(merged['time'])
        self.assertEqual(
            times,
            list(pd.to_datetime(['2026-04-29 13:30:00Z', '2026-04-29 13:32:00Z'], utc=True)),
        )
        only_es, only_nq = exclusive_bar_times(merged['time'], merged['time'])
        self.assertEqual(len(only_es), 0)
        self.assertEqual(len(only_nq), 0)
        self.assertFalse(merged['close_es'].isna().any())
        self.assertFalse(merged['close_nq'].isna().any())

    def test_source_uses_explicit_inner_join(self):
        src = Path('smt_scanner_v8_8.py').read_text()
        self.assertIn("how='inner'", src)
        self.assertIn('es.index.intersection(nq.index)', src)
        self.assertNotIn("how='outer'", src)
        self.assertNotIn("how='left'", src)
        self.assertNotIn('.ffill()', src)
        self.assertNotIn('.fillna(method', src)


class ResampleIntersectionTests(unittest.TestCase):
    def test_exclusive_es_high_does_not_enter_15m_bar(self):
        shared = pd.date_range('2026-04-29 13:00:00Z', periods=10, freq='1min')
        es = _bars(list(shared) + [pd.Timestamp('2026-04-29 13:10:00Z')],
                   high=[100.0] * 10 + [999.0])
        nq = _bars(list(shared), high=[100.0] * 10)
        with tempfile.TemporaryDirectory() as d:
            es_p = Path(d) / 'es.csv'
            nq_p = Path(d) / 'nq.csv'
            es.to_csv(es_p, index=False)
            nq.to_csv(nq_p, index=False)
            es15, nq15, es5, nq5 = resample_to_15m_and_5m(str(es_p), str(nq_p))
        bucket = es15[es15['time'] == pd.Timestamp('2026-04-29 13:00:00Z')]
        self.assertEqual(len(bucket), 1)
        self.assertEqual(float(bucket.iloc[0]['high']), 100.0)
        self.assertNotEqual(float(bucket.iloc[0]['high']), 999.0)
        only_es, only_nq = exclusive_bar_times(es5['time'], nq5['time'])
        self.assertEqual(len(only_es), 0)
        self.assertEqual(len(only_nq), 0)


if __name__ == '__main__':
    unittest.main()
