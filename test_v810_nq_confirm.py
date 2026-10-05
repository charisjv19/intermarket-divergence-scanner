"""v8.10 dual-confirm: NQ-confirm can fire when ES-confirm already exists."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

import smt_scanner_v8_8 as v88
import smt_scanner_v8_10 as v810


ROOT = Path(__file__).resolve().parent
ES_BARS = ROOT / "live" / "bars" / "v88" / "MES.csv"
NQ_BARS = ROOT / "live" / "bars" / "v88" / "MNQ.csv"


def _run(mod, es_path: Path, nq_path: Path) -> pd.DataFrame:
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = mod.run(str(es_path), str(nq_path))
    frame = result[0] if isinstance(result, tuple) else result
    return frame if frame is not None else pd.DataFrame()


def _identities(frame: pd.DataFrame) -> set[str]:
    if frame is None or frame.empty:
        return set()
    out = set()
    for _, row in frame.iterrows():
        sw2 = pd.Timestamp(row["sw2_conf_time"], utc=True)
        out.add(f"{row['date']}|{sw2.isoformat()}|{row['direction']}|{row['instrument']}")
    return out


class TestV810DualConfirm(unittest.TestCase):
    def test_tagged_oos_still_exclusive(self):
        src = (ROOT / "smt_scanner_v8_8.py").read_text(encoding="utf-8")
        self.assertIn(
            "_last_smt_long_nq = None if _last_smt_long_es else detect_smt_v86",
            src,
        )

    def test_morning_0914_nq_confirm_emits_when_es_confirm_exists(self):
        if not ES_BARS.is_file() or not NQ_BARS.is_file():
            self.skipTest("live v8.8 bars not present")
        v88_out = _run(v88, ES_BARS, NQ_BARS)
        v810_out = _run(v810, ES_BARS, NQ_BARS)
        v88_ids = _identities(v88_out)
        v810_ids = _identities(v810_out)
        es_id = "2026-10-05|2026-10-05T13:24:00+00:00|LONG|ES"
        nq_id = "2026-10-05|2026-10-05T13:24:00+00:00|LONG|NQ"
        self.assertIn(es_id, v88_ids)
        self.assertNotIn(nq_id, v88_ids)
        self.assertIn(es_id, v810_ids)
        self.assertIn(nq_id, v810_ids)
        nq_rows = v810_out[
            (v810_out["instrument"] == "NQ")
            & (v810_out["direction"] == "LONG")
            & (pd.to_datetime(v810_out["sw2_conf_time"], utc=True) == pd.Timestamp("2026-10-05T13:24:00+00:00"))
        ]
        self.assertGreaterEqual(len(nq_rows), 1)
        sw1 = str(nq_rows.iloc[0]["nq_sw1_time"])
        self.assertIn("09:14", sw1)


if __name__ == "__main__":
    unittest.main()
