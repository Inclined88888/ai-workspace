"""Build dist/rooks.pyz: one file that runs with any Python 3.11+ on macOS, Windows and Linux (`python rooks.pyz`)."""

import shutil
import tempfile
import zipapp
from pathlib import Path

here = Path(__file__).resolve().parents[1]
out = here / "dist" / "rooks.pyz"
out.parent.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    shutil.copytree(here / "rooks_code", Path(tmp) / "rooks_code", ignore=shutil.ignore_patterns("__pycache__"))
    zipapp.create_archive(tmp, out, interpreter="/usr/bin/env python3", main="rooks_code.cli:main", compressed=True)
print(f"built {out} ({out.stat().st_size:,} bytes)")
