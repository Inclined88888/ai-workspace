"""The companion's own rules, with an in-memory desktop. Run: python3 -m pytest companion/tests/test_engine.py"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from rooks_companion.adapters.base import Adapter  # noqa: E402
from rooks_companion.engine import Engine  # noqa: E402


class FakeDesk(Adapter):
    name, os = "fake", "Test"

    def __init__(self):
        self.log, self.typed = [], ""
        self.els = [{"role": "Button", "label": "Add one", "bbox": [0, 0, 10, 10], "path": [1]},
                    {"role": "Button", "label": "Send message", "bbox": [20, 0, 10, 10], "path": [2]},
                    {"role": "TextField", "label": "Password", "secure": True, "bbox": [0, 20, 10, 10], "path": [3]}]

    def permissions(self):
        return {"accessibility": True, "screen_recording": False, "notes": ""}

    def windows(self):
        return [{"app": "Notes", "title": "Notes", "focused": True}, {"app": "Terminal", "title": "zsh", "focused": False}]

    def active_app(self):
        return "Notes"

    def elements(self, app=""):
        return [dict(e) for e in self.els]

    def press(self, el):
        self.log.append(("press", el["label"]))

    def type_text(self, text):
        time.sleep(0.01)
        self.typed += text

    def key(self, combo):
        self.log.append(("key", combo))

    def scroll(self, d, n):
        self.log.append(("scroll", d))


@pytest.fixture()
def eng(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    (tmp_path / "secret.txt").write_text("outside")
    cfg = {"folders": [str(root)], "blocked_apps": ["Notes Private"], "stop_file": str(tmp_path / "STOP")}
    return Engine(FakeDesk(), cfg, journal_path=str(tmp_path / "journal.json")), root, tmp_path


def act(e, n, kind, approved=False, **args):
    return e.execute({"id": n, "kind": kind, "args": args, "approved": approved})


def test_observe_assigns_ids_that_expire_after_a_change(eng):
    e, _, _ = eng
    r = act(e, 1, "observe")
    assert r["ok"] and [x["id"] for x in r["result"]["elements"]] == ["e1", "e2", "e3"]
    assert act(e, 2, "click", target="e1", label="Add one", app="Notes")["ok"]
    r = act(e, 3, "click", target="e1", label="Add one", app="Notes")
    assert r.get("refused") and "Observe again" in r["error"]


def test_label_must_match_the_real_element(eng):
    e, _, _ = eng
    act(e, 1, "observe")
    r = act(e, 2, "click", target="e2", label="Add one", app="Notes")  # claims a harmless label for the Send button
    assert r.get("refused") and "not 'Add one'" in r["error"]
    assert e.adapter.log == []


def test_consequential_elements_need_approval_even_if_the_server_forgot(eng):
    e, _, _ = eng
    act(e, 1, "observe")
    r = act(e, 2, "click", target="e2", label="Send message", app="Notes")
    assert r.get("refused") and "not approved" in r["error"]
    act(e, 3, "observe")
    assert act(e, 4, "click", approved=True, target="e2", label="Send message", app="Notes")["ok"]


def test_never_types_into_password_fields(eng):
    e, _, _ = eng
    act(e, 1, "observe")
    r = act(e, 2, "type_text", target="e3", label="Password", text="hunter2", app="Notes")
    assert r.get("refused") and "password" in r["error"] and e.adapter.typed == ""


def test_blocked_apps_and_shells_are_refused_locally(eng):
    e, _, _ = eng
    assert act(e, 1, "key", keys="cmd+s", app="Terminal").get("refused")
    assert act(e, 2, "key", keys="cmd+s", app="Notes Private").get("refused")
    windows = act(e, 3, "observe")["result"]["windows"]
    assert all(w["app"] != "Notes Private" for w in windows)


def test_read_only_and_bad_keys(eng):
    e, _, _ = eng
    e.config["read_only"] = True
    assert act(e, 1, "key", keys="cmd+s", app="Notes").get("refused")
    e.config["read_only"] = False
    assert act(e, 2, "key", keys="rm -rf /", app="Notes").get("refused")
    assert act(e, 3, "key", keys="cmd+shift+s", app="Notes")["ok"]


def test_files_stay_inside_allowed_folders_and_never_overwrite(eng):
    e, root, tmp = eng
    assert act(e, 1, "files_list", path=str(root))["ok"]
    assert act(e, 2, "files_read", path=str(tmp / "secret.txt")).get("refused")
    os.symlink(tmp / "secret.txt", root / "link.txt")
    assert act(e, 3, "files_read", path=str(root / "link.txt")).get("refused")  # a link out of the folder
    assert act(e, 4, "files_mkdir", path=str(root / "Invoices"))["ok"]
    assert act(e, 5, "files_move", src=str(root / "a.txt"), dst=str(root / "Invoices"))["ok"]
    assert (root / "Invoices" / "a.txt").read_text() == "hello"
    (root / "b.txt").write_text("new")
    r = act(e, 6, "files_move", src=str(root / "b.txt"), dst=str(root / "Invoices" / "a.txt"))
    assert r.get("refused") and "already exists" in r["error"] and (root / "Invoices" / "a.txt").read_text() == "hello"
    assert act(e, 7, "files_write", path=str(root / "Invoices" / "a.txt"), text="x").get("refused")


def test_journal_replays_instead_of_repeating(eng, tmp_path):
    e, _, tmp = eng
    act(e, 1, "observe")
    assert act(e, 2, "click", target="e1", label="Add one", app="Notes")["ok"]
    e2 = Engine(e.adapter, e.config, journal_path=str(tmp / "journal.json"))  # the companion restarted
    r = e2.execute({"id": 2, "kind": "click", "args": {"target": "e1", "label": "Add one", "app": "Notes"}})
    assert r.get("replayed") and e.adapter.log.count(("press", "Add one")) == 1
    assert {x["id"] for x in e2.unacknowledged()} == {1, 2}
    e2.acknowledge(1)
    assert {x["id"] for x in e2.unacknowledged()} == {2}


def test_stop_interrupts_typing_midway(eng):
    e, _, _ = eng
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()
    r = e.execute({"id": 9, "kind": "type_text", "args": {"text": "x" * 2000, "app": "Notes"}}, stop.is_set)
    assert r.get("cancelled") and 0 < len(e.adapter.typed) < 2000


def test_local_stop_file(eng):
    e, _, tmp = eng
    (tmp / "STOP").write_text("")
    assert act(e, 1, "observe").get("cancelled")
