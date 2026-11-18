"""The live copies in live_scanners/ must stay identical to the root originals.

The scanners that are currently live are copied into live_scanners/ so the
live folder shows exactly what is running. The root files remain the
version history. If you change a live version, change the root original
and re-copy it here (or the other way round) — this test fails if they drift.
"""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE_DIR = ROOT / "live_scanners"
LIVE_FILES = ("smt_scanner_v8_7.py", "smt_scanner_v8_8.py", "smt_scanner_v8_10.py")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LiveCopiesMatchRoot(unittest.TestCase):
    def test_live_copies_are_identical_to_root_originals(self):
        for name in LIVE_FILES:
            with self.subTest(scanner=name):
                self.assertTrue((LIVE_DIR / name).is_file(), f"missing live copy {name}")
                self.assertEqual(_sha(LIVE_DIR / name), _sha(ROOT / name),
                                 f"live_scanners/{name} differs from root {name}")


if __name__ == "__main__":
    unittest.main()
