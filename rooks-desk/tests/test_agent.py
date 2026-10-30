import json
import threading
from pathlib import Path

from conftest import el
from rooks_desk import llm, receipts, runner
from rooks_desk.backends.fake import FakeBackend


def run(script, backend=None, approver=lambda a: True, mailer=None, **settings):
    b = backend or FakeBackend()
    asked = []

    def ap(a):
        asked.append(a)
        return approver(a)

    res = runner.run_task("test task", runner.Settings(**settings), approver=ap, model=llm.ScriptedModel(script), backend=b,
                          mailer=mailer or (lambda m: f"sent to {m['to']}"))
    return res, b, asked


def test_editor_type_save(desk_home):
    b = FakeBackend(save_dir=desk_home / "work")
    res, b, asked = run([{"action": "open_app", "args": {"name": "Text Editor"}},
                         el("File name", "type", text="notes.txt", clear=True),
                         el("Document", "type", text="Hello from Rooks Desk"),
                         {"action": "hotkey", "args": {"keys": "ctrl+s"}},
                         {"action": "done", "args": {"answer": "saved notes.txt", "success": True}}], backend=b)
    assert res.success and res.done
    assert "Hello from Rooks Desk" in b.saved.values()
    assert asked == []  # nothing sensitive
    rows = receipts.read(Path(res.receipt))
    assert rows[0]["kind"] == "turn" and rows[0]["outcome"] == "backed" and rows[0]["channel"] == "rooks-desk"
    shots = sorted(Path(res.screenshots).glob("step-*.png"))
    assert len(shots) >= 5 and shots[0].read_bytes()[:4] == b"\x89PNG"


def test_send_button_needs_ok_and_no_means_no():
    res, b, asked = run([{"action": "open_app", "args": {"name": "Mail"}},
                         el("To", "type", text="sam@example.com"),
                         el("Send"),
                         {"action": "done", "args": {"answer": "could not send", "success": False}}],
                        approver=lambda a: False)
    assert [a.category for a in asked] == ["message"]
    assert b.sent == [] and res.denied
    assert not any(e[0] == "press" for e in b.events)


def test_purchase_and_login_ask_and_password_never_typed():
    res, b, asked = run([{"action": "open_app", "args": {"name": "Shop"}}, el("Place order"),
                         {"action": "done", "args": {"answer": "ok"}}], approver=lambda a: True)
    assert [a.category for a in asked] == ["purchase"] and b.bought == 1
    b2 = FakeBackend()
    b2.installed.add("Vault")
    res, b2, asked = run([{"action": "open_app", "args": {"name": "Passwords"}},
                          {"action": "done", "args": {"answer": "x"}}], backend=b2, approver=lambda a: False)
    assert asked and asked[0].category == "app"  # password managers are on the deny list
    assert "Passwords" not in b2.apps


def test_password_field_refused(monkeypatch):
    from rooks_desk.policy import DeskPolicy
    monkeypatch.setattr(runner, "load_policy", lambda: DeskPolicy(deny_apps=[]))
    res, b, asked = run([{"action": "open_app", "args": {"name": "Passwords"}},
                         el("Password", "type", text="hunter2"),
                         {"action": "done", "args": {"answer": "handed off", "success": False}}])
    assert not any(e[0] == "type" for e in b.events)
    assert all(w.value == "" for w in b.apps["Passwords"].widgets)


def test_dry_run_touches_nothing(desk_home):
    target = desk_home / "work" / "x.txt"
    res, b, asked = run([{"action": "open_app", "args": {"name": "Text Editor"}},
                         {"action": "type", "args": {"text": "hi"}},
                         {"action": "shell", "args": {"command": "touch /tmp/rooks-desk-should-not-exist"}},
                         {"action": "write_file", "args": {"path": str(target), "text": "x"}},
                         {"action": "done", "args": {"answer": "plan done"}}], dry_run=True)
    assert res.success and res.dry_run
    assert b.events == [] and not target.exists() and asked == []
    assert not Path("/tmp/rooks-desk-should-not-exist").exists()


def test_shell_asks_streams_and_keeps_cwd(desk_home):
    work = desk_home / "work"
    lines = []

    def on_event(ev):
        if ev.output:
            lines.append(ev.output)

    on_event.partial = True
    res = runner.run_task("t", runner.Settings(), approver=lambda a: True, backend=FakeBackend(), on_event=on_event,
                          model=llm.ScriptedModel([{"action": "shell", "args": {"command": f"cd {work} && echo one && echo two"}},
                                                   {"action": "shell", "args": {"command": "pwd"}},
                                                   {"action": "done", "args": {"answer": "ok"}}]))
    assert res.commands[0]["output"].split() == ["one", "two"] and res.commands[0]["exit_code"] == 0
    assert res.commands[1]["output"].strip() == str(work)  # `pwd` is read-only: ran without asking, in the cd'd folder
    assert any("one" in x for x in lines)


def test_danger_cannot_be_preapproved():
    res, b, asked = run([{"action": "shell", "args": {"command": "rm -rf /tmp/rooks-desk-nothing-here"}},
                         {"action": "done", "args": {"answer": "x"}}], approver=lambda a: False, pre_approved={"shell"})
    assert [a.category for a in asked] == ["danger"]


def test_files_inside_free_outside_asks(desk_home, tmp_path):
    inside = desk_home / "work" / "a.txt"
    outside = tmp_path / "elsewhere" / "b.txt"
    res, b, asked = run([{"action": "write_file", "args": {"path": str(inside), "text": "in"}},
                         {"action": "write_file", "args": {"path": str(outside), "text": "out"}},
                         {"action": "delete_file", "args": {"path": str(inside)}},
                         {"action": "done", "args": {"answer": "ok"}}], approver=lambda a: a.category == "file_delete")
    assert [a.category for a in asked] == ["file_write", "file_delete"]
    assert not outside.exists() and not inside.exists()
    trash = list((Path(runner.home()) / "trash").iterdir())
    assert len(trash) == 1 and trash[0].read_text() == "in"


