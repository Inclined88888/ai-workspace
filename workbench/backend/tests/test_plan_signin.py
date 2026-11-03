"""Plan sign-in from the app (plan_signin.py) against fake claude and codex programs that act like the real sign-ins."""

import json
import os
import stat
import sys
import time

import pytest

H = {"X-Workbench": "1"}

FAKE_CLAUDE = r'''#!{py}
import json, os, sys
mark = os.path.join(os.path.dirname(__file__), "claude-signed-in")
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": os.path.exists(mark), "authMethod": "claude.ai", "email": "me@example.com"}}))
elif a[:2] == ["auth", "login"]:
    print("Opening browser to sign in...")
    print("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=abc")
    sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code == "good-code":
        open(mark, "w").close(); print("Login successful"); sys.exit(0)
    print("Invalid code"); sys.exit(1)
'''

FAKE_CODEX = r'''#!{py}
import os, sys, time
mark = os.path.join(os.path.dirname(__file__), "codex-signed-in")
a = sys.argv[1:]
if a == ["login", "status"]:
    if os.path.exists(mark):
        print("Logged in using ChatGPT"); sys.exit(0)
    print("Not logged in"); sys.exit(1)
elif a == ["login", "--device-auth"]:
    print("1. Open this link in your browser and sign in to your account")
    print("   https://auth.openai.com/codex/device")
    print("2. Enter this one-time code (expires in 15 minutes)")
    print("   AB12-CD345", flush=True)
    time.sleep(1.5)
    open(mark, "w").close(); print("Successfully logged in"); sys.exit(0)
'''


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        p = tmp_path / name
        p.write_text(body.format(py=sys.executable))
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("WORKBENCH_CLAUDE_BIN", str(tmp_path / "claude"))
    monkeypatch.setenv("WORKBENCH_CODEX_BIN", str(tmp_path / "codex"))
    yield tmp_path
    from app import db
    db.run("DELETE FROM connections WHERE base_url LIKE 'cli://%'")


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminal sign-in is POSIX; Windows uses pipes")
def test_claude_sign_in_from_the_app_then_chat_on_the_plan(client, fakes):
    st = client.get("/api/plans", headers=H).json()["claude"]
    assert st["installed"] and not st["signed_in"] and not st["connected"]
    assert client.post("/api/plans/claude/connect", headers=H).status_code == 400  # not signed in yet
    s = client.post("/api/plans/claude/signin", headers=H).json()
    assert s["status"] == "waiting" and s["needs_code"] and s["url"].startswith("https://claude.com/cai/oauth/authorize")
    bad = client.post(f"/api/plans/signin/{s['id']}/code", headers=H, json={"code": "wrong"}).json()
    assert bad["status"] == "failed" and not client.get("/api/plans", headers=H).json()["claude"]["signed_in"]
    s = client.post("/api/plans/claude/signin", headers=H).json()
    done = client.post(f"/api/plans/signin/{s['id']}/code", headers=H, json={"code": "good-code"}).json()
    assert done["status"] == "done"
    st = client.get("/api/plans", headers=H).json()["claude"]
    assert st["signed_in"] and st["account"] == "me@example.com"
    from app import db as _db
    earlier = _db.run("INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, residency, created_at) "
                      "VALUES ('Groq', 'api_key', 'chat_completions', 'https://api.groq.com/openai/v1', '', 'cloud', 't')")
    c = client.post("/api/plans/claude/connect", headers=H).json()
    assert c["connected"]
    from app import db
    row = db.one("SELECT * FROM connections WHERE id = ?", c["connection_id"])
    assert row["base_url"] == "cli://claude" and row["enabled"] == 1
    assert client.post("/api/plans/claude/connect", headers=H).json()["connection_id"] == c["connection_id"]  # no duplicate
    from app import router
    ids = [x["id"] for x in router.ordered_connections()]
    assert ids[0] == c["connection_id"] and earlier in ids  # chosen on purpose: ahead of the key added earlier
    _db.run("DELETE FROM connections WHERE id = ?", earlier)
    router.save_settings({"order": []})


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminal sign-in is POSIX; Windows uses pipes")
def test_codex_device_sign_in_shows_the_code_and_finishes_by_itself(client, fakes):
    s = client.post("/api/plans/codex/signin", headers=H).json()
    assert s["url"] == "https://auth.openai.com/codex/device" and s["code"] == "AB12-CD345" and not s["needs_code"]
    for _ in range(60):
        s = client.get(f"/api/plans/signin/{s['id']}", headers=H).json()
        if s["status"] != "waiting":
            break
        time.sleep(0.1)
    assert s["status"] == "done"
    assert client.get("/api/plans", headers=H).json()["codex"]["signed_in"]


