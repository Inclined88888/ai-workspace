"""The scorecard checks itself: every task is set up cleanly, nothing passes by doing nothing (unless doing nothing is
right), and known-correct answers pass the code, SQL and number checks. Runs in its own workspace, no model needed."""

import os
import subprocess
import sys
from pathlib import Path


def test_scorecard_selftest():
    here = Path(__file__).resolve().parent.parent
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN"))}
    r = subprocess.run([sys.executable, "bench/scorecard.py", "--selftest"], cwd=here, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "0 problems" in r.stdout


def test_says_tolerates_typographic_hyphens_and_spaces():
    from bench import scorecard
    ok, _ = scorecard.says(r"stand-?up", r"9:00 AM")("**9:00\u202fAM** Stand\u2011up")
    assert ok
    assert scorecard.number(-3.5)("balance \u22123.5")[0]
