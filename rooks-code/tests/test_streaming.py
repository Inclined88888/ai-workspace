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


def test_list_models_failure_returns_empty():
    from fakes import FakeModels
    srv = FakeModels(fail=True)
    providers._models_cache.clear()
    assert providers.list_models(srv.url, "bad") == []
    srv.close()
