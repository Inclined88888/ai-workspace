import os
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture
def project(tmp_path, monkeypatch):
    p = tmp_path / "proj"
    p.mkdir()
    (p / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (p / "test_calc.py").write_text("from calc import add\nassert add(2, 3) == 5, 'add is broken'\nprint('tests pass')\n")
    cfg = tmp_path / "rooks.toml"
    cfg.write_text("")
    monkeypatch.setenv("ROOKS_CONFIG", str(cfg))
    monkeypatch.setenv("AI_WORKSPACE_APP_FILE", str(tmp_path / "no-app.json"))  # never pick up a real running app
    for k in ("ROOKS_PROVIDER", "ROOKS_MODEL", "ROOKS_BASE_URL", "WORKBENCH_URL", "WORKBENCH_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return p


def rooks(project, *args, stdin="", env=None):
    e = {**os.environ, "PYTHONPATH": str(PKG), "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **(env or {})}
    return subprocess.run([sys.executable, "-m", "rooks_code", "-C", str(project), *args] if args and args[0] not in (
        "undo", "receipts", "doctor", "init", "mcp", "version") else [sys.executable, "-m", "rooks_code", *args],
        input=stdin, capture_output=True, text=True, encoding="utf-8", env=e, timeout=60)
