"""Discretion-review pack: decision-bar clip, leak scan, scoring. No live API."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.discretion_review.common import (
    clip_15m,
    clip_1m,
    confirmed_swing_mask,
    decision_bar_time,
    leak_hits,
    near_edge_price,
    prepare_tape,
    random_ids,
    scan_path_for_leaks,
    strip_png_text,
    to_et,
)
from backtest.discretion_review.render import render_signal
from backtest.discretion_review.sample import shuffled_order
from backtest.discretion_review.score_review import assigned_r, attach_r, half_split, report

ET = ZoneInfo("America/New_York")


def _tape(start: str, n: int, *, px: float = 100.0, swing_at=None) -> pd.DataFrame:
    start_ts = pd.Timestamp(start, tz=ET)
    rows = []
    for i in range(n):
        t = start_ts + pd.Timedelta(minutes=i)
        o = px + (i % 5) * 0.25
        rows.append(
            {
                "time": t.tz_convert("UTC"),
                "open": o,
                "high": o + 0.75,
                "low": o - 0.5,
                "close": o + 0.25,
                "Swing High": 1 if swing_at is not None and i == swing_at else 0,
                "Swing Low": 0,
            }
        )
    return prepare_tape(pd.DataFrame(rows))


def _sig(**kwargs) -> pd.Series:
    base = {
        "date": "2026-05-01",
        "sw2_conf_time": datetime(2026, 5, 1, 9, 30, tzinfo=ET),
        "smt_time": datetime(2026, 5, 1, 9, 31, tzinfo=ET),
        "session": "NY Morning",
        "direction": "LONG",
        "instrument": "ES",
        "entry_type": "SMT_IN_FVG",
        "stop_loss": 98.0,
        "fvg_bar": pd.NaT,
        "fvg_low": pd.NA,
        "fvg_high": pd.NA,
        "entry_50": pd.NA,
        "pre_fvg_bar": datetime(2026, 5, 1, 9, 20, tzinfo=ET),
        "pre_fvg_low": 99.0,
        "pre_fvg_high": 101.0,
        "es_sw1_time": "2026-05-01 09:10",
        "es_sw1_price": 99.5,
        "es_sw2_time": "2026-05-01 09:30",
        "es_sw2_price": 98.8,
        "nq_sw1_time": "2026-05-01 09:10",
        "nq_sw1_price": 20099.5,
        "nq_sw2_time": "2026-05-01 09:30",
        "nq_sw2_price": 20098.8,
        "combined_15m_bias": "BULLISH",
        "es_15m_bias": "STRONGLY BULLISH",
        "nq_15m_bias": "BULLISH (SLOWING)",
        "id": "ab23cd45",
        "window": "mayaug",
    }
    base.update(kwargs)
    return pd.Series(base)


class TestDecisionBar(unittest.TestCase):
    def test_fvg_after_smt_is_completing_bar(self):
        row = _sig(
            entry_type="FVG_AFTER_SMT",
            fvg_bar=datetime(2026, 5, 1, 9, 33, tzinfo=ET),
            smt_time=datetime(2026, 5, 1, 9, 33, tzinfo=ET),
        )
        self.assertEqual(decision_bar_time(row), pd.Timestamp("2026-05-01 09:34", tz=ET))

    def test_smt_in_fvg_is_confirmation_bar(self):
        row = _sig()
        self.assertEqual(decision_bar_time(row), pd.Timestamp("2026-05-01 09:31", tz=ET))

    def test_near_edge_long_is_fvg_high(self):
        row = _sig(
            entry_type="FVG_AFTER_SMT",
            direction="LONG",
            fvg_low=100.0,
            fvg_high=102.0,
            entry_50=101.0,
        )
        self.assertEqual(near_edge_price(row), 102.0)

    def test_near_edge_short_is_fvg_low(self):
        row = _sig(
            entry_type="FVG_AFTER_SMT",
            direction="SHORT",
            fvg_low=100.0,
            fvg_high=102.0,
            entry_50=101.0,
        )
        self.assertEqual(near_edge_price(row), 100.0)


class TestClipAndSwings(unittest.TestCase):
    def test_clip_includes_decision_excludes_later(self):
        tape = _tape("2026-05-01 08:00", 120)
        decision = datetime(2026, 5, 1, 9, 31, tzinfo=ET)
        clipped = clip_1m(tape, decision, lookback=90)
        self.assertEqual(to_et(clipped["et"].iloc[-1]), to_et(decision))
        self.assertFalse((clipped["et"] > to_et(decision)).any())
        self.assertGreaterEqual(len(clipped), 90)

    def test_decision_bar_swing_is_unconfirmed(self):
        tape = _tape("2026-05-01 08:00", 120, swing_at=91)  # 08:00 + 91m = 09:31
        decision = datetime(2026, 5, 1, 9, 31, tzinfo=ET)
        clipped = clip_1m(tape, decision, lookback=90)
        mask = confirmed_swing_mask(clipped, decision)
        last = clipped.iloc[-1]
        self.assertEqual(int(last["Swing High"]), 1)
        self.assertFalse(bool(mask.iloc[-1]))

    def test_prior_bar_swing_is_confirmed(self):
        tape = _tape("2026-05-01 08:00", 120, swing_at=90)  # 09:30
        decision = datetime(2026, 5, 1, 9, 31, tzinfo=ET)
        clipped = clip_1m(tape, decision, lookback=90)
        mask = confirmed_swing_mask(clipped, decision)
        self.assertTrue(bool(mask.iloc[-2]))

    def test_15m_excludes_unclosed_bar(self):
        tape = _tape("2026-05-01 08:00", 120)
        decision = datetime(2026, 5, 1, 9, 31, tzinfo=ET)
        bars = clip_15m(tape, decision, n_bars=16)
        clock = to_et(decision) + pd.Timedelta(minutes=1)
        self.assertTrue((bars["close_time"] <= clock).all())
        self.assertFalse((bars["et"] == pd.Timestamp("2026-05-01 09:30", tz=ET)).any())
        self.assertTrue((bars["et"] == pd.Timestamp("2026-05-01 09:15", tz=ET)).any())


class TestSampleAndIds(unittest.TestCase):
    def test_shuffle_is_reproducible_and_ids_unique(self):
        rows = []
        for i in range(12):
            rows.append(
                {
                    "date": "2026-05-01",
                    "sw2_conf_time": datetime(2026, 5, 1, 8, i, tzinfo=ET),
                    "smt_time": datetime(2026, 5, 1, 8, i + 1, tzinfo=ET),
                    "direction": "LONG" if i % 2 == 0 else "SHORT",
                    "instrument": "ES" if i < 6 else "NQ",
                    "entry_type": "SMT_IN_FVG",
                    "session": "NY Morning",
                    "combined_15m_bias": "BULLISH",
                    "window": "mayaug",
                    "fvg_bar": pd.NaT,
                }
            )
        df = pd.DataFrame(rows)
        a = shuffled_order(df, seed=42)
        b = shuffled_order(df, seed=42)
        self.assertEqual(list(a["id"]), list(b["id"]))
        self.assertEqual(list(a["stored_order"]), list(range(1, 13)))
        self.assertEqual(a["id"].nunique(), 12)
        c = shuffled_order(df, seed=99)
        self.assertNotEqual(list(a["sw2_conf_time"]), list(c["sw2_conf_time"]))

    def test_ids_avoid_ambiguous_glyphs(self):
        ids = random_ids(200, seed=43)
        blob = "".join(ids)
        for ch in "ilo01":
            self.assertNotIn(ch, blob)


class TestRenderBlind(unittest.TestCase):
    def test_png_has_no_leaks_and_filename_is_id(self):
        es = _tape("2026-05-01 08:00", 120, px=100.0)
        nq = _tape("2026-05-01 08:00", 120, px=20100.0)
        row = _sig()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{row['id']}.png"
            render_signal(row, es, nq, path)
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "ab23cd45.png")
            self.assertEqual(scan_path_for_leaks(path), [])
            strip_png_text(path)
            self.assertEqual(scan_path_for_leaks(path), [])

    def test_15m_bias_helpers(self):
        from backtest.discretion_review.render import bias_aligns, bias_face, detect_15m_swings

        self.assertEqual(bias_aligns("STRONGLY BULLISH", "LONG"), "with")
        self.assertEqual(bias_aligns("BEARISH (SLOWING)", "LONG"), "against")
        self.assertNotEqual(bias_face("STRONGLY BULLISH"), bias_face("STRONGLY BEARISH"))
        tape = _tape("2026-05-01 08:00", 120)
        bars = clip_15m(tape, datetime(2026, 5, 1, 9, 31, tzinfo=ET), n_bars=16)
        marked = detect_15m_swings(bars)
        self.assertGreater(len(marked), 0)
        self.assertEqual(int(marked.iloc[-1]["swing_high"]), 0)
        self.assertEqual(int(marked.iloc[-1]["swing_low"]), 0)

    def test_html_template_has_no_outcome_words(self):
        html = Path("backtest/discretion_review/review_template.html").read_text(encoding="utf-8")
        self.assertEqual(leak_hits(html), [])
        self.assertIn("const REASONS", html)
        self.assertIn("download decisions.csv", html)
        self.assertIn("img.onerror", html)

    def test_write_html_embeds_png_bytes(self):
        from backtest.discretion_review.build_pack import png_data_uri, write_html

        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "ab23cd45.png"
            png.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
            )
            out = Path(tmp) / "review.html"
            write_html(
                [{"id": "ab23cd45", "stored_order": 1, "image": png_data_uri(png)}],
                out,
                "pilot-test",
            )
            html = out.read_text(encoding="utf-8")
            self.assertIn("data:image/png;base64,", html)
            self.assertNotIn("images/ab23cd45.png", html)


class TestScoring(unittest.TestCase):
    def test_never_filled_and_skips_are_zero_in_take_book(self):
        id_map = pd.DataFrame(
            [
                {
                    "stored_order": 1,
                    "id": "aaaa1111",
                    "window": "mayaug",
                    "date": "2026-05-01",
                    "sw2_conf_time": datetime(2026, 5, 1, 9, 0, tzinfo=ET),
                    "direction": "LONG",
                    "instrument": "ES",
                    "entry_type": "FVG_AFTER_SMT",
                    "session": "NY Morning",
                    "combined_15m_bias": "BULLISH",
                    "decision_time": "x",
                },
                {
                    "stored_order": 2,
                    "id": "bbbb2222",
                    "window": "mayaug",
                    "date": "2026-05-01",
                    "sw2_conf_time": datetime(2026, 5, 1, 9, 10, tzinfo=ET),
                    "direction": "SHORT",
                    "instrument": "NQ",
                    "entry_type": "SMT_IN_FVG",
                    "session": "NY Morning",
                    "combined_15m_bias": "BEARISH",
                    "decision_time": "x",
                },
                {
                    "stored_order": 3,
                    "id": "cccc3333",
                    "window": "sepjan",
                    "date": "2025-10-01",
                    "sw2_conf_time": datetime(2025, 10, 1, 9, 0, tzinfo=ET),
                    "direction": "LONG",
                    "instrument": "ES",
                    "entry_type": "SMT_IN_FVG",
                    "session": "NY Morning",
                    "combined_15m_bias": "BULLISH",
                    "decision_time": "x",
                },
                {
                    "stored_order": 4,
                    "id": "dddd4444",
                    "window": "sepjan",
                    "date": "2025-10-02",
                    "sw2_conf_time": datetime(2025, 10, 2, 9, 0, tzinfo=ET),
                    "direction": "LONG",
                    "instrument": "ES",
                    "entry_type": "FVG_AFTER_SMT",
                    "session": "NY Morning",
                    "combined_15m_bias": "BULLISH",
                    "decision_time": "x",
                },
            ]
        )
        fills = id_map.copy()
        fills["outcome"] = ["win", "loss", "no_fill_timeout", "win"]
        fills["realized_R"] = [2.0, -1.0, float("nan"), 1.5]
        decisions = pd.DataFrame(
            [
                {"id": "aaaa1111", "stored_order": 1, "decision": "take", "reason_code": "", "reason_label": ""},
                {"id": "bbbb2222", "stored_order": 2, "decision": "skip", "reason_code": 1, "reason_label": "no clear impulse"},
                {"id": "cccc3333", "stored_order": 3, "decision": "take", "reason_code": "", "reason_label": ""},
                {"id": "dddd4444", "stored_order": 4, "decision": "skip", "reason_code": 5, "reason_label": "choppy range"},
            ]
        )
        work = attach_r(decisions, id_map, fills)
        self.assertEqual(assigned_r("no_fill_timeout", float("nan")), 0.0)
        self.assertEqual(list(work["all_r"]), [2.0, -1.0, 0.0, 1.5])
        self.assertEqual(list(work["take_r"]), [2.0, 0.0, 0.0, 0.0])
        self.assertAlmostEqual(work["delta_r"].sum(), -( -1.0 + 1.5 ))
        first, second = half_split(work)
        self.assertEqual(list(first["id"]), ["aaaa1111", "bbbb2222"])
        self.assertEqual(list(second["id"]), ["cccc3333", "dddd4444"])
        text = report(work)
        self.assertIn("held-out", text.lower())
        self.assertIn("no clear impulse", text)
        self.assertNotIn("target_R", text)


if __name__ == "__main__":
    unittest.main()