def test_missing_program_and_unknown_plan(client, monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_CLAUDE_BIN", str(tmp_path / "nope"))
    assert client.get("/api/plans", headers=H).json()["claude"]["installed"] is False
    assert client.post("/api/plans/claude/signin", headers=H).status_code == 400
    assert client.post("/api/plans/gemini/signin", headers=H).status_code == 404


def test_plans_need_a_session(client):
    from starlette.testclient import TestClient

    assert TestClient(client.app).get("/api/plans").status_code == 401


def test_new_workspace_is_sent_to_the_welcome(client):
    from app import db

    before = db.get_setting("desktop_welcome_done")
    db.set_setting("desktop_welcome_done", "0")
    try:
        assert client.get("/api/status").json()["welcome_pending"] is True
        client.post("/api/desktop/welcome-done", headers=H)
        s = client.get("/api/status").json()
        assert s["welcome_pending"] is False and s["welcome_done"] is True
    finally:
        db.set_setting("desktop_welcome_done", before or "")


FAKE_COPILOT = r'''#!{py}
import os, sys, time
a = sys.argv[1:]
if a[:2] == ["login", "--device-code"]:
    print("To authenticate, visit https://github.com/login/device and enter code 2C0E-7364")  # the real CLI's words
    print("Waiting for authorization...", flush=True)
    time.sleep(1.2)
    print("Signed in successfully as octocat"); sys.exit(0)
if "-p" in a:
    prompt = a[a.index("-p") + 1]
    assert "--available-tools=" in a and "--disable-builtin-mcps" in a and "-s" in a, a  # no tools, answer only
    if "quota" in prompt:
        sys.stderr.write("\nYou have exceeded your monthly quota (Request ID: X)\n"); sys.exit(1)
    print("Copilot says: " + prompt.splitlines()[-1]); sys.exit(0)
'''


@pytest.fixture
def copilot(tmp_path, monkeypatch):
    p = tmp_path / "copilot"
    p.write_text(FAKE_COPILOT.format(py=sys.executable))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("WORKBENCH_COPILOT_BIN", str(p))
    monkeypatch.setenv("PATH", str(tmp_path))  # no gh: signed in only once the sign-in here finishes
    from app import cli_agents
    monkeypatch.setattr(cli_agents, "_extra_dirs", lambda: [])  # nor the real one in /opt/homebrew/bin
    for k in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from app import db
    db.set_setting("copilot_signed_in", "")
    yield p
    db.run("DELETE FROM connections WHERE base_url LIKE 'cli://%'")
    db.set_setting("copilot_signed_in", "")


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminal sign-in is POSIX; Windows uses pipes")
def test_copilot_device_sign_in_then_answers_without_tools_and_explains_a_used_up_quota(client, copilot):
    import asyncio

    from app import adapters, db

    st = client.get("/api/plans", headers=H).json()["copilot"]
    assert st["installed"] and not st["signed_in"] and st["name"] == "GitHub Copilot"
    s = client.post("/api/plans/copilot/signin", headers=H).json()
    assert s["url"] == "https://github.com/login/device" and s["code"] == "2C0E-7364" and not s["needs_code"]
    for _ in range(60):
        s = client.get(f"/api/plans/signin/{s['id']}", headers=H).json()
        if s["status"] != "waiting":
            break
        time.sleep(0.1)
    assert s["status"] == "done" and client.get("/api/plans", headers=H).json()["copilot"]["signed_in"]
    cid = client.post("/api/plans/copilot/connect", headers=H).json()["connection_id"]
    conn = db.one("SELECT * FROM connections WHERE id = ?", cid)
    assert conn["base_url"] == "cli://copilot"

    plan = adapters.CliPlan()
    out = asyncio.run(plan.chat(conn, "", "default", [{"role": "user", "content": "hello there"}], None))
    assert out["message"]["content"] == "Copilot says: hello there"
    with pytest.raises(adapters.ProviderError) as e:
        asyncio.run(plan.chat(conn, "", "default", [{"role": "user", "content": "quota please"}], None))
    assert e.value.upstream == 429 and "premium requests for this month are used up" in str(e.value.detail)


def test_copilot_is_a_chat_model_not_a_coding_agent(client):
    from app import cli_agents

    assert "copilot" in cli_agents.AGENTS and "copilot" not in cli_agents.CODING_AGENTS
    ids = [a["id"] for a in client.get("/api/cli-agents", headers=H).json().get("agents", [])] if client.get("/api/cli-agents", headers=H).status_code == 200 else []
    assert "copilot" not in ids
    subs = {s["id"]: s for s in client.get("/api/auth/subscriptions", headers=H).json()["options"]}
    assert subs["copilot"]["available"]


def test_thinking_level_reaches_each_plan_cli_and_only_known_levels_pass(client):
    from app.adapters import EFFORT, CliPlan

    p = CliPlan()
    tok = EFFORT.set("high")
    try:
        claude = p.argv({"base_url": "cli://claude"}, "claude", "opus", "")
        codex = p.argv({"base_url": "cli://codex"}, "codex", "default", "")
        copilot = p.argv({"base_url": "cli://copilot"}, "copilot", "default", "")
    finally:
        EFFORT.reset(tok)
    assert claude[claude.index("--effort") + 1] == "high" and claude[-2:] == ["--model", "opus"]
    assert 'model_reasoning_effort="high"' in codex and codex[-1] == "-"
    assert copilot[copilot.index("--reasoning-effort") + 1] == "high"
    tok = EFFORT.set("rm -rf /")
    try:
        assert "--effort" not in p.argv({"base_url": "cli://claude"}, "claude", "", "")
    finally:
        EFFORT.reset(tok)
    assert "--effort" not in p.argv({"base_url": "cli://claude"}, "claude", "", "")  # default: the plan's own


def test_v1_models_tells_a_picker_what_to_show(client):
    from app import bridge, db

    cid = db.run("INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, residency, created_at) "
                 "VALUES ('Claude plan (Claude Code)', 'cli_subscription', 'cli_plan', 'cli://claude', '', 'cloud', 't')")
    try:
        rows = {r["id"]: r for r in bridge.models()["data"]}
        sonnet = rows[f"{cid}/sonnet"]
        assert sonnet["owned_by"] == "Claude plan (Claude Code)" and sonnet["effort"] and sonnet["pick"] and sonnet["label"] == "sonnet"
    finally:
        db.run("DELETE FROM connections WHERE id = ?", cid)


def test_chat_takes_a_thinking_level_for_plans_and_the_picker_knows_which_models_take_one(client):
    from app import db

    assert client.post("/api/assistant/chat", headers=H, json={"message": "hi", "effort": "extreme"}).status_code == 422
    cid = db.run("INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, residency, created_at) "
                 "VALUES ('Claude plan (Claude Code)', 'cli_subscription', 'cli_plan', 'cli://claude', '', 'cloud', 't')")
    try:
        rows = {m["ref"]: m for m in client.get("/api/models", headers=H).json()["models"]}
        assert rows[f"{cid}/opus"]["effort"] is True
        assert all(not m["effort"] for r, m in rows.items() if not r.startswith(f"{cid}/"))
    finally:
        db.run("DELETE FROM connections WHERE id = ?", cid)


def test_a_plan_answering_in_claudes_own_tool_format_still_calls_the_tools():
    from app.adapters import parse_text_tool_calls

    calls, rest = parse_text_tool_calls('<invoke name="web_search">\n<parameter name="query">weather sydney</parameter>\n</invoke>',
                                        {"web_search"})
    assert calls[0]["function"]["name"] == "web_search" and json.loads(calls[0]["function"]["arguments"]) == {"query": "weather sydney"}
    assert rest == ""
    assert parse_text_tool_calls('<invoke name="delete_everything"><parameter name="x">1</parameter></invoke>', {"web_search"})[0] == []


def test_claude_plan_is_asked_for_and_shown_its_own_tool_format():
    from app.adapters import CliPlan, parse_text_tool_calls

    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "type": "function", "function": {"name": "todo", "arguments": json.dumps({"items": [{"text": "x", "status": "doing"}]})}}]},
            {"role": "tool", "tool_call_id": "a", "content": "ok"}]
    tools = [{"type": "function", "function": {"name": "todo", "description": "d", "parameters": {}}}]
    system, prompt = CliPlan.transcript(msgs, tools, xml=True)
    assert "<invoke name=\"TOOL\">" in system and "<tool_call>" not in prompt
    calls, _ = parse_text_tool_calls(prompt.split("ASSISTANT:\n", 1)[1].split("TOOL RESULT")[0], {"todo"})
    assert json.loads(calls[0]["function"]["arguments"]) == {"items": [{"text": "x", "status": "doing"}]}  # round-trips


