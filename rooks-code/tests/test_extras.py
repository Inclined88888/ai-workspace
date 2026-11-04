"""Project instructions, checklist, web, background jobs and sub-agents (extras.py), driven through the real agent loop."""

import json
import sys
import time

import pytest

from rooks_code import config, extras
from rooks_code.agent import Agent
from rooks_code.providers import Reply, ToolCall


def assistant(text, calls=()):
    return Reply(text, list(calls), 10, 5)


def call(name, args):
    call.n = getattr(call, "n", 0) + 1
    return ToolCall(f"c{call.n}", name, args)


class FakeModel:
    """In-process model: replies in order and records what it was sent."""

    model = "m"

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def complete(self, system, messages, tools, on_text=None):
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages)), "tools": tools})
        return self.script.pop(0)


class UI:
    color = False

    def __init__(self):
        self.out, self.lists = [], []

    def __getattr__(self, name):
        return lambda *a, **k: self.out.append((name, a))

    def todos(self, items):
        self.lists.append(items)

    def ask(self, plan):
        return "y"


def settings(root, **kw):
    s = config.Settings(root=root, provider="openai", kind="openai", base_url="http://127.0.0.1:9/v1", model="m", learn=False)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_project_instructions_reach_the_model(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("Run tests with make test. Never edit generated/.")
    model = FakeModel([assistant("ok")])
    a = Agent(settings(tmp_path), UI(), model=model)
    assert a.instruction_files == [str(tmp_path / "AGENTS.md")]
    a.run("hi")
    assert "Never edit generated/." in model.calls[0]["system"]


def test_instructions_from_a_parent_folder_up_to_the_repo_root(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "CLAUDE.md").write_text("root rule")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("package rule")
    text, files = extras.project_instructions(sub)
    assert text.index("package rule") < text.index("root rule") and len(files) == 2


def test_checklist_is_shown_and_kept(tmp_path):
    ui = UI()
    model = FakeModel([assistant("", [call("todo", {"items": [{"text": "read", "status": "done"}, {"text": "fix", "status": "doing"}]})]),
                       assistant("done")])
    a = Agent(settings(tmp_path), ui, model=model)
    a.run("do it")
    assert ui.lists[-1][1] == {"text": "fix", "status": "doing"} and a.extras.todos == ui.lists[-1]


def test_background_job_runs_and_stops(tmp_path):
    a = Agent(settings(tmp_path, auto_approve=True), UI(), model=FakeModel([]))
    py = sys.executable
    out = a.extras.jobs.start(f'{py} -c "import time; print(\'listening on 5173\', flush=True); time.sleep(60)"')
    assert "job1" in out and "listening on 5173" in out and "running" in out
    assert "listening" in a.extras.jobs.check("job1")
    assert a.extras.jobs.stop("job1") == "stopped job1"
    time.sleep(0.3)
    assert "finished" in a.extras.jobs.check()
    with pytest.raises(Exception):
        a.extras.jobs.check("job9")


def test_web_fetch_turns_a_page_into_text():
    title, text = extras.html_to_text("<html><head><title>Docs</title><script>x=1</script></head><body><nav>menu</nav>"
                                      "<h1>Install</h1><p>Run <code>pip install x</code>.</p></body></html>")
    assert title == "Docs" and "Install" in text and "pip install x" in text and "x=1" not in text and "menu" not in text


def test_sub_agent_reads_only_and_returns_its_summary(tmp_path):
    (tmp_path / "a.py").write_text("SECRET_FLAG = 1\n")
    ui = UI()
    model = FakeModel([
        assistant("", [call("task", {"goal": "find where SECRET_FLAG is defined"})]),
        # the sub-agent's turn: tries to write (refused), reads, answers
        assistant("", [call("write_file", {"path": "x.py", "content": "boom"})]),
        assistant("", [call("search", {"pattern": "SECRET_FLAG"})]),
        assistant("a.py line 1 defines SECRET_FLAG"),
        assistant("It is in a.py."),
    ])
    a = Agent(settings(tmp_path, auto_approve=True), ui, model=model)
    assert a.run("where is SECRET_FLAG?") == "It is in a.py."
    assert not (tmp_path / "x.py").exists()
    sub_tools = {t["name"] for t in model.calls[1]["tools"]}
    assert sub_tools == {"read_file", "list_files", "search", "web_search", "web_fetch"}
    task_result = [m for m in model.calls[-1]["messages"] if m["role"] == "tool"][-1]["content"]
    assert "a.py line 1" in task_result
    assert len([p for p in (tmp_path / ".rooks" / "runs").glob("*.messages.json")]) == 1  # the sub-agent saved no session


def test_mode_command_switches_the_tools_on_offer(tmp_path):
    from rooks_code import tui

    a = Agent(settings(tmp_path), UI(), model=FakeModel([]))
    ui = UI()
    tui.command(a, ui, "/mode", "plan")
    assert a.mode() == "plan"
    offered = {t["name"] for t in a.tool_specs()}
    assert "read_file" in offered and "write_file" not in offered and "run_command" not in offered
    tui.command(a, ui, "/mode", "build")
    assert "write_file" in {t["name"] for t in a.tool_specs()}


def test_plan_mode_will_not_write_a_file(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    ui = UI()
    model = FakeModel([
        assistant("", [call("write_file", {"path": "a.py", "content": "nope\n"})]),
        assistant("I only looked."),
    ])
    a = Agent(settings(tmp_path, auto_approve=True, mode="plan"), ui, model=model)
    a.run("change a.py")
    assert (tmp_path / "a.py").read_text() == "x = 1\n"
    offered = {t["name"] for t in model.calls[0]["tools"]}
    assert "write_file" not in offered and "read_file" in offered
    assert "not available" in model.calls[-1]["messages"][-1]["content"]


def test_before_tool_hook_can_stop_a_command(tmp_path):
    hook = tmp_path / ".rooks" / "hooks"
    hook.mkdir(parents=True)
    script = hook / "before_tool"
    script.write_text("#!/bin/sh\nread line\nprintf '%s' \"$line\" | grep -q run_command && exit 2\nexit 0\n")
    script.chmod(0o755)
    ui = UI()
    model = FakeModel([
        assistant("", [call("run_command", {"command": "echo hi"})]),
        assistant("Stopped."),
    ])
    a = Agent(settings(tmp_path, auto_approve=True), ui, model=model)
    assert a.run("run it") == "Stopped."
    assert "project hook" in model.calls[-1]["messages"][-1]["content"]


def test_build_sub_agent_may_edit_under_parent_approvals(tmp_path):
    (tmp_path / "bug.py").write_text("x = 1\n")
    ui = UI()
    model = FakeModel([
        assistant("", [call("task", {"goal": "set x to 2 in bug.py", "mode": "build"})]),
        assistant("", [call("edit_file", {"path": "bug.py", "old_text": "x = 1\n", "new_text": "x = 2\n"})]),
        assistant("Changed bug.py: x is now 2."),
        assistant("Fixed."),
    ])
    a = Agent(settings(tmp_path, auto_approve=True), ui, model=model)
    assert a.run("fix bug.py") == "Fixed."
    assert (tmp_path / "bug.py").read_text() == "x = 2\n"
    sub_tools = {t["name"] for t in model.calls[1]["tools"]}
    assert "edit_file" in sub_tools and "run_command" in sub_tools and "task" not in sub_tools


def test_claude_native_xml_tool_calls_are_understood():
    from rooks_code.providers import parse_text_tool_calls

    text = ('Starting.\n<function_calls>\n<invoke name="todo">\n<parameter name="items">[{"text": "a", "status": "doing"}]</parameter>\n'
            '</invoke>\n<invoke name="read_file">\n<parameter name="path">rooks_code/tui.py</parameter>\n<parameter name="limit">40</parameter>\n'
            '</invoke>\n<invoke name="todo">\n<parameter name="items">[{"text": "a", "status": "doing"}]</parameter>\n</invoke>\n'
            '<invoke name="rm_rf"><parameter name="x">/</parameter></invoke>\n</function_calls>')
    calls, rest = parse_text_tool_calls(text, {"todo", "read_file"})
    assert [(c.name, c.arguments) for c in calls] == [("todo", {"items": [{"text": "a", "status": "doing"}]}),
                                                      ("read_file", {"path": "rooks_code/tui.py", "limit": 40})]
    assert rest == "Starting."


def test_claude_style_tool_lines_and_modes(tmp_path, capsys):
    import io

    from rooks_code import tui
    from rooks_code.providers import ToolCall
    from rooks_code.tools import Plan

    out = io.StringIO()
    ui = tui.RichUI(out=out, color=False)
    ui.console.width = 100
    ui.tool_note("read src/app.py", read=True)
    ui.tool_note("read-only")  # approval chatter stays off the screen
    plan = Plan("command", "run: pytest -q", "pytest -q", lambda: ("", None, ""))
    ui.show_plan(plan)
    ui.tool_result(plan, "exit code 0\n3 passed", "ok")
    ui.assistant("Done. `/version` works.<function_calls><invoke name=\"x\"></invoke></function_calls>", final=True)
    text = out.getvalue() + capsys.readouterr().out
    assert "⏺ Read src/app.py" in text and "⏺ Bash pytest -q" in text and "3 passed" in text and "* Done." in text
    assert "read-only" not in text and "function_calls" not in text and "Done." in text

    class S:
        dry_run = auto_approve = False
        trusted = staticmethod(lambda: False)
    s = S()
    assert tui.mode_of(s) == "asks first"
    tui.set_mode(s, "auto-accept")
    assert tui.mode_of(s) == "auto-accept"
    tui.set_mode(s, "plan only")
    assert s.dry_run and tui.mode_of(s) == "plan only"
    # arguments named the way other agents name them still land
    assert ToolCall("1", "read_file", {"file_path": "a.py"}).arguments == {"path": "a.py"}
