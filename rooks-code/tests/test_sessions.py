"""Task 03: sessions survive and context never overflows."""

import sys
from pathlib import Path

import pytest

from rooks_code import sessions


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def make_agent(messages, model="gpt-4.1"):
    """A stand-in Agent with what compaction touches."""

    class M:
        def complete(self, system, msgs, tools):
            return type("R", (), {"text": "summary: user wanted the bug fixed"})()

    class S:
        pass

    a = type("A", (), {})()
    a.messages = list(messages)
    a.system = "system prompt"
    a.s = S()
    a.s.model = model
    a.model = M()
    return a


def test_context_window_table_and_fallback():
    assert sessions.context_window("gpt-4.1-mini") == 1_000_000
    assert sessions.context_window("openai/gpt-4.1") == 1_000_000  # provider prefix stripped
    assert sessions.context_window("claude-sonnet-4-5") == 200_000
    assert sessions.context_window("totally-unknown-model") == sessions.DEFAULT_CONTEXT
    # a provider /models listing wins over the table
    listed = {"data": [{"id": "custom", "context_length": 32000}]}
    assert sessions.context_window("custom", listed) == 32_000


def test_resume_round_trip(store):
    st = sessions.SessionStore(store / "proj", "openai", "gpt-4.1-mini")
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"}]
    for m in msgs:
        st.save_message(m)
    meta, back = sessions.load(st.id)
    assert meta["model"] == "gpt-4.1-mini" and meta["cwd"] == str(store / "proj")
    assert back == msgs


def test_newest_for_folder(store):
    proj = store / "proj"
    proj.mkdir()
    a = sessions.SessionStore(proj, "openai", "m1")
    a.save_message({"role": "user", "content": "first"})
    import time as _t
    _t.sleep(0.02)  # the newest is picked by mtime; give b a later one
    b = sessions.SessionStore(proj, "openai", "m2")
    b.save_message({"role": "user", "content": "second"})
    import os
    os.utime(b.path, (a.path.stat().st_mtime + 10, a.path.stat().st_mtime + 10))
    other = store / "other"
    other.mkdir()
    sessions.SessionStore(other, "openai", "m3").save_message({"role": "user", "content": "elsewhere"})
    found = sessions.newest_for(proj)
    assert found is not None and found[0]["model"] == "m2"
    assert all(r["model"] != "m3" for r in sessions.list_all(proj))  # only this folder's


def test_compaction_triggers_at_75_percent():
    window = sessions.context_window("gpt-4.1")  # 1,000,000
    big = "x" * (window * 4 // 2 // 40)  # enough messages to pass 75% of a small model instead
    a = make_agent([{"role": "user", "content": big}] * 8, model="mistral")  # 32k window
    notes = []
    assert sessions.maybe_compact(a, notes.append)
    assert notes and "compacting" in notes[0]
    assert a.messages[0]["content"].startswith("[summary of the conversation so far]")


def test_compaction_keeps_system_and_last_6_turns():
    msgs = [{"role": "user", "content": f"turn {i}"} for i in range(10)]
    a = make_agent(msgs)
    sessions.compact(a, lambda x: None)
    assert a.messages[0]["content"].startswith("[summary")
    kept = [m["content"] for m in a.messages[1:]]
    assert kept == [f"turn {i}" for i in range(10 - sessions.KEEP_TURNS, 10)]


def test_tool_call_pairs_never_split():
    # an assistant message with two tool calls sits right at the KEEP_TURNS boundary
    pair = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "function": {"name": "f", "arguments": "{}"}},
        {"id": "c2", "function": {"name": "g", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "tool", "tool_call_id": "c2", "content": "r2"}]
    msgs = [{"role": "user", "content": f"t{i}"} for i in range(sessions.KEEP_TURNS - 1)] + pair + [
        {"role": "user", "content": "latest"}]
    old, keep = sessions.split_for_compaction(msgs)
    # the boundary must not land between the assistant and its tool answers
    roles = [m["role"] for m in keep]
    if "assistant" in roles:
        i = roles.index("assistant")
        m = keep[i]
        want = {c["id"] for c in m.get("tool_calls", [])}
        got = {x.get("tool_call_id") for x in keep[i + 1:] if x.get("role") == "tool"}
        assert want <= got
    # and the split version is exactly old + keep
    assert old + keep == msgs


def test_compact_too_short_is_a_no_op():
    a = make_agent([{"role": "user", "content": "only"}])
    assert sessions.compact(a, lambda x: None) is False
    assert len(a.messages) == 1
