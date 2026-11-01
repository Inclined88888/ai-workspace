"""Task 02: stream answers and tool calls. A local fake SSE server drives both protocols; tokens are printed live,
reasoning is dimmed, tool call deltas are assembled, and usage still feeds the cost caps."""

import pytest


@pytest.fixture(autouse=True)
def _stdout_is_a_tty(monkeypatch):
    """Streaming only happens on a real terminal; the test runner's captured stdout is not one, so force it."""
    import sys
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)


from fakes import FakeModel, anthropic_reply, anthropic_stream, openai_reply, openai_stream
from rooks_code import providers


class Collect:
    """The on_token callback: records (text, style) pairs."""

    def __init__(self):
        self.events = []

    def __call__(self, text, style):
        self.events.append((text, style))

    @property
    def text(self):
        return "".join(t for t, s in self.events if s == "text")

    @property
    def reasoning(self):
        return "".join(t for t, s in self.events if s == "reasoning")


def test_openai_stream_text_tokens_and_usage():
    srv = FakeModel([openai_stream(text="hello world", tokens=(111, 22))])
    cb = Collect()
    m = providers.OpenAIChat(srv.url, "", "m", on_token=cb)
    r = m.complete("sys", [{"role": "user", "content": "hi"}], [])
    srv.close()
    assert r.text == "hello world"
    assert cb.text == "hello world"  # printed live, in order
    assert (r.tokens_in, r.tokens_out) == (111, 22)  # usage from the final chunk still feeds the caps


def test_openai_stream_assembles_tool_call_deltas():
    srv = FakeModel([openai_stream(calls=[("edit_file", {"path": "a.py", "old_text": "x", "new_text": "y"})])])
    cb = Collect()
    m = providers.OpenAIChat(srv.url, "", "m", on_token=cb)
    r = m.complete("sys", [{"role": "user", "content": "hi"}], [{"name": "edit_file", "description": "", "parameters": {}}])
    srv.close()
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name == "edit_file"
    assert r.tool_calls[0].arguments == {"path": "a.py", "old_text": "x", "new_text": "y"}
    assert not r.tool_calls[0].bad_json


def test_openai_stream_reasoning_is_dimmed_not_text():
    srv = FakeModel([openai_stream(text="answer", reasoning="thinking out loud")])
    cb = Collect()
    m = providers.OpenAIChat(srv.url, "", "m", on_token=cb)
    r = m.complete("sys", [{"role": "user", "content": "hi"}], [])
    srv.close()
    assert r.text == "answer"
    assert cb.reasoning == "thinking out loud"
    assert "thinking" not in cb.text


def test_openai_falls_back_when_stream_is_refused():
    srv = FakeModel([openai_reply(text="plain", tokens=(5, 3))], refuse_stream=True)  # 400 on stream, JSON on plain
    cb = Collect()
    m = providers.OpenAIChat(srv.url, "", "m", on_token=cb)
    r = m.complete("sys", [{"role": "user", "content": "hi"}], [])
    srv.close()
    assert r.text == "plain" and (r.tokens_in, r.tokens_out) == (5, 3)
    assert len(srv.requests) == 2 and srv.requests[0]["body"].get("stream") is True  # tried stream, then plain


