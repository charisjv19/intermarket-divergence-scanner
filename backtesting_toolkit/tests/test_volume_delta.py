"""Unit tests for 1-minute volume delta built from 1-second bars."""

import unittest

import pandas as pd

from backtesting_toolkit import volume_delta as vd


def secs(rows, start="2026-11-17T14:30:00+00:00"):
    t0 = pd.Timestamp(start)
    return pd.DataFrame([
        {"time": t0 + pd.Timedelta(seconds=s), "open": o, "high": max(o, c), "low": min(o, c), "close": c, "Volume": v}
        for s, o, c, v in rows
    ])


class TestPolarity(unittest.TestCase):
    def test_up_down_flat_rules(self):
        df = secs([(0, 10, 11, 5),     # up -> buy
                   (1, 11, 10, 3),     # down -> sell
                   (2, 10, 10, 2),     # flat, close == prev close -> carry sell
                   (3, 10.25, 10.25, 4),  # flat, close > prev close -> buy
                   (4, 10.25, 10.25, 1)])  # flat, unchanged -> carry buy
        self.assertEqual(vd.second_polarity(df).tolist(), [1, -1, -1, 1, 1])


class TestMinuteDelta(unittest.TestCase):
    def test_columns_and_values(self):
        df = secs([(0, 10, 11, 5), (1, 11, 10, 3), (2, 10, 10, 2), (59, 10, 12, 4),
                   (60, 12, 11, 7)])  # next minute
        m = vd.minute_delta(df)
        self.assertEqual(list(m.columns), ["time", "open", "high", "low", "close", "Volume"] + vd.DELTA_COLUMNS)
        first = m.iloc[0]
        self.assertEqual((first["open"], first["high"], first["low"], first["close"]), (10, 12, 10, 12))
        self.assertEqual(first["Volume"], 14)
        self.assertEqual((first["Buy Vol"], first["Sell Vol"], first["Delta"]), (9, 5, 4))
        # running delta: +5, +2, 0, +4  -> high 5, low clipped to 0
        self.assertEqual((first["Delta High"], first["Delta Low"], first["Active Secs"]), (5, 0, 4))
        second = m.iloc[1]
        self.assertEqual((second["Delta"], second["Delta High"], second["Delta Low"]), (-7, 0, -7))

    def test_buy_plus_sell_equals_volume(self):
        df = secs([(k, 10 + (k % 3) * 0.25, 10 + ((k + 1) % 3) * 0.25, k + 1) for k in range(180)])
        m = vd.minute_delta(df)
        self.assertTrue((m["Buy Vol"] + m["Sell Vol"] == m["Volume"]).all())
        self.assertTrue((m["Delta High"] >= m["Delta"].clip(lower=0)).all())
        self.assertTrue((m["Delta Low"] <= m["Delta"].clip(upper=0)).all())


if __name__ == "__main__":
    unittest.main()
