"""v8.10 sw1–sw2 staleness: confirm-side sw1 may be at most 90 minutes older than sw2."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

import smt_scanner_v8_8 as v88
import smt_scanner_v8_10 as v810


ROOT = Path(__file__).resolve().parent
ET = "America/New_York"
V810_ES = ROOT / "live" / "bars" / "v810" / "MES.csv"
V810_NQ = ROOT / "live" / "bars" / "v810" / "MNQ.csv"
V88_ES = ROOT / "live" / "bars" / "v88" / "MES.csv"
V88_NQ = ROOT / "live" / "bars" / "v88" / "MNQ.csv"


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz=ET)


def _run(mod, es_path: Path, nq_path: Path) -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = mod.run(str(es_path), str(nq_path))
    frame = result[0] if isinstance(result, tuple) else result
    return frame if frame is not None else pd.DataFrame()


def _nq_long_sw2(frame: pd.DataFrame, sw2_utc: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    sw2 = pd.to_datetime(frame["sw2_conf_time"], utc=True)
    return frame[
        (frame["instrument"] == "NQ")
        & (frame["direction"] == "LONG")
        & (sw2 == pd.Timestamp(sw2_utc))
    ]


class TestV810Sw1Staleness(unittest.TestCase):
    def test_constant_is_90_and_v88_unwired(self):
        self.assertEqual(v810.SW1_STALENESS_MINS, 90)
        src88 = (ROOT / "smt_scanner_v8_8.py").read_text(encoding="utf-8")
        src810 = (ROOT / "smt_scanner_v8_10.py").read_text(encoding="utf-8")
        self.assertNotIn("SW1_STALENESS_MINS", src88)
        self.assertIn("if not sw1_is_fresh(t1c, t2c):", src810)
        self.assertNotIn("if not sw1_is_fresh(t1c, t2c):", src88)

    def test_boundary_equal_90_allowed_91_rejected(self):
        sw2 = _ts("2026-10-06 09:18:00")
        self.assertTrue(v810.sw1_is_fresh(_ts("2026-10-06 07:48:00"), sw2))
        self.assertFalse(v810.sw1_is_fresh(_ts("2026-10-06 07:47:00"), sw2))
        self.assertTrue(v810.sw1_is_fresh(sw2, sw2))

    def test_detect_smt_drops_only_stale_failed_parallel(self):
        sw2_t = _ts("2026-10-06 09:18:00")
        stale_t = _ts("2026-10-06 07:37:00")
        fresh_t = _ts("2026-10-06 08:00:00")
        conf = [
            (100.0, 0, stale_t),
            (99.5, 1, fresh_t),
            (99.0, 2, sw2_t),
        ]
        fail = [
            (200.0, 0, stale_t),
            (201.0, 1, fresh_t),
            (202.0, 2, sw2_t),
        ]
        smt = v810.detect_smt_v86(conf, fail, sw2_t, "LONG")
        self.assertIsNotNone(smt)
        self.assertEqual(pd.Timestamp(smt["sw1_conf"][2]), fresh_t)
        self.assertEqual(smt["confirmations_count"], 1)
        self.assertEqual(smt["alt_sw1_times"], "")

    def test_detect_smt_none_when_only_parallel_is_stale(self):
        sw2_t = _ts("2026-10-06 09:18:00")
        stale_t = _ts("2026-10-06 07:37:00")
        conf = [
            (100.0, 0, stale_t),
            (99.0, 1, sw2_t),
        ]
        fail = [
            (200.0, 0, stale_t),
            (201.0, 1, sw2_t),
        ]
        self.assertIsNone(v810.detect_smt_v86(conf, fail, sw2_t, "LONG"))

    def test_oct6_live_bars_drop_0918_keep_0936(self):
        if not V810_ES.is_file() or not V810_NQ.is_file():
            self.skipTest("live v8.10 bars not present")
        out = _run(v810, V810_ES, V810_NQ)
        stale = _nq_long_sw2(out, "2026-10-06T13:18:00+00:00")
        fresh = _nq_long_sw2(out, "2026-10-06T13:36:00+00:00")
        self.assertEqual(len(stale), 0, "07:37/09:18 is 101 min and must not emit")
        self.assertGreaterEqual(len(fresh), 1)
        sw1 = str(fresh.iloc[0]["nq_sw1_time"])
        self.assertIn("08:26", sw1)

    def test_oct5_0924_nq_confirm_still_emits(self):
        if not V88_ES.is_file() or not V88_NQ.is_file():
            self.skipTest("live v8.8 bars not present")
        out = _run(v810, V88_ES, V88_NQ)
        rows = _nq_long_sw2(out, "2026-10-05T13:24:00+00:00")
        if rows.empty:
            self.skipTest("Oct 5 09:24 rolled out of the 36h live window")
        primary = str(rows.iloc[0]["nq_sw1_time"])
        alts = str(rows.iloc[0]["alt_sw1_times"])
        self.assertTrue(
            "09:14" in alts or "09:14" in primary,
            f"09:14 missing after 90-min gate primary={primary!r} alts={alts!r}",
        )


if __name__ == "__main__":
    unittest.main()
