"""/model picker, /effort, remembering the choice, and learning with the app (learning.py)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from rooks_code import cli, config, providers
from rooks_code.learning import Learning

MODELS = [
    {"id": "auto", "owned_by": "Smart router", "label": "Auto (picks per task)", "pick": True, "effort": False},
    {"id": "7/opus", "owned_by": "Claude plan", "label": "opus", "pick": True, "effort": True},
    {"id": "7/sonnet", "owned_by": "Claude plan", "label": "sonnet", "pick": True, "effort": True},
    {"id": "3/nvidia/nemotron-3-ultra", "owned_by": "NVIDIA NIM", "label": "nemotron-3-ultra", "pick": True, "effort": False},
    {"id": "3/nvidia/old-embed", "owned_by": "NVIDIA NIM", "label": "old-embed", "pick": False, "effort": False},
]


@pytest.fixture
def app():
    seen: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
            if self.path == "/v1/models":
                return self._send({"object": "list", "data": MODELS})
            if self.path.startswith("/v1/rooks/context"):
                return self._send({"context": "About the user:\n- Name is Azad", "skills": [
                    {"name": "weekly-report", "description": "When asked for the weekly report"}]})
            if self.path == "/v1/rooks/skills/weekly-report":
                return self._send({"name": "weekly-report", "description": "d", "steps": "1. read sales.csv"})
            return self._send({"detail": "no"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n))
            seen.append({"path": self.path, "body": body})
            if self.path == "/v1/rooks/remember":
                return self._send({"saved": 5})
            if self.path == "/v1/rooks/turn":
                return self._send({"conversation_id": 1})
            self._send({"choices": [{"message": {"role": "assistant", "content": "ready"}}], "usage": {}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()


def _app_settings(url, tmp_path):
    s = config.Settings(root=tmp_path)
    s.provider, s.kind, s.base_url, s.model, s.key = "workbench", "openai", url + "/v1", "default", "k" * 32
    return s


class UI:
    def __init__(self):
        self.out = []

    def p(self, text=""):
        self.out.append(str(text))

    def c(self, _style, text):
        return text

    def error(self, text):
        self.out.append("ERROR " + text)

    warn = error


class FakeAgent:
    def __init__(self, s):
        self.s = s
        self.model = providers.make(s)

    def switch(self, s, model=None):
        self.s = s
        self.model = model or providers.make(s)


def test_status_names_are_short():
    s = config.Settings(provider="workbench", model="default")
    assert cli.short_model(s) == "Auto"
    s.model, s.effort = "7/opus", "high"
    assert cli.short_model(s) == "opus · high"
    s.provider, s.model, s.effort = "nvidia", "nvidia/nemotron-3-ultra-550b-a55b", ""
    assert cli.short_model(s) == "nemotron-3-ultra-550b-a55b"


def test_model_list_is_grouped_short_and_hides_non_chat(app, tmp_path):
    url, _ = app
    agent, ui = FakeAgent(_app_settings(url, tmp_path)), UI()
    cli.model_command(agent, ui, "")
    text = "\n".join(ui.out)
    assert "Claude plan" in text and "NVIDIA NIM" in text and "old-embed" not in text
    assert "1 older or specialist models hidden" in text
    cli.model_command(agent, ui, "2")  # numbered as listed: Auto is 1, opus 2
    assert agent.s.model == "7/opus"


def test_effort_reaches_the_app_and_the_choice_is_remembered(app, tmp_path):
    url, seen = app
    agent, ui = FakeAgent(_app_settings(url, tmp_path)), UI()
    cli.set_model(agent, "7/sonnet", "high")
    assert agent.model.extra == {"reasoning_effort": "high"}
    agent.model.complete("sys", [{"role": "user", "content": "hi"}], [])
    assert [x for x in seen if x["path"] == "/v1/chat/completions"][-1]["body"]["reasoning_effort"] == "high"
    assert config.load_choice() == {"provider": "workbench", "model": "7/sonnet", "effort": "high"}
    cli.effort_command(agent, ui, "auto")
    assert agent.s.effort == "" and agent.model.extra == {}
    cli.effort_command(agent, ui, "extreme")
    assert ui.out[-1].startswith("ERROR")


def test_effort_is_never_sent_to_other_providers(tmp_path):
    s = config.Settings(root=tmp_path, provider="openai", kind="openai", base_url="http://x/v1", model="gpt", effort="high")
    assert providers.make(s).extra == {}


def test_next_start_keeps_the_picked_model_unless_one_is_named(tmp_path, monkeypatch):
    link = tmp_path / "app.json"
    monkeypatch.setenv("AI_WORKSPACE_APP_FILE", str(link))
    monkeypatch.setattr(config, "_reachable", lambda url: True, raising=False)
    config.save_choice("nvidia", "nvidia/llama-4", "")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
    assert config.resolve({"provider": "nvidia"}, tmp_path, {}).model == "nvidia/llama-4"
    assert config.resolve({"provider": "nvidia", "model": "other"}, tmp_path, {}).model == "other"
    config.save_choice("openai", "gpt-x", "")
    assert config.resolve({"provider": "nvidia"}, tmp_path, {}).model != "gpt-x"  # another provider's choice never leaks


def test_picker_goes_ai_then_model_then_thinking_level(app, tmp_path, monkeypatch):
    from rooks_code import tui

    url, _ = app
    agent, ui = FakeAgent(_app_settings(url, tmp_path)), UI()
    asked = []
    answers = iter(["Claude plan", "7/opus", "medium"])

    def choose(message, options, default=None):
        asked.append((message, [v for v, _ in options]))
        return next(answers)

    monkeypatch.setattr(tui, "_choose", choose)
    assert tui.pick_model(agent, ui)
    assert asked[0][1] == ["__auto__", "Claude plan", "NVIDIA NIM"]
    assert asked[1][1] == ["7/opus", "7/sonnet"]
    assert asked[2][0] == "How hard should it think?"
    assert (agent.s.model, agent.s.effort) == ("7/opus", "medium")
    asked.clear()
    answers = iter(["NVIDIA NIM", "__more__", "3/nvidia/old-embed"])
    assert tui.pick_model(agent, ui)
    assert asked[1][1] == ["3/nvidia/nemotron-3-ultra", "__more__"]  # short list, the rest behind "More"
    assert len(asked) == 3 and agent.s.model == "3/nvidia/old-embed" and agent.s.effort == ""  # no level for API models
    answers = iter([None])
    assert tui.pick_model(agent, ui) and agent.s.model == "3/nvidia/old-embed"  # Esc: no change


def test_learning_with_the_app(app, tmp_path):
    url, seen = app
    s = _app_settings(url, tmp_path)
    learn = Learning(s, session_id="sess-1")
    ctx = learn.context("make the weekly report")
    assert "Name is Azad" in ctx and "weekly-report" in ctx
    assert {x["name"] for x in learn.specs()} == {"remember", "load_skill"}
    assert learn.prepare("load_skill", {"name": "weekly-report"}).apply()[0].endswith("1. read sales.csv")
    assert learn.remember("Uses pnpm, not npm") == "Saved to your AI Workspace memory."
    msgs = [{"role": "user", "content": "make it"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "1|a"},
            {"role": "assistant", "content": "done"}]
    learn.after("make it", "done", msgs, wait=True)
    turn = [x for x in seen if x["path"] == "/v1/rooks/turn"][-1]["body"]
    assert turn["session"] == "sess-1" and turn["calls"] == [{"name": "read_file", "arguments": "{}", "result": "1|a"}]
    assert turn["untrusted"] is False


def test_learning_without_the_app_keeps_facts_locally(tmp_path):
    s = config.Settings(root=tmp_path, provider="nvidia", base_url="http://127.0.0.1:9/v1")
    learn = Learning(s)
    assert learn.context("anything") == ""
    assert learn.remember("Name is Azad").startswith("Saved.")
    assert learn.remember("name is  azad") == "Already known; nothing new saved."
    assert "- Name is Azad" in Learning(s).context("hi")  # a later session sees it
    assert [x["name"] for x in learn.specs()] == ["remember"]  # no skills without the app
    assert learn.after("p", "a", []) is None


def test_app_down_never_breaks_a_request(tmp_path):
    s = _app_settings("http://127.0.0.1:9", tmp_path)
    learn = Learning(s)
    assert learn.context("x") == ""
    assert learn.remember("Something durable").startswith("Error")
    learn.after("p", "a", [], wait=True)  # no exception


def test_memory_and_skills_commands_show_what_was_learned(app, tmp_path, monkeypatch):
    from rooks_code import tui

    url, seen = app
    agent, ui = FakeAgent(_app_settings(url, tmp_path)), UI()
    agent.learning = Learning(agent.s)
    orig = Learning._req

    def req(self, method, path, body=None):
        if path == "/memory":
            return {"facts": [{"content": "Name is Azad"}]}
        return orig(self, method, path, body)

    monkeypatch.setattr(Learning, "_req", req)
    tui.command(agent, ui, "/memory", "")
    assert "  • Name is Azad" in ui.out
    tui.command(agent, ui, "/skills", "")
    assert any("weekly-report" in line for line in ui.out)
    local = FakeAgent(config.Settings(root=tmp_path, provider="openai", kind="openai", base_url="http://127.0.0.1:9/v1", model="m"))
    local.learning = Learning(local.s)
    tui.command(local, ui, "/skills", "")
    assert "isn't connected" in ui.out[-1]
