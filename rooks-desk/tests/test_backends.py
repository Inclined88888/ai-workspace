"""The real backends import everywhere (their OS libraries load lazily), and the key/coordinate helpers behave."""

import importlib
import sys

import pytest

from rooks_desk.backends import get_backend
from rooks_desk.backends.base import normalise_keys


@pytest.mark.parametrize("mod", ["macos", "windows", "linux", "fake", "_pynput"])
def test_import(mod):
    importlib.import_module(f"rooks_desk.backends.{mod}")


def test_keys():
    assert normalise_keys("Ctrl+Shift+S") == ["ctrl", "shift", "s"]
    assert normalise_keys(["command", "space"]) == ["cmd", "space"]
    assert normalise_keys("Return") == ["enter"]


def test_fake_selected(monkeypatch):
    monkeypatch.setenv("ROOKS_DESK_BACKEND", "fake")
    assert get_backend().name == "fake"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_macos_backend_constructs():
    b = get_backend("macos")
    assert set(b.permissions()) == {"screen", "input", "accessibility"}
    assert b.monitors()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_backend_constructs():
    b = get_backend("windows")
    assert b.monitors() and isinstance(b.windows(), list)
