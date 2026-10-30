"""Real desktop end-to-end tests: a real X server, real apps (Mousepad, xfce4-terminal), real mouse and keyboard
events, real accessibility tree. Opt in with ROOKS_DESK_E2E=1 on a Linux box with a display (Xvfb is fine):

  Xvfb :77 -screen 0 1600x1000x24 & DISPLAY=:77 xfwm4 & ; export DISPLAY=:77
  ROOKS_DESK_E2E=1 pytest -m gui tests/test_e2e_linux.py

The model is scripted (no key needed) unless ROOKS_DESK_E2E_MODEL=provider:model is set, in which case the same tasks
run with that real model and only the outcome is checked. ROOKS_DESK_E2E_SHOTS=DIR keeps the step screenshots."""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rooks_desk import llm, runner

pytestmark = [pytest.mark.gui, pytest.mark.skipif(
    not (sys.platform.startswith("linux") and os.getenv("ROOKS_DESK_E2E") == "1" and os.getenv("DISPLAY")),
    reason="set ROOKS_DESK_E2E=1 with an X display")]


def real_model():
    spec = os.getenv("ROOKS_DESK_E2E_MODEL")
    if not spec:
        return None
    p, _, m = spec.partition(":")
    return llm.make(p, m or None)


def keep(res, name):
    d = os.getenv("ROOKS_DESK_E2E_SHOTS")
    if d:
        out = Path(d) / name
        out.mkdir(parents=True, exist_ok=True)
        for f in Path(res.screenshots).glob("step-*.png"):
            shutil.copy(f, out / f.name)


def pkill(name):
    subprocess.run(["pkill", "-f", name], capture_output=True)
    time.sleep(0.5)


@pytest.fixture
def work(desk_home):
    return desk_home / "work"


class Steps(llm.ScriptedModel):
    """A scripted model that, like a person, first clears dialogs it did not expect (Mousepad's "restore the previous
    session?" after a crash), then carries on with its script."""

    def __init__(self, script):
        super().__init__(script)

    def complete(self, system, user, image_b64=None):
        if "previous session did not end normally" in user:
            self.prompts.append(user)
            return llm.Reply('{"thought": "an unexpected dialog; do not restore", "action": "click", "args": {"id": %d}}'
                             % find(user, r"button 'No'"))
        return super().complete(system, user, image_b64)


def find(prompt, pattern):
    m = re.search(r"\[(\d+)\] " + pattern, prompt)
    assert m, f"{pattern!r} not in the element list:\n{prompt[-2500:]}"
    return int(m.group(1))


@pytest.mark.skipif(not shutil.which("mousepad"), reason="needs mousepad")
def test_editor_write_and_save(work):
    pkill("mousepad")
    target = work / "rooks-desk-e2e.txt"
    text = "Hello from Rooks Desk.\nTyped by a real X11 keyboard: déjà vu, 100% ✓"

    def type_in_editor(prompt):
        assert "Mousepad" in prompt
        return {"thought": "the document area", "action": "type", "args": {"id": find(prompt, r"text '' "), "text": text}}

    def save_name(prompt):
        assert "Save As" in prompt, prompt[-1500:]
        return {"action": "type", "args": {"text": str(target), "clear": True}}

    def click_save(prompt):
        if re.search(r"\[\d+\] button 'Save'", prompt):
            return {"thought": "press the dialog's Save button", "action": "click", "args": {"id": find(prompt, r"button 'Save'")}}
        # The GTK dialog sometimes publishes no element list (seen on CI); a person would just press Enter in the name field
        assert "Save As" in prompt, prompt[-1500:]
        return {"thought": "no Save button listed; Enter saves", "action": "hotkey", "args": {"keys": "enter"}}

    script = [{"action": "open_app", "args": {"name": "mousepad", "wait": 2.5}}, type_in_editor,
              {"action": "hotkey", "args": {"keys": "ctrl+s"}}, save_name, click_save, {"action": "wait", "args": {"seconds": 1}},
              {"action": "done", "args": {"answer": f"saved {target}", "success": True}}]
    m = real_model() or Steps(script)
    res = runner.run_task(f"Open Mousepad, type exactly this text:\n{text}\nand save it as {target}", runner.Settings(max_steps=15),
                          approver=lambda a: True, model=m)
    keep(res, "editor")
    assert res.success, res.errors
    assert target.read_text(encoding="utf-8").rstrip("\n") == text
    pkill("mousepad")


def test_shell_tool_and_files(work):
    (work / "a.txt").write_text("alpha")
    (work / "b.md").write_text("beta")
    script = [{"action": "shell", "args": {"command": f"cd {work} && ls"}},
              {"action": "shell", "args": {"command": "wc -c a.txt b.md > sizes.txt && cat sizes.txt"}},
              {"action": "read_file", "args": {"path": str(work / "sizes.txt")}},
              {"action": "done", "args": {"answer": "sizes written", "success": True}}]
    asked = []
    res = runner.run_task(f"List the files in {work}, write their byte sizes to sizes.txt there", runner.Settings(),
                          approver=lambda a: (asked.append(a), True)[1], model=real_model() or llm.ScriptedModel(script))
    assert res.success
    assert "a.txt" in res.commands[0]["output"] and res.commands[0]["exit_code"] == 0
    assert "5 a.txt" in (work / "sizes.txt").read_text()
    assert any(a.category == "shell" for a in asked)  # the redirect writes a file, so it asked


@pytest.mark.skipif(not shutil.which("xfce4-terminal"), reason="needs xfce4-terminal")
def test_typing_into_a_real_terminal_asks_first(work):
    pkill("xfce4-terminal")
    marker = work / "from-terminal.txt"
    script = [{"action": "open_app", "args": {"name": "xfce4-terminal", "args": [f"--working-directory={work}"], "wait": 2.5}},
              {"action": "type", "args": {"text": f"echo typed-in-a-terminal > {marker.name}", "enter": True}},
              {"action": "wait", "args": {"seconds": 1}},
              {"action": "done", "args": {"answer": "ran it", "success": True}}]
    asked = []
    res = runner.run_task("type a command into the terminal", runner.Settings(), approver=lambda a: (asked.append(a), True)[1],
                          model=llm.ScriptedModel(script))
    keep(res, "terminal")
    assert res.success
    assert [a.category for a in asked] == ["shell"] and "typing a command into a terminal" in asked[0].summary.lower()
    assert marker.read_text().strip() == "typed-in-a-terminal"
    pkill("xfce4-terminal")


@pytest.mark.skipif(not shutil.which("xdotool"), reason="needs xdotool to press the hotkey like a person")
def test_kill_switch_hotkey(work):
    script = [{"action": "wait", "args": {"seconds": 20}}, {"action": "shell", "args": {"command": "touch should-not-run"}}]

    def press_later():
        time.sleep(2.5)
        subprocess.run(["xdotool", "key", "ctrl+alt+Escape"])

    import threading
    threading.Thread(target=press_later, daemon=True).start()
    t0 = time.time()
    res = runner.run_task("wait", runner.Settings(), approver=lambda a: True, model=llm.ScriptedModel(script))
    keep(res, "killswitch")
    assert res.stopped and "Ctrl+Alt+Esc" in res.stopped
    assert time.time() - t0 < 10
    assert not (work / "should-not-run").exists()
