"""desktop/sidecar: an ad hoc signed macOS build keeps its own secrets in a 0600 file, never re-asking the keychain."""

import importlib.util
import json
import stat
from pathlib import Path

SIDE = Path(__file__).resolve().parents[3] / "desktop" / "sidecar" / "workbench_server.py"


def _sidecar():
    spec = importlib.util.spec_from_file_location("workbench_server_under_test", SIDE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OldKeychain:
    def __init__(self, items):
        self.items, self.reads = items, 0

    def get_password(self, service, name):
        self.reads += 1
        return self.items.get(name)


def test_adhoc_build_moves_old_keychain_secrets_to_the_file_once(tmp_path, monkeypatch):
    side = _sidecar()
    kr = OldKeychain({"APP_SECRET": "old-app-secret-kept"})
    monkeypatch.setattr(side, "keychain_unstable", lambda: True)
    monkeypatch.setattr(side, "_keyring", lambda: kr)
    values, where = side.load_master_secrets(tmp_path)
    assert where == "file" and values["APP_SECRET"] == "old-app-secret-kept"
    path = tmp_path / ".desktop-secrets.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["APP_SECRET"] == "old-app-secret-kept"
    reads = kr.reads
    again, _ = side.load_master_secrets(tmp_path)
    assert again == values and kr.reads == reads  # the second start never touches the keychain


def test_keychain_is_stable_outside_a_frozen_mac_build(monkeypatch):
    side = _sidecar()
    monkeypatch.delattr(side.sys, "frozen", raising=False)
    assert side.keychain_unstable() is False
