import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_latest_json as m  # noqa: E402


def _touch(d: Path, name: str, sig: str | None = "SIG"):
    (d / name).write_bytes(b"x")
    if sig is not None:
        (d / (name + ".sig")).write_text(sig + "\n")


def test_maps_every_platform(tmp_path):
    _touch(tmp_path, "AI Workspace_aarch64-apple-darwin.app.tar.gz", "A")
    _touch(tmp_path, "AI Workspace_x86_64-apple-darwin.app.tar.gz", "B")
    _touch(tmp_path, "AI Workspace_0.1.0_x64_en-US.msi", "C")
    _touch(tmp_path, "AI Workspace_0.1.0_x64-setup.exe", "D")
    _touch(tmp_path, "AI Workspace_0.1.0_amd64.AppImage", "E")
    _touch(tmp_path, "AI Workspace_0.1.0_aarch64.dmg", None)  # not an updater bundle
    out = m.build(tmp_path, "v0.1.0", "o/r")
    p = out["platforms"]
    assert out["version"] == "0.1.0"
    assert p["darwin-aarch64"]["signature"] == "A"
    assert p["darwin-x86_64"]["signature"] == "B"
    assert p["windows-x86_64"]["signature"] == "C"
    assert p["windows-x86_64-nsis"]["signature"] == "D"
    assert p["linux-x86_64"]["url"] == (
        "https://github.com/o/r/releases/download/v0.1.0/AI%20Workspace_0.1.0_amd64.AppImage"
    )
    assert out["pub_date"].endswith("Z")


def test_unsigned_bundles_are_skipped_and_empty_fails(tmp_path):
    _touch(tmp_path, "x.AppImage", None)
    with pytest.raises(SystemExit):
        m.build(tmp_path, "0.1.0", "o/r")


def test_nsis_only_backfills_windows_key(tmp_path):
    _touch(tmp_path, "a-setup.exe", "D")
    assert m.build(tmp_path, "0.2.0", "o/r")["platforms"]["windows-x86_64"]["signature"] == "D"


def test_main_writes_file(tmp_path):
    _touch(tmp_path, "a.AppImage")
    assert m.main(["x", str(tmp_path), "v1.2.3", "o/r"]) == 0
    assert json.loads((tmp_path / "latest.json").read_text())["version"] == "1.2.3"
