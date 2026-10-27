"""The macOS adapter with osascript and screencapture REPLACED BY A MOCK. These tests check how commands are built,
how answers and errors are read, and the coordinate maths. They do not show that macOS behaves as expected: that
needs tests/macos_check.py on a real Mac."""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from rooks_companion.adapters.base import NotSupported  # noqa: E402
from rooks_companion.adapters.macos import JXA_ACTIVE, MacOSAdapter, display_for, window_point  # noqa: E402


class R:
    def __init__(self, out="", err="", code=0):
        self.stdout, self.stderr, self.returncode = out, err, code


class FakeRun:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        a = self.answers.pop(0) if self.answers else R("")
        if isinstance(a, Exception):
            raise a
        return a


def adapter(*answers):
    ad = MacOSAdapter(run=FakeRun(answers))
    ad._ax = ad._cg = None  # no CoreGraphics in this environment
    return ad


def test_arguments_go_in_argv_never_into_script_text():
    ad = adapter(R('{"pressed": true}'))
    hostile = 'x"); Application("Terminal").doScript("rm -rf ~'
    ad.press({"app": "TextEdit", "path": [0, 1], "label": hostile, "role": "Button", "bbox": [1, 2, 3, 4]})
    cmd = ad._run.calls[0]
    assert cmd[:4] == ["/usr/bin/osascript", "-l", "JavaScript", "-e"] and hostile not in cmd[4]
    assert json.loads(cmd[-1])["label"] == hostile and cmd[-2] == "--"


@pytest.mark.parametrize("err, expect", [
    ("execution error: Not authorized to send Apple events to System Events. (-1743)", "Automation"),
    ("System Events got an error: osascript is not allowed assistive access. (-25211)", "Accessibility"),
    ("Can't get application process \"Numbers\". (-1728)", "no longer there"),
])
def test_errors_are_explained(err, expect):
    ad = adapter(R(err=err, code=1))
    with pytest.raises((NotSupported, RuntimeError), match=expect):
        ad.active_app()


def test_timeouts_are_explained():
    ad = adapter(subprocess.TimeoutExpired(["osascript"], 15))
    with pytest.raises(RuntimeError, match="did not answer within"):
        ad.active_app()


def test_moved_or_gone_elements_are_not_pressed():
    with pytest.raises(RuntimeError, match="changed"):
        adapter(R('{"moved": true, "found": "Delete"}')).press({"app": "Mail", "path": [0, 3], "label": "Archive"})
    with pytest.raises(RuntimeError, match="gone"):
        adapter(R('{"gone": "element"}')).press({"app": "Mail", "path": [0, 3], "label": "Archive"})


def test_focus_is_verified(monkeypatch):
    ad = adapter(R("ok"), *([R("Finder")] * 40))
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="did not come to the front"):
        ad.focus("Numbers")
    ok = adapter(R("ok"), R("Finder"), R("Numbers"))
    ok.focus("Numbers")


def test_menu_and_set_value_answers():
    with pytest.raises(RuntimeError, match="disabled"):
        adapter(R('{"disabled": true}')).menu("TextEdit", ["File", "Save"])
    with pytest.raises(RuntimeError, match="No menu item"):
        adapter(R('{"missing": "nope"}')).menu("TextEdit", ["File", "Nope"])
    assert adapter(R('{"set": true, "now_length": 5}')).set_value({"app": "TextEdit", "path": [0, 1]}, "héllo")["set"]
    with pytest.raises(RuntimeError, match="did not accept"):
        adapter(R('{"set": false}')).set_value({"app": "TextEdit", "path": [0, 1]}, "x")


def test_windows_and_elements_parse():
    ad = adapter(R(json.dumps({"front": "TextEdit", "windows": [{"app": "TextEdit", "title": "a.txt", "id": 41, "bbox": [100, 50, 800, 600], "focused": True}]})))
    assert ad.windows()[0]["id"] == 41
    ad = adapter(R(json.dumps({"window": {"title": "a.txt", "dialog": False}, "elements": [{"role": "TextArea", "label": "", "path": [0, 0]}]})))
    assert ad.elements("TextEdit")[0]["role"] == "TextArea" and ad.last_window["title"] == "a.txt"
    with pytest.raises(RuntimeError, match="not running"):
        adapter(R('{"error": "not_running"}')).elements("Numbers")


def test_coordinates_across_retina_and_multiple_displays():
    displays = [{"x": 0, "y": 0, "width": 1512, "height": 982, "scale": 2.0},          # MacBook Retina, main
                {"x": -1920, "y": -98, "width": 1920, "height": 1080, "scale": 1.0}]   # external monitor to the left
    # a window on the external monitor: normalised positions map into its negative coordinates
    assert window_point([-1800, 0, 1000, 800], 0.5, 0.25) == (-1300, 200)
    assert display_for(displays, -1300, 200)["scale"] == 1.0
    # the same normalised point on the Retina display gives points, not pixels, whatever the screenshot size
    assert window_point([100, 50, 800, 600], 0.5, 0.5) == (500, 350)
    assert display_for(displays, 500, 350)["scale"] == 2.0
    with pytest.raises(ValueError):
        window_point([0, 0, 10, 10], 1.2, 0.5)


def test_scripts_are_the_documented_ones():
    assert "frontmost: true" in JXA_ACTIVE