FAKE_STREAMING_CLAUDE = r'''#!{py}
import json, sys
a = sys.argv[1:]
sys.stdin.read()
if "stream-json" not in a:
    print(json.dumps({{"type": "result", "result": "whole answer", "usage": {{"input_tokens": 3, "output_tokens": 2}}}}))
    sys.exit(0)
text = 'Reading the file first.\n<function_calls>\n<invoke name="read_file">\n<parameter name="path">a.py</parameter>\n</invoke>\n</function_calls>'
for piece in ("Reading the ", "file first.\n<func", "tion_calls>\n<invoke name=\"read_file\">", "\n<parameter name=\"path\">a.py</parameter>\n</invoke>\n</function_calls>"):
    print(json.dumps({{"type": "stream_event", "event": {{"type": "content_block_delta", "index": 0, "delta": {{"type": "text_delta", "text": piece}}}}}}), flush=True)
print(json.dumps({{"type": "result", "result": text, "usage": {{"input_tokens": 5, "output_tokens": 9}}}}))
'''


def test_a_plan_turn_with_tools_streams_its_prose_through_v1_and_hides_the_call_markup(client, tmp_path, monkeypatch):
    from app import config, db

    fake = tmp_path / "claude"
    fake.write_text(FAKE_STREAMING_CLAUDE.format(py=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("WORKBENCH_CLAUDE_BIN", str(fake))
    monkeypatch.setattr(config, "BROWSER_API_KEY", "k" * 32)
    cid = db.run("INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, residency, created_at) "
                 "VALUES ('Claude plan (Claude Code)', 'cli_subscription', 'cli_plan', 'cli://claude', '', 'cloud', 't')")
    try:
        body = {"model": f"{cid}/opus", "stream": True, "messages": [{"role": "user", "content": "fix a.py"}],
                "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]}
        r = client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer " + "k" * 32})
        chunks = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
        text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c.get("choices"))
        calls = [tc for c in chunks if c.get("choices") for tc in c["choices"][0]["delta"].get("tool_calls") or []]
        assert text.startswith("Reading the file first.") and "<" not in text
        texts = [c["choices"][0]["delta"].get("content") for c in chunks if c.get("choices") and c["choices"][0]["delta"].get("content")]
        assert len(texts) >= 2  # it arrived in pieces, not as one block at the end
        assert calls[0]["function"]["name"] == "read_file" and json.loads(calls[0]["function"]["arguments"]) == {"path": "a.py"}
        assert chunks[-2]["choices"][0]["finish_reason"] == "tool_calls" if chunks[-2].get("choices") else True
    finally:
        db.run("DELETE FROM connections WHERE id = ?", cid)


def test_claude_habit_tool_names_and_unclosed_xml_still_become_calls():
    from app.adapters import parse_text_tool_calls

    calls, left = parse_text_tool_calls('Checking.\n<function_calls>\n<invoke name="Read">\n<parameter name="file_path">a.md</parameter>\n'
                                        '</invoke>\n<invoke name="Bash"><parameter name="command">ls', {"read_file", "run_command"})
    assert [c["function"]["name"] for c in calls] == ["read_file", "run_command"] and left == "Checking."
    assert json.loads(calls[1]["function"]["arguments"]) == {"command": "ls"}
