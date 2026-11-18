"""v8.7 reconstruction formula tests. No live API, no full scanner."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.reconstruct_v87 import (
    RECONSTRUCTION_LABEL,
    STOP_POINTS,
    STOP_RULE_FLAG,
    reconstruct_one,
    reconstruct_signals,
    simulate_reconstructed_v87,
)
from backtest.simulate_fills import simulate_fills

ET = ZoneInfo("America/New_York")


def _row(**kwargs):
    base = {
        "date": "2026-02-02",
        "session": "NY Morning",
        "sw2_conf_time": datetime(2026, 2, 2, 8, 20, tzinfo=ET),
        "smt_time": datetime(2026, 2, 2, 8, 20, tzinfo=ET),
        "fvg_bar": datetime(2026, 2, 2, 8, 22, tzinfo=ET),
        "direction": "LONG",
        "instrument": "ES",
        "entry_type": "FVG_AFTER_SMT",
        "entry_50": 100.0,
        "es_sw2_price": 98.0,
        "nq_sw2_price": 25000.0,
    }
    base.update(kwargs)
    return pd.Series(base)


class TestReconstructFormula(unittest.TestCase):
    def test_long_es_3r_fvg_after_smt(self):
        # entry 100, swept 98, stop 93, R=7, TP=121
        out = reconstruct_one(_row())
        self.assertEqual(out["entry_price"], 100.0)
        self.assertEqual(out["stop_loss"], 93.0)
        self.assertEqual(out["take_profit"], 121.0)
        self.assertEqual(out["target_R"], 3.0)
        self.assertEqual(out["stop_offset_pts"], 5.0)
        self.assertFalse(out["risk_inverted"])

    def test_smt_in_fvg_uses_limit_50_and_1_5r(self):
        out = reconstruct_one(_row(entry_type="SMT_IN_FVG"))
        self.assertEqual(out["entry_price"], 100.0)  # still entry_50, not a market close
        self.assertEqual(out["target_R"], 1.5)
        self.assertEqual(out["take_profit"], 110.5)  # 100 + 1.5*7

    def test_nq_stop_is_5pt_not_v88_10pt(self):
        out = reconstruct_one(
            _row(instrument="NQ", direction="SHORT", entry_50=25010.0, nq_sw2_price=25000.0)
        )
        self.assertEqual(STOP_POINTS, 5.0)
        self.assertEqual(out["stop_loss"], 25005.0)  # swept + 5, NOT +10
        self.assertEqual(out["take_profit"], 24995.0)  # 3R of 5pt risk: 25010 - 15

    def test_short_stop_above_swept(self):
        out = reconstruct_one(_row(direction="SHORT", entry_50=90.0, es_sw2_price=100.0))
        self.assertEqual(out["stop_loss"], 105.0)
        self.assertEqual(out["take_profit"], 45.0)  # 90 - 3*15

    def test_inverted_risk_flagged(self):
        # LONG entry below stop (entry_50 deep through the swept extreme)
        out = reconstruct_one(_row(entry_50=90.0, es_sw2_price=100.0))
        self.assertTrue(out["risk_inverted"])
        self.assertEqual(out["stop_loss"], 95.0)

    def test_swept_prefers_explicit_column(self):
        out = reconstruct_one(_row(swept_extreme=97.25, es_sw2_price=98.0))
        self.assertEqual(out["swept_extreme"], 97.25)
        self.assertEqual(out["stop_loss"], 92.25)

    def test_missing_entry_50_raises(self):
        row = _row()
        row["entry_50"] = pd.NA
        with self.assertRaises(ValueError):
            reconstruct_one(row)

    def test_stop_rule_flag_mentions_nq_split(self):
        self.assertIn("5.0", STOP_RULE_FLAG)
        self.assertIn("10.0", STOP_RULE_FLAG)
        self.assertIn(RECONSTRUCTION_LABEL[:20], RECONSTRUCTION_LABEL)


class TestReconstructSimIsLimit(unittest.TestCase):
    def test_smt_in_fvg_fills_as_limit_not_market(self):
        # Market path would fill at 08:20 close (never waits). Limit waits for low<=entry.
        sigs = pd.DataFrame([
            _row(
                entry_type="SMT_IN_FVG",
                entry_50=100.0,
                es_sw2_price=98.0,
            )
        ])
        bars = pd.DataFrame(
            [
                {"time": pd.Timestamp("2026-02-02 08:20", tz=ET).tz_convert("UTC"),
                 "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.0},
                {"time": pd.Timestamp("2026-02-02 08:21", tz=ET).tz_convert("UTC"),
                 "open": 102.0, "high": 102.5, "low": 101.0, "close": 101.5},
                {"time": pd.Timestamp("2026-02-02 08:22", tz=ET).tz_convert("UTC"),
                 "open": 101.5, "high": 101.8, "low": 99.5, "close": 100.5},
                {"time": pd.Timestamp("2026-02-02 08:23", tz=ET).tz_convert("UTC"),
                 "open": 100.5, "high": 111.0, "low": 100.0, "close": 110.5},
            ]
        )
        filled = simulate_reconstructed_v87(sigs, es_bars=bars)
        self.assertEqual(filled.iloc[0]["entry_type"], "SMT_IN_FVG")  # restored for reporting
        self.assertEqual(filled.iloc[0]["outcome"], "win")
        self.assertEqual(float(filled.iloc[0]["entry_price"]), 100.0)
        # If it had market-filled at 08:20 close, SL/TP would start 08:21 (low 101, never 100).
        # Limit fill is on 08:22 (low 99.5), then 08:23 high 111 hits 1.5R TP 110.5.

    def test_naive_smt_in_fvg_market_path_would_differ(self):
        """Guard: simulate_fills(SMT_IN_FVG) is market; reconstruction must not use that."""
        recon = reconstruct_signals(pd.DataFrame([_row(entry_type="SMT_IN_FVG")]))
        bars = pd.DataFrame(
            [
                # Market path fills at 08:20 close; next bar 08:21 high hits 1.5R TP.
                {"time": pd.Timestamp("2026-02-02 08:20", tz=ET).tz_convert("UTC"),
                 "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.0},
                {"time": pd.Timestamp("2026-02-02 08:21", tz=ET).tz_convert("UTC"),
                 "open": 102.0, "high": 111.0, "low": 101.0, "close": 110.0},
                # Limit path starts at fvg_bar 08:22 and never trades 100 (or the 50% level 105.25).
                {"time": pd.Timestamp("2026-02-02 08:22", tz=ET).tz_convert("UTC"),
                 "open": 104.0, "high": 104.5, "low": 103.5, "close": 104.0},
                {"time": pd.Timestamp("2026-02-02 08:23", tz=ET).tz_convert("UTC"),
                 "open": 104.0, "high": 104.5, "low": 103.5, "close": 104.0},
            ]
        )
        market = simulate_fills(recon, es_bars=bars)
        limit = simulate_reconstructed_v87(recon, es_bars=bars)
        self.assertEqual(market.iloc[0]["outcome"], "win")
        self.assertIn(limit.iloc[0]["outcome"], {"no_fill_timeout", "no_fill_never_traded"})
        self.assertNotEqual(market.iloc[0]["outcome"], limit.iloc[0]["outcome"])


if __name__ == "__main__":
    unittest.main()