def test_openai_stream_error_mid_stream_raises():
    srv = FakeModel([[{"choices": [{"delta": {"content": "par"}}]},  # then a broken frame
                     "{not json", {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, "[DONE]"]])
    cb = Collect()
    m = providers.OpenAIChat(srv.url, "", "m", on_token=cb)
    r = m.complete("sys", [{"role": "user", "content": "hi"}], [])  # bad frames are skipped, not fatal
    srv.close()
    assert r.text == "par"


def test_anthropic_stream_text_tool_and_usage():
    srv = FakeModel([anthropic_stream(text="fixed it", calls=[("run_command", {"command": "pytest"})],
                                      tokens=(90, 30))])
    cb = Collect()
    m = providers.Anthropic(srv.url, "", "claude", on_token=cb)
    r = m.complete("sys", [{"role": "user", "content": "hi"}], [{"name": "run_command", "description": "", "parameters": {}}])
    srv.close()
    assert r.text == "fixed it"
    assert cb.text == "fixed it"
    assert len(r.tool_calls) == 1 and r.tool_calls[0].name == "run_command"
    assert r.tool_calls[0].arguments == {"command": "pytest"}
    assert (r.tokens_in, r.tokens_out) == (90, 30)


def test_anthropic_stream_thinking_is_dimmed():
    srv = FakeModel([anthropic_stream(text="done", thinking="let me think")])
    cb = Collect()
    m = providers.Anthropic(srv.url, "", "claude", on_token=cb)
    r = m.complete("sys", [{"role": "user", "content": "hi"}], [])
    srv.close()
    assert r.text == "done"
    assert cb.reasoning == "let me think"


def test_anthropic_falls_back_when_stream_is_refused():
    srv = FakeModel([anthropic_reply(text="plain", tokens=(7, 4))], refuse_stream=True)
    cb = Collect()
    m = providers.Anthropic(srv.url, "", "claude", on_token=cb)
    r = m.complete("sys", [{"role": "user", "content": "hi"}], [])
    srv.close()
    assert r.text == "plain" and (r.tokens_in, r.tokens_out) == (7, 4)
    assert len(srv.requests) == 2 and srv.requests[0]["body"].get("stream") is True


def test_no_on_token_means_no_stream_request():
    """Without a callback (e.g. a non-tty) the provider must not ask for a stream."""
    srv = FakeModel([openai_reply(text="x")])
    m = providers.OpenAIChat(srv.url, "", "m")  # no on_token
    m.complete("sys", [{"role": "user", "content": "hi"}], [])
    assert srv.requests[0]["body"].get("stream") is not True
    srv.close()


def test_list_models_hits_the_fake_server_and_caches():
    from fakes import FakeModels
    srv = FakeModels(ids=["a-model", "b-model"])
    providers._models_cache.clear()
    ids = providers.list_models(srv.url, "key")
    assert ids == ["a-model", "b-model"]
    # cached: a second call does not hit the server again
    srv.close()
    assert providers.list_models(srv.url, "key") == ["a-model", "b-model"]


def test_picker_selects_number_and_filters_unique_match():
    from rooks_code.cli import choose_model

    replies = iter(["bet", "2"])
    selected = choose_model(["alpha", "beta", "better"], lambda _: next(replies), lambda _: None)
    assert selected == "better"

    selected = choose_model(["alpha", "beta", "better"], lambda _: "alp", lambda _: None)
    assert selected == "alpha"


def test_picker_exact_unique_and_cancel():
    from rooks_code.cli import choose_model

    assert choose_model(["alpha", "beta"], lambda _: "beta", lambda _: None) == "beta"
    assert choose_model(["alpha", "beta"], lambda _: "", lambda _: None) is None


def test_model_picker_fake_server_lists_filters_and_selects_number():
    from fakes import FakeModels
    from rooks_code.cli import choose_model

    srv = FakeModels(ids=["alpha", "beta", "better"])
    providers._models_cache.clear()
    ids = providers.list_models(srv.url, "picker-test-key")
    replies = iter(["bet", "2"])
    shown = []
    selected = choose_model(ids, lambda _: next(replies), shown.append)
    srv.close()
    assert selected == "better"
    assert "beta" in "\n".join(shown) and "better" in "\n".join(shown)


def test_model_command_picks_number_from_fake_server(monkeypatch, tmp_path):
    import io
    from types import SimpleNamespace
    from fakes import FakeModels
    from rooks_code import cli

    server = FakeModels(ids=["first-model", "second-model"])
    providers._models_cache.clear()
    state = SimpleNamespace(provider="test", model="first-model", base_url=server.url, key_env="", key="", api_key="",
                            kind="openai", local=False, key_optional=True, headers={}, trusted=lambda: False,
                            dry_run=False, auto_approve=False)
    model = SimpleNamespace(model="first-model")
    agent = SimpleNamespace(s=state, model=model, messages=[], root=tmp_path,
                            log=SimpleNamespace(model="first-model"), tool_specs=lambda: [], run=lambda _: None)
    monkeypatch.setattr("sys.stdin", io.StringIO("/model\n2\n/exit\n"))
    cli.repl(agent, cli.UI(out=io.StringIO(), color=False))
    server.close()
    assert state.model == model.model == agent.log.model == "second-model"


def test_provider_command_switches_provider_and_keeps_messages(monkeypatch, tmp_path):
    import io
    from types import SimpleNamespace
    from rooks_code import cli

    messages = [{"role": "user", "content": "keep me"}]
    state = SimpleNamespace(provider="old", model="old-model", base_url="http://old/v1", key_env="OLD_KEY",
                            api_key="old-key", key="old-key", kind="openai", local=False, key_optional=False,
                            headers={}, trusted=lambda: False, dry_run=False, auto_approve=False)
    resolved = SimpleNamespace(provider="new", model="new-model", base_url="http://new/v1", key_env="NEW_KEY",
                               api_key="new-key", key="new-key", kind="openai", local=False, key_optional=False,
                               headers={})
    agent = SimpleNamespace(s=state, messages=messages, root=tmp_path, model=object(), log=SimpleNamespace(model="old-model"),
                            tool_specs=lambda: [], _egress=lambda *args: None, _token=lambda *args: None,
                            run=lambda _: None, close=lambda: None)
    monkeypatch.setitem(cli.config.PROVIDERS, "new", {"kind": "openai", "base_url": "http://new/v1", "key_env": "NEW_KEY", "model": "new-model"})
    monkeypatch.setattr(cli.config, "resolve", lambda *_args: resolved)
    monkeypatch.setattr(providers, "make", lambda *_args, **_kwargs: "new-model-instance")
    output = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO("/provider new\n/exit\n"))
    cli.repl(agent, cli.UI(out=output, color=False))
    assert (state.provider, state.model, state.base_url, state.key_env) == ("new", "new-model", "http://new/v1", "NEW_KEY")
    assert agent.model == "new-model-instance"
    assert agent.messages is messages and messages == [{"role": "user", "content": "keep me"}]


def test_list_models_failure_returns_empty():
    from fakes import FakeModels
    srv = FakeModels(fail=True)
    providers._models_cache.clear()
    assert providers.list_models(srv.url, "bad") == []
    srv.close()