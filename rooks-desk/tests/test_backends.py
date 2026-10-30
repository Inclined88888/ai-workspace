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


def test_pynput_typing_uses_a_sensible_default_interval(monkeypatch):
    from rooks_desk.backends import _pynput

    typed = []
    sleeps = []
    backend = _pynput.PynputInput.__new__(_pynput.PynputInput)
    backend.kb = type("Keyboard", (), {"type": lambda self, char: typed.append(char)})()
    monkeypatch.setattr(_pynput.time, "sleep", sleeps.append)

    backend.type_text("hi")
    assert typed == ["h", "i"]
    assert sleeps == [0.02, 0.02]

    backend.type_text("!", interval=0.05)
    assert sleeps[-1] == 0.05


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
