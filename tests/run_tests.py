"""Runs the Mesh Monitor regression tests and appends a one-line summary to tests/results.log.

    python tests/run_tests.py           # everything (GUI tests open windows for ~4 minutes)
    python tests/run_tests.py --quick   # skip the GUI tests (a few seconds)
"""
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if "--quick" in sys.argv:
    os.environ["MESH_TESTS_QUICK"] = "1"

suite = unittest.defaultTestLoader.loadTestsFromName("test_mesh_widget")
result = unittest.TextTestRunner(verbosity=2).run(suite)

bad = [t.id().split(".", 1)[1] for t, _ in result.failures + result.errors]
line = (f"{datetime.datetime.now():%Y-%m-%d %H:%M} {'PASS' if result.wasSuccessful() else 'FAIL'} "
        f"ran {result.testsRun}, failed {len(result.failures)}, errors {len(result.errors)}, "
        f"skipped {len(result.skipped)}{' (quick)' if '--quick' in sys.argv else ''}"
        + (f" :: {', '.join(bad)}" if bad else ""))
with open(os.path.join(HERE, "results.log"), "a", encoding="utf-8") as f:
    f.write(line + "\n")
print(line)
sys.exit(0 if result.wasSuccessful() else 1)
