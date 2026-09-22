"""Stats helpers for the automated P&L report. No live API."""

from __future__ import annotations

import unittest

import pandas as pd

from backtest.pnl_report import compute_stats, funnel, wilson_ci


class TestPnlStats(unittest.TestCase):
    def test_wilson_contains_sample_proportion(self):
        lo, hi = wilson_ci(40, 100)
        self.assertLess(lo, 0.4)
        self.assertGreater(hi, 0.4)

    def test_funnel_splits_no_fill_reasons(self):
        df = pd.DataFrame(
            {
                "outcome": [
                    "win",
                    "loss",
                    "no_fill_timeout",
                    "no_fill_50pct",
                    "no_fill_never_traded",
                    "no_fill_by_eod",
                ]
            }
        )
        fun = funnel(df)
        self.assertEqual(list(fun["outcome"]), [
            "win",
            "loss",
            "no_impulse_exit",
            "no_fill_timeout",
            "no_fill_50pct",
            "no_fill_never_traded",
            "no_fill_by_eod",
        ])
        counts = dict(zip(fun["outcome"], fun["count"]))
        self.assertEqual(counts["no_impulse_exit"], 0)
        self.assertTrue(all(counts[o] == 1 for o in [
            "win", "loss", "no_fill_timeout", "no_fill_50pct",
            "no_fill_never_traded", "no_fill_by_eod",
        ]))

    def test_no_impulse_exit_keeps_actual_R(self):
        df = pd.DataFrame(
            {
                "outcome": ["win", "loss", "no_impulse_exit", "no_fill_by_eod"],
                "realized_R": [1.5, -1.0, 0.4, 0.8],
            }
        )
        a = compute_stats(df, treat_eod_as_zero=False)
        b = compute_stats(df, treat_eod_as_zero=True)
        self.assertEqual(a["n"], 3)
        self.assertAlmostEqual(a["expectancy"], (1.5 - 1.0 + 0.4) / 3)
        self.assertEqual(b["n"], 4)
        self.assertAlmostEqual(b["expectancy"], (1.5 - 1.0 + 0.4 + 0.0) / 4)

    def test_eod_as_zero_lowers_expectancy_vs_exclude(self):
        df = pd.DataFrame(
            {
                "outcome": ["win", "loss", "no_fill_by_eod", "no_fill_timeout"],
                "realized_R": [1.5, -1.0, 0.8, float("nan")],
            }
        )
        a = compute_stats(df, treat_eod_as_zero=False)
        b = compute_stats(df, treat_eod_as_zero=True)
        self.assertEqual(a["n"], 2)
        self.assertEqual(b["n"], 3)
        self.assertGreater(a["expectancy"], b["expectancy"])
        self.assertEqual(b["eod_as_zero"], 1)


if __name__ == "__main__":
    unittest.main()
