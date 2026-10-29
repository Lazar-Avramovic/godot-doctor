from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


if __name__ == "__main__":
    suite = unittest.TestLoader().discover(
        start_dir=str(ROOT / "tests"),
        top_level_dir=str(ROOT),
    )
    if suite.countTestCases() == 0:
        print("RED: no tests discovered", file=sys.stderr)
        raise SystemExit(1)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