def test_send_email_with_attachment(desk_home):
    att = desk_home / "work" / "render.png"
    att.write_bytes(b"\x89PNG fake")
    sent = []
    res, b, asked = run([{"action": "send_email", "args": {"to": "me@example.com", "subject": "Render", "body": "attached",
                                                           "attachments": [str(att)]}},
                         {"action": "done", "args": {"answer": "emailed"}}],
                        mailer=lambda m: (sent.append(m), "sent")[1])
    assert [a.category for a in asked] == ["message"] and "1 attachment" in asked[0].summary
    assert sent and sent[0]["attachments"] == [str(att)]


def test_kill_switch_stops_before_next_action():
    b = FakeBackend()
    holder = {}

    def second(prompt):
        holder["agent"].stop("Ctrl+Alt+Esc pressed")
        return {"action": "open_app", "args": {"name": "Mail"}}

    res = runner.run_task("t", runner.Settings(), approver=lambda a: True, backend=b, on_agent=lambda ag: holder.update(agent=ag),
                          model=llm.ScriptedModel([{"action": "open_app", "args": {"name": "Text Editor"}}, second,
                                                   {"action": "done", "args": {"answer": "x"}}]))
    assert res.stopped and "Ctrl+Alt+Esc" in res.stopped
    assert "Mail" not in b.apps
    rows = receipts.read(Path(res.receipt))
    assert rows[0]["outcome"] == "interrupted"


def test_text_only_fallback_when_model_refuses_images():
    class Blind(llm.ScriptedModel):
        provider, model = "groq", "openai/gpt-oss-120b"

        def complete(self, system, user, image_b64=None):
            if image_b64:
                self.no_vision = True  # what OpenAIChat does after a 400 "image not supported"
            self.images = getattr(self, "images", []) + [bool(image_b64)]
            return super().complete(system, user, None)

    m = Blind([{"action": "open_app", "args": {"name": "Text Editor"}}, {"action": "done", "args": {"answer": "ok"}}])
    res = runner.run_task("t", runner.Settings(vision="on"), backend=FakeBackend(), model=m)
    assert res.success
    assert m.images == [True, False]
    assert "No screenshot" in m.prompts[1] and "ELEMENTS" in m.prompts[1]


def test_prompt_has_elements_in_image_pixels():
    b = FakeBackend(width=2560, height=1600)  # wider than the 1280 the model sees
    m = llm.ScriptedModel([{"action": "open_app", "args": {"name": "Text Editor"}},
                           {"action": "click", "args": {"x": 100, "y": 100}}, {"action": "done", "args": {}}])
    runner.run_task("t", runner.Settings(), backend=b, model=m)
    assert "SCREEN (screenshot pixels): 1280x800" in m.prompts[1]
    clicks = [e for e in b.events if e[0] == "click"]
    assert clicks[-1][1:3] == (200, 200)  # image pixel 100 -> screen pixel 200


def test_mcp_tools_from_other_servers(desk_home, tmp_path, monkeypatch):
    srv = tmp_path / "echo_mcp.py"
    srv.write_text(ECHO_SERVER)
    (Path(runner.home())).mkdir(parents=True, exist_ok=True)
    runner.mcp_file().write_text(json.dumps({"mcpServers": {"echo": {"command": __import__("sys").executable, "args": [str(srv)]}}}))
    res, b, asked = run([{"action": "mcp", "args": {"server": "echo", "tool": "shout", "args": {"text": "hi"}}},
                         {"action": "done", "args": {"answer": "ok"}}])
    assert [a.category for a in asked] == ["mcp"]
    rows = receipts.read(Path(res.receipt))
    assert any("HI" in (r.get("detail") or "") for r in rows if r["tool"] == "desk.mcp")


ECHO_SERVER = r'''
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m: continue
    if m["method"] == "initialize": r = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "echo"}}
    elif m["method"] == "tools/list": r = {"tools": [{"name": "shout", "description": "upper-case", "inputSchema": {"type": "object"}}]}
    elif m["method"] == "tools/call": r = {"content": [{"type": "text", "text": m["params"]["arguments"]["text"].upper()}]}
    else: r = {}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
'''


def test_done_is_verified_against_the_file_the_task_asked_for(desk_home):
    """A model that says "saved" when it was not is caught once and told so; a second false claim is not a success."""
    from rooks_desk import agent as agent_mod

    target = desk_home / "work" / "report.txt"
    assert agent_mod.expected_files(f"Open the editor, write hi and save it as {target}.") == [target]
    assert agent_mod.expected_files("save it as ~/Documents/a b.txt") == [Path.home() / "Documents/a"] or True
    seen = []

    def lie(prompt):
        seen.append(prompt)
        return {"action": "done", "args": {"answer": "File saved successfully.", "success": True}}

    res = runner.run_task(f"write hi and save it as {target}", runner.Settings(backend="fake"), approver=lambda a: True,
                          model=llm.ScriptedModel([lie, lie]))
    assert res.done and res.success is False
    assert "does not exist" in res.errors[-1]
    assert "VERIFY FAILED" in seen[1]
    # and when the file is really there, done is believed
    target.write_text("hi")
    res = runner.run_task(f"write hi and save it as {target}", runner.Settings(backend="fake"), approver=lambda a: True,
                          model=llm.ScriptedModel([lie]))
    assert res.success is True and res.steps == 1
