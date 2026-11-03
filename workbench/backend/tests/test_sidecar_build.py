"""Do not package a backend with an unsupported Python interpreter."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt" or not shutil.which("node"), reason="uses a POSIX fake interpreter and Node")
def test_sidecar_build_rejects_old_python_before_freezing(tmp_path):
    script = Path(__file__).resolve().parents[3] / "desktop/scripts/build-sidecar.mjs"
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\nprintf \'3.9.6\\n\'\n')
    python.chmod(0o755)
    result = subprocess.run(
        ["node", str(script), "--python", str(python), "--target", "aarch64-apple-darwin"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "Python 3.12 or newer" in result.stderr
    assert "3.9.6" in result.stderr
