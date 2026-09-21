"""Fill-simulator tests on synthetic 1-min bars. No live API."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.simulate_fills import simulate_fills, simulate_one

ET = ZoneInfo("America/New_York")


def _bars(rows):
    """rows: (et_naive_or_aware, o, h, l, c). Naive times are ET."""
    recs = []
    for t, o, h, l, c in rows:
        ts = pd.Timestamp(t)
        if ts.tzinfo is None:
            ts = ts.tz_localize(ET)
        recs.append({"time": ts.tz_convert("UTC"), "open": o, "high": h, "low": l, "close": c})
    return pd.DataFrame(recs)


def _sig(**kwargs):
    base = {
        "smt_time": datetime(2026, 9, 17, 9, 0, tzinfo=ET),
        "session": "NY Morning",
        "direction": "LONG",
        "instrument": "ES",
        "entry_type": "SMT_IN_FVG",
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 103.0,
        "target_R": 99.0,  # must be ignored
    }
    base.update(kwargs)
    return pd.Series(base)


class TestSimulateFills(unittest.TestCase):
    def test_market_tp_first_is_win_and_ignores_target_R(self):
        bars = _bars(
            [
                ("2026-09-17 09:00", 100.0, 100.2, 99.8, 100.0),
                ("2026-09-17 09:01", 100.1, 103.5, 100.0, 103.2),
            ]
        )
        out = simulate_one(_sig(), bars)
        self.assertEqual(out["outcome"], "win")
        self.assertEqual(out["exit_price"], 103.0)
        self.assertAlmostEqual(out["realized_R"], 3.0)

    def test_market_sl_first_is_loss(self):
        bars = _bars(
            [
                ("2026-09-17 09:00", 100.0, 100.2, 99.8, 100.0),
                ("2026-09-17 09:01", 100.0, 100.2, 98.5, 99.0),
            ]
        )
        out = simulate_one(_sig(), bars)
        self.assertEqual(out["outcome"], "loss")
        self.assertEqual(out["exit_price"], 99.0)
        self.assertAlmostEqual(out["realized_R"], -1.0)

    def test_same_bar_both_levels_is_stop_first(self):
        bars = _bars(
            [
                ("2026-09-17 09:00", 100.0, 100.1, 99.9, 100.0),
                ("2026-09-17 09:01", 100.0, 104.0, 98.0, 102.0),
            ]
        )
        out = simulate_one(_sig(), bars)
        self.assertEqual(out["outcome"], "loss")
        self.assertAlmostEqual(out["realized_R"], -1.0)

    def test_limit_never_traded_is_no_fill(self):
        bars = _bars(
            [
                ("2026-09-17 09:05", 101.0, 102.0, 100.6, 101.5),
                ("2026-09-17 09:06", 101.5, 103.5, 101.0, 103.0),
            ]
        )
        sig = _sig(
            entry_type="FVG_AFTER_SMT",
            entry_price=100.0,
            fvg_bar=datetime(2026, 9, 17, 9, 5, tzinfo=ET),
        )
        out = simulate_one(sig, bars)
        self.assertEqual(out["outcome"], "no_fill")
        self.assertTrue(pd.isna(out["exit_price"]))

    def test_limit_fill_then_tp(self):
        bars = _bars(
            [
                ("2026-09-17 09:05", 100.5, 100.8, 100.2, 100.4),
                ("2026-09-17 09:06", 100.3, 100.4, 99.9, 100.0),  # trades to 100 limit
                ("2026-09-17 09:07", 100.1, 103.2, 100.0, 103.0),
            ]
        )
        sig = _sig(
            entry_type="FVG_AFTER_SMT",
            entry_price=100.0,
            fvg_bar=datetime(2026, 9, 17, 9, 5, tzinfo=ET),
        )
        out = simulate_one(sig, bars)
        self.assertEqual(out["outcome"], "win")
        self.assertAlmostEqual(out["realized_R"], 3.0)

    def test_filled_no_sl_tp_by_session_end(self):
        bars = _bars(
            [
                ("2026-09-17 10:28", 100.0, 100.2, 99.9, 100.1),
                ("2026-09-17 10:29", 100.1, 100.4, 100.0, 100.3),
            ]
        )
        out = simulate_one(_sig(smt_time=datetime(2026, 9, 17, 10, 28, tzinfo=ET)), bars)
        self.assertEqual(out["outcome"], "no_fill_by_eod")
        self.assertEqual(out["exit_price"], 100.3)
        self.assertAlmostEqual(out["realized_R"], 0.3)

    def test_short_limit_fill_uses_high(self):
        bars = _bars(
            [
                ("2026-09-17 13:05", 99.5, 99.8, 99.2, 99.4),
                ("2026-09-17 13:06", 99.6, 100.1, 99.5, 100.0),  # high reaches 100
                ("2026-09-17 13:07", 99.9, 100.0, 97.0, 97.2),
            ]
        )
        sig = _sig(
            session="NY Afternoon",
            smt_time=datetime(2026, 9, 17, 13, 0, tzinfo=ET),
            direction="SHORT",
            entry_type="FVG_AFTER_SMT",
            entry_price=100.0,
            stop_loss=101.0,
            take_profit=97.0,
            fvg_bar=datetime(2026, 9, 17, 13, 5, tzinfo=ET),
        )
        out = simulate_one(sig, bars)
        self.assertEqual(out["outcome"], "win")
        self.assertAlmostEqual(out["realized_R"], 3.0)

    def test_simulate_fills_adds_columns_and_picks_es_vs_nq(self):
        es = _bars(
            [
                ("2026-09-17 09:00", 100.0, 100.1, 99.9, 100.0),
                ("2026-09-17 09:01", 100.0, 100.2, 98.5, 99.0),
            ]
        )
        nq = _bars(
            [
                ("2026-09-17 09:00", 20000.0, 20010.0, 19990.0, 20000.0),
                ("2026-09-17 09:01", 20000.0, 20080.0, 19990.0, 20050.0),
            ]
        )
        sigs = pd.DataFrame(
            [
                _sig(instrument="ES"),
                _sig(
                    instrument="NQ",
                    entry_price=20000.0,
                    stop_loss=19960.0,
                    take_profit=20080.0,
                ),
            ]
        )
        out = simulate_fills(sigs, es_bars=es, nq_bars=nq)
        self.assertEqual(list(out["outcome"]), ["loss", "win"])
        self.assertAlmostEqual(out.loc[0, "realized_R"], -1.0)
        self.assertAlmostEqual(out.loc[1, "realized_R"], 2.0)

    def test_smt_in_fvg_ignores_stop_on_confirmation_bar(self):
        """Fill is the confirmation close. That bar's low already happened."""
        bars = _bars(
            [
                # confirmation bar: low prints through the stop, then closes at entry
                ("2026-09-17 09:00", 100.2, 100.4, 98.0, 100.0),
                ("2026-09-17 09:01", 100.0, 103.5, 99.8, 103.2),
            ]
        )
        out = simulate_one(_sig(), bars)
        self.assertEqual(out["outcome"], "win")
        self.assertEqual(out["path"][0]["action"], "market_fill_at_close")
        self.assertEqual(out["path"][-1]["action"], "target")

    def test_path_starts_at_fill_bar(self):
        bars = _bars(
            [
                ("2026-09-17 09:00", 100.0, 100.2, 99.8, 100.0),
                ("2026-09-17 09:01", 100.0, 100.2, 98.5, 99.0),
            ]
        )
        out = simulate_one(_sig(), bars)
        self.assertEqual(out["outcome"], "loss")
        self.assertEqual([s["action"] for s in out["path"]], ["market_fill_at_close", "stop"])


if __name__ == "__main__":
    unittest.main()
