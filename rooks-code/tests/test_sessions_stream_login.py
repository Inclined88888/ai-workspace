"""Streaming, resuming a session, compaction, rooks login, doctor's diagnosis and the /model picker."""

import io
import json
import os
import stat
import sys

import pytest

from conftest import rooks
from fakes import FakeModel, anthropic_reply, openai_reply


@pytest.fixture
def fake():
    servers = []

    def make(script):
        servers.append(FakeModel(script))
        return servers[-1]
    yield make
    for s in servers:
        s.close()


class StreamUI:
    """The real UI, writing to a buffer that says it is a terminal, so answers stream."""

    def __new__(cls):
        from rooks_code.cli import UI

        out = io.StringIO()
        ui = UI(out=out, err=io.StringIO(), color=False)
        ui.can_stream = lambda: True
        return ui


def _settings(project, url, kind="openai", **kw):
    from rooks_code import config

    s = config.resolve({"provider": "openai-compatible", "base_url": url, "model": "fake-model", "yes": True}, project, file={})
    s.kind = kind
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_openai_answers_and_tool_calls_stream(project, fake):
    from rooks_code.agent import Agent

    m = fake([openai_reply("", [("read_file", {"path": "calc.py"})]), openai_reply("All read, nothing to change.")])
    ui = StreamUI()
    agent = Agent(_settings(project, m.url), ui)
    assert agent.run("look at calc.py") == "All read, nothing to change."
    assert all(r["body"].get("stream") for r in m.requests)
    assert "All read, nothing to change." in ui.out.getvalue()
    assert agent.messages[1]["tool_calls"][0]["function"]["name"] == "read_file"  # pieces of the call were joined
    assert json.loads(agent.messages[1]["tool_calls"][0]["function"]["arguments"]) == {"path": "calc.py"}
    agent.close()


def test_anthropic_streams_and_marks_cache_breakpoints(project, fake):
    from rooks_code.agent import Agent

    m = fake([anthropic_reply("", [("read_file", {"path": "calc.py"})]), anthropic_reply("Done reading.")])
    agent = Agent(_settings(project, m.url, kind="anthropic"), StreamUI())
    assert agent.run("read calc.py") == "Done reading."
    body = m.requests[-1]["body"]
    assert body["stream"] and body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    agent.close()


def test_piped_output_does_not_stream(project, fake):
    m = fake([openai_reply("plain answer")])
    r = rooks(project, "-P", "openai-compatible", "--base-url", m.url, "-m", "fake-model", "hello")
    assert r.returncode == 0 and "plain answer" in r.stdout
    assert not m.requests[0]["body"].get("stream")


def test_continue_picks_up_the_last_session(project, fake):
    m = fake([openai_reply("First answer."), openai_reply("Second answer.")])
    args = ("-P", "openai-compatible", "--base-url", m.url, "-m", "fake-model")
    assert rooks(project, *args, "remember the word teal").returncode == 0
    r = rooks(project, *args, "-c", "what word?")
    assert r.returncode == 0, r.stderr
    sent = [x["content"] for x in m.requests[-1]["body"]["messages"] if x["role"] == "user"]
    assert sent == ["remember the word teal", "what word?"]
    assert "continuing session" in r.stdout
    assert rooks(project, *args, "--resume", "nope", "x").returncode == 1


def test_long_sessions_are_compacted_before_the_window_fills(project, fake):
    from rooks_code.agent import Agent

    m = fake([openai_reply("one"), openai_reply("two"), openai_reply("SUMMARY: user said one, two."), openai_reply("three")])
    ui = StreamUI()
    ui.can_stream = lambda: False
    agent = Agent(_settings(project, m.url, context_tokens=0), ui)
    agent.run("first " + "x" * 4000)
    agent.run("second " + "y" * 4000)
    agent.s.context_tokens = int(agent._estimate() / 0.7) - 50  # the next prompt crosses the line
    agent.run("third")
    summary_call = m.requests[2]["body"]
    assert "compress a coding session" in summary_call["messages"][0]["content"]
    assert agent.messages[0]["content"].startswith("[Summary of the earlier part of this session]")
    assert "SUMMARY: user said one, two." in agent.messages[0]["content"]
    assert [x["content"] for x in agent.messages if x["role"] == "user"][-2:] == ["second " + "y" * 4000, "third"]
    rows = [json.loads(l) for l in agent.log.path.read_text().splitlines()]
    assert any(r["kind"] == "compaction" for r in rows)
    agent.close()


