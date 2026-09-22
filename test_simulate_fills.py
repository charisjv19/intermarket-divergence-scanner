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
                # highs stay below 50% of entry→TP (101.5) so this is never-traded, not 50%
                ("2026-09-17 09:05", 101.0, 101.2, 100.6, 101.1),
                ("2026-09-17 09:06", 101.1, 101.4, 100.7, 101.2),
            ]
        )
        sig = _sig(
            entry_type="FVG_AFTER_SMT",
            entry_price=100.0,
            fvg_bar=datetime(2026, 9, 17, 9, 5, tzinfo=ET),
        )
        out = simulate_one(sig, bars)
        self.assertEqual(out["outcome"], "no_fill_never_traded")
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


class TestLimitCancelsAndMorningExtend(unittest.TestCase):
    def test_limit_times_out_at_exactly_20_minutes(self):
        """fvg_bar 09:05 → deadline 09:25. The 09:25 bar would fill; still timeout."""
        rows = []
        for m in range(5, 25):
            # 09:05..09:24: never trades the 100 limit, never hits 101.5 50% level
            rows.append((f"2026-09-17 09:{m:02d}", 100.8, 101.2, 100.6, 100.7))
        rows.append(("2026-09-17 09:25", 100.5, 100.6, 99.9, 100.0))  # would fill
        sig = _sig(
            entry_type="FVG_AFTER_SMT",
            entry_price=100.0,
            take_profit=103.0,
            fvg_bar=datetime(2026, 9, 17, 9, 5, tzinfo=ET),
        )
        out = simulate_one(sig, _bars(rows))
        self.assertEqual(out["outcome"], "no_fill_timeout")
        self.assertTrue(pd.isna(out["exit_price"]))
        self.assertEqual(out["path"][-1]["action"], "cancel_timeout")

    def test_limit_fill_at_19_minutes_is_still_a_fill(self):
        rows = []
        for m in range(5, 24):
            rows.append((f"2026-09-17 09:{m:02d}", 100.8, 101.2, 100.6, 100.7))
        rows.append(("2026-09-17 09:24", 100.4, 100.5, 99.9, 100.0))  # +19 min
        rows.append(("2026-09-17 09:25", 100.1, 103.2, 100.0, 103.0))
        sig = _sig(
            entry_type="FVG_AFTER_SMT",
            entry_price=100.0,
            fvg_bar=datetime(2026, 9, 17, 9, 5, tzinfo=ET),
        )
        out = simulate_one(sig, _bars(rows))
        self.assertEqual(out["outcome"], "win")

    def test_limit_cancelled_by_50pct_to_target(self):
        # entry 100, TP 103 → 50% level 101.5. High prints it; low never reaches 100.
        bars = _bars(
            [
                ("2026-09-17 09:05", 100.8, 101.2, 100.6, 100.7),
                ("2026-09-17 09:06", 100.7, 101.6, 100.5, 101.4),  # high 101.6
                ("2026-09-17 09:07", 100.4, 100.5, 99.9, 100.0),  # would have filled
            ]
        )
        sig = _sig(
            entry_type="FVG_AFTER_SMT",
            entry_price=100.0,
            take_profit=103.0,
            fvg_bar=datetime(2026, 9, 17, 9, 5, tzinfo=ET),
        )
        out = simulate_one(sig, bars)
        self.assertEqual(out["outcome"], "no_fill_50pct")
        self.assertTrue(pd.isna(out["exit_price"]))
        self.assertEqual(out["path"][-1]["action"], "cancel_50pct")

    def test_short_limit_cancelled_by_50pct_uses_low(self):
        # entry 100, TP 97 → 50% level 98.5. Low prints it; high never reaches 100.
        bars = _bars(
            [
                ("2026-09-17 13:05", 99.4, 99.8, 99.0, 99.2),
                ("2026-09-17 13:06", 99.2, 99.3, 98.4, 98.6),
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
        self.assertEqual(out["outcome"], "no_fill_50pct")

    def test_morning_entry_after_0945_flattens_at_1100(self):
        """09:46 fill; TP at 10:45 is after 10:30 — only reachable with 11:00 flatten."""
        rows = [("2026-09-17 09:46", 100.0, 100.2, 99.8, 100.0)]
        for m in range(47, 60):
            rows.append((f"2026-09-17 09:{m:02d}", 100.0, 100.2, 99.8, 100.0))
        for m in range(0, 45):
            rows.append((f"2026-09-17 10:{m:02d}", 100.0, 100.2, 99.8, 100.0))
        rows.append(("2026-09-17 10:45", 100.1, 103.5, 100.0, 103.2))
        out = simulate_one(_sig(smt_time=datetime(2026, 9, 17, 9, 46, tzinfo=ET)), _bars(rows), impulse_confirm_bars=0)
        self.assertEqual(out["outcome"], "win")
        self.assertEqual(out["exit_price"], 103.0)

    def test_morning_entry_at_0945_still_uses_1030(self):
        """09:45 is not after 09:45; 10:45 TP is past 10:30 so this is EOD, not a win."""
        rows = [("2026-09-17 09:45", 100.0, 100.2, 99.8, 100.0)]
        for m in range(46, 60):
            rows.append((f"2026-09-17 09:{m:02d}", 100.0, 100.2, 99.8, 100.0))
        for m in range(0, 30):
            rows.append((f"2026-09-17 10:{m:02d}", 100.0, 100.2, 99.8, 100.1))
        rows.append(("2026-09-17 10:45", 100.1, 103.5, 100.0, 103.2))
        out = simulate_one(_sig(smt_time=datetime(2026, 9, 17, 9, 45, tzinfo=ET)), _bars(rows), impulse_confirm_bars=0)
        self.assertEqual(out["outcome"], "no_fill_by_eod")
        self.assertLess(_to_et_min(out["exit_time"]), "10:30")

    def test_afternoon_is_unaffected_by_morning_extension(self):
        """14:50 fill; a 15:10 TP must not count. Flatten stays 15:00."""
        rows = [("2026-09-17 14:50", 100.0, 100.2, 99.8, 100.0)]
        for m in range(51, 60):
            rows.append((f"2026-09-17 14:{m:02d}", 100.0, 100.2, 99.8, 100.1))
        rows.append(("2026-09-17 15:00", 100.1, 100.2, 99.8, 100.1))
        rows.append(("2026-09-17 15:10", 100.1, 103.5, 100.0, 103.2))
        out = simulate_one(
            _sig(
                session="NY Afternoon",
                smt_time=datetime(2026, 9, 17, 14, 50, tzinfo=ET),
            ),
            _bars(rows),
            impulse_confirm_bars=0,
        )
        self.assertEqual(out["outcome"], "no_fill_by_eod")
        self.assertEqual(pd.Timestamp(out["exit_time"]).tz_convert(ET).strftime("%H:%M"), "14:59")


def _to_et_min(ts):
    return pd.Timestamp(ts).tz_convert(ET).strftime("%H:%M")


class TestImpulseConfirm(unittest.TestCase):
    """SMT_IN_FVG 7-bar impulse FVG. Entry is unchanged; management only."""

    def test_fvg_within_7_bars_continues_to_tp(self):
        """LONG gap completes at bar 3 (entry, +1, +2); TP on bar 4."""
        bars = _bars(
            [
                ("2026-09-17 09:00", 100.0, 100.2, 99.8, 100.0),  # entry; ph=100.2
                ("2026-09-17 09:01", 100.1, 100.5, 100.0, 100.2),
                ("2026-09-17 09:02", 100.8, 101.0, 100.8, 100.9),  # nl=100.8 > 100.2, size=0.6
                ("2026-09-17 09:03", 101.0, 103.5, 100.9, 103.2),
            ]
        )
        out = simulate_one(_sig(), bars)
        self.assertEqual(out["outcome"], "win")
        self.assertEqual(out["exit_price"], 103.0)
        self.assertAlmostEqual(out["realized_R"], 3.0)
        self.assertTrue(out["impulse_fvg_found"])
        self.assertEqual([s["action"] for s in out["path"]], [
            "market_fill_at_close", "open", "impulse_fvg", "target",
        ])

    def test_no_fvg_within_7_bars_exits_at_close(self):
        """Bar 7 prints through TP and SL; still flatten at that close, not ±R."""
        rows = [("2026-09-17 09:00", 100.0, 100.3, 99.8, 100.0)]
        for m in range(1, 6):
            rows.append((f"2026-09-17 09:{m:02d}", 100.0, 100.3, 99.8, 100.1))
        # 7th bar (09:06): high through TP, low through SL, close 100.4 → +0.4R
        rows.append(("2026-09-17 09:06", 100.2, 103.5, 98.5, 100.4))
        rows.append(("2026-09-17 09:07", 100.4, 103.5, 100.0, 103.2))  # must not be used
        out = simulate_one(_sig(), _bars(rows))
        self.assertEqual(out["outcome"], "no_impulse_exit")
        self.assertEqual(out["exit_price"], 100.4)
        self.assertAlmostEqual(out["realized_R"], 0.4)
        self.assertFalse(out["impulse_fvg_found"])
        self.assertEqual(out["path"][-1]["action"], "no_impulse_exit")
        self.assertEqual(pd.Timestamp(out["exit_time"]).tz_convert(ET).strftime("%H:%M"), "09:06")

    def test_fvg_exactly_at_bar_7_continues_to_tp(self):
        """Window (bar 5, 6, 7) is the last eligible FVG; trade then hits TP on bar 8."""
        rows = [("2026-09-17 09:00", 100.0, 100.3, 99.8, 100.0)]
        for m in range(1, 4):
            rows.append((f"2026-09-17 09:{m:02d}", 100.0, 100.3, 99.8, 100.1))
        rows.append(("2026-09-17 09:04", 100.1, 100.2, 99.9, 100.1))  # bar 5; ph=100.2
        rows.append(("2026-09-17 09:05", 100.2, 100.6, 100.0, 100.3))  # bar 6
        rows.append(("2026-09-17 09:06", 100.8, 101.0, 100.8, 100.9))  # bar 7; nl=100.8, size=0.6
        rows.append(("2026-09-17 09:07", 101.0, 103.5, 100.9, 103.2))
        out = simulate_one(_sig(), _bars(rows))
        self.assertEqual(out["outcome"], "win")
        self.assertEqual(out["exit_price"], 103.0)
        self.assertAlmostEqual(out["realized_R"], 3.0)
        self.assertTrue(out["impulse_fvg_found"])
        self.assertEqual(out["path"][-2]["action"], "impulse_fvg")
        self.assertEqual(pd.Timestamp(out["impulse_fvg_time"]).tz_convert(ET).strftime("%H:%M"), "09:06")


if __name__ == "__main__":
    unittest.main()
