"""Native desktop smoke tests for Windows and macOS: the real backend on a real desktop session, no model.
Opt in with ROOKS_DESK_E2E_NATIVE=1 (CI does this on windows-2022 and macos-14 runners, which have a logged-in desktop).

Windows: screenshot, open Notepad, find and focus its window, type with real SendInput events, read the text back
through UI Automation. macOS: report the permission state; with Screen Recording / Accessibility granted (not the case
on a fresh Mac or, usually, on CI) it also takes a screenshot and reads TextEdit. Missing permissions are a skip with
the reason, never a silent pass."""

import os
import subprocess
import sys
import time

import pytest

from rooks_desk.backends import get_backend

pytestmark = [pytest.mark.gui, pytest.mark.skipif(os.getenv("ROOKS_DESK_E2E_NATIVE") != "1",
                                                  reason="set ROOKS_DESK_E2E_NATIVE=1 on a Windows or macOS desktop")]
TEXT = "Hello from Rooks Desk 42"


def _not_blank(img) -> bool:
    lo, hi = img.convert("L").getextrema()
    return hi - lo > 10


def _wait_window(b, needle: str, timeout: float = 15):
    end = time.time() + timeout
    while time.time() < end:
        for w in b.windows():
            if needle in w.title.lower() or needle in w.app.lower():
                return w
        time.sleep(0.5)
    return None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_notepad_round_trip(tmp_path):
    b = get_backend("windows")
    assert b.monitors()
    assert _not_blank(b.screenshot()), "the screenshot is a single colour"
    b.open_app("notepad")
    try:
        w = _wait_window(b, "notepad")
        assert w, f"no Notepad window in {[x.title for x in b.windows()]}"
        b.focus_window(w)
        time.sleep(0.5)
        b.type_text(TEXT)
        time.sleep(0.8)
        assert b.ax_available()[0]
        texts = [e.value for e in b.accessibility(w) if e.role in ("edit", "document")]
        assert any(TEXT in t for t in texts), texts
        shot = b.screenshot()
        shot.save(os.path.join(os.getenv("ROOKS_DESK_E2E_SHOTS", str(tmp_path)), "windows-notepad.png"))
    finally:
        subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_macos_permissions_and_textedit(tmp_path):
    b = get_backend("macos")
    perms = b.permissions()
    assert set(perms) == {"screen", "input", "accessibility"}
    print("macOS permissions:", {k: v["ok"] for k, v in perms.items()})
    if not perms["screen"]["ok"]:
        pytest.skip("Screen Recording is not granted to this process (expected on CI; `rooks-desk setup` asks on a real Mac)")
    assert _not_blank(b.screenshot())
    b.open_app("TextEdit")
    try:
        w = _wait_window(b, "textedit")
        assert w, f"no TextEdit window in {[x.app for x in b.windows()]}"
        if not perms["accessibility"]["ok"]:
            pytest.skip("Accessibility is not granted, so Rooks Desk cannot type here (expected on CI)")
        b.hotkey(["cmd", "n"])
        time.sleep(1)
        b.type_text(TEXT)
        time.sleep(0.8)
        assert any(TEXT in e.value for e in b.accessibility())
    finally:
        subprocess.run(["osascript", "-e", 'quit app "TextEdit" saving no'], capture_output=True)