def test_login_checks_the_key_saves_it_privately_and_uses_it(project, fake, tmp_path):
    m = fake([openai_reply("ready"), openai_reply("hello from the saved key")])
    env = {"ROOKS_BASE_URL": m.url, "OPENAI_API_KEY": ""}
    r = rooks(project, "login", "-P", "openai", "-m", "fake-model", "--key-stdin", stdin="sk-test-saved-key\n", env=env)
    assert r.returncode == 0, r.stderr
    assert m.requests[0]["headers"]["authorization"] == "Bearer sk-test-saved-key"
    keys = tmp_path / "rooks-keys.json"
    assert json.loads(keys.read_text()) == {"openai": "sk-test-saved-key"}
    if os.name != "nt":
        assert stat.S_IMODE(keys.stat().st_mode) == 0o600
    cfg = (tmp_path / "rooks.toml").read_text()
    assert 'provider = "openai"' in cfg and "sk-test" not in cfg
    r = rooks(project, "hi", env=env)
    assert r.returncode == 0 and "hello from the saved key" in r.stdout
    assert m.requests[-1]["headers"]["authorization"] == "Bearer sk-test-saved-key"


def test_login_refuses_a_key_that_does_not_work(project, tmp_path):
    r = rooks(project, "login", "-P", "openai", "--key-stdin", stdin="sk-bad\n", env={"ROOKS_BASE_URL": "http://127.0.0.1:9/v1"})
    assert r.returncode == 1 and "did not answer" in r.stderr
    assert not (tmp_path / "rooks-keys.json").exists()


def test_key_shapes_are_recognised():
    from rooks_code import login

    for key, name in (("nvapi-x", "nvidia"), ("gsk_x", "groq"), ("sk-ant-x", "anthropic"), ("sk-or-x", "openrouter"),
                      ("AIzaX", "gemini"), ("fw_x", "fireworks"), ("wb_x", "workbench"), ("sk-x", "openai")):
        assert login.detect(key) == name, key
    assert login.detect("mystery") is None


def test_doctor_explains_what_is_missing(project):
    env = {k: "" for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
                           "NVIDIA_API_KEY", "XAI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "CEREBRAS_API_KEY",
                           "TOGETHER_API_KEY", "FIREWORKS_API_KEY")}
    env["OLLAMA_HOST"] = "127.0.0.1:9"
    r = rooks(project, "doctor", "-C", str(project), env=env)
    assert r.returncode == 1
    assert "rooks:" in r.stdout and "fix: run `rooks login`" in r.stdout


def test_model_picker_lists_and_switches(project, fake):
    m = fake([openai_reply("on model b")])
    r = rooks(project, "-P", "openai-compatible", "--base-url", m.url, "-m", "model-a", stdin="/model\n/model 2\nhello\n")
    assert "  2  model-b" in r.stdout and "model is now model-b" in r.stdout
    assert m.requests[-1]["body"]["model"] == "model-b"


def test_provider_switch_keeps_the_conversation(project, fake):
    m1, m2 = fake([openai_reply("from one")]), fake([openai_reply("from two")])
    cfg = project.parent / "rooks.toml"
    cfg.write_text(f'[providers.two]\nkind = "openai"\nbase_url = "{m2.url}"\nkey_env = ""\nmodel = "m2"\n')
    r = rooks(project, "-P", "openai-compatible", "--base-url", m1.url, "-m", "m1", stdin="hi\n/provider two\nagain\n")
    assert "now on two/m2" in r.stdout and "from two" in r.stdout
    assert [x["content"] for x in m2.requests[0]["body"]["messages"] if x["role"] == "user"] == ["hi", "again"]


def test_an_overloaded_provider_is_tried_again(project, fake, monkeypatch):
    from rooks_code import agent as agent_mod
    from rooks_code.agent import Agent

    monkeypatch.setattr(agent_mod, "RETRY_BASE_S", 0.01)
    m = fake([{"__status__": 503}, {"__status__": 429}, openai_reply("made it")])
    ui = StreamUI()
    a = Agent(_settings(project, m.url), ui)
    assert a.run("hi") == "made it" and len(m.requests) == 3
    assert "trying again" in ui.err.getvalue()
    m2 = fake([{"__status__": 401}])
    b = Agent(_settings(project, m2.url), StreamUI())
    assert b.run("hi").startswith("Model error") and len(m2.requests) == 1  # a refused key is not retried
    a.close(), b.close()


def test_no_price_warning_through_the_app_but_still_for_a_direct_model(project):
    from rooks_code.agent import Agent
    from rooks_code.cli import UI

    def warnings(provider):
        err = io.StringIO()
        s = _settings(project, "http://127.0.0.1:9", max_usd=1.0)
        s.provider = provider
        Agent(s, UI(out=io.StringIO(), err=err, color=False))
        return err.getvalue()

    assert "cannot be enforced" not in warnings("workbench")  # the app picks the model and keeps the spend
    assert "cannot be enforced" in warnings("openai-compatible")
