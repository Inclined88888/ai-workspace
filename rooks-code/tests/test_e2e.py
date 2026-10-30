"""End-to-end: the real CLI process against a scripted fake model over real HTTP."""

import json
import sys
from pathlib import Path

import pytest

from conftest import rooks
from fakes import FakeModel, anthropic_reply, openai_reply

FIX = [
    openai_reply("Looking around.", [("list_files", {"path": "."})]),
    openai_reply("", [("read_file", {"path": "calc.py"})]),
    openai_reply("", [("edit_file", {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"})]),
    openai_reply("", [("run_command", {"command": f'"{sys.executable}" test_calc.py'})]),
    openai_reply("Fixed add(): it subtracted. test_calc.py now passes.", tokens=(300, 15)),
]


def receipt(project):
    files = sorted((project / ".rooks" / "runs").glob("*.jsonl"))
    assert len(files) == 1
    rows = {}
    for line in files[0].read_text().splitlines():
        r = json.loads(line)
        rows[r["id"]] = r
    return list(rows.values())


@pytest.fixture
def fake():
    servers = []

    def make(script):
        servers.append(FakeModel(script))
        return servers[-1]
    yield make
    for s in servers:
        s.close()


def run(project, fake_model, *extra, stdin=""):
    return rooks(project, "-P", "openai-compatible", "--base-url", fake_model.url, "-m", "fake-model", *extra,
                 "fix the failing test", stdin=stdin)


def test_fixes_a_bug_with_approval_diff_command_and_receipt(project, fake):
    m = fake(FIX)
    r = run(project, m, stdin="y\ny\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (project / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    out = r.stdout
    assert "-    return a - b" in out and "+    return a + b" in out          # the diff was shown
    assert "Apply this change? [y]es / [n]o / [a]lways this session: y" in out
    assert "Run this command?" in out and "tests pass" in out
    assert "Fixed add()" in out
    # the model saw every tool result, in order
    last = m.requests[-1]["body"]["messages"]
    tool_results = [x["content"] for x in last if x["role"] == "tool"]
    assert "calc.py" in tool_results[0] and "return a - b" in tool_results[1]
    assert tool_results[2].startswith("edited calc.py (+1 -1)") and tool_results[3].startswith("exit code 0")
    assert {t["function"]["name"] for t in m.requests[0]["body"]["tools"]} >= {"read_file", "edit_file", "run_command", "search"}
    rows = receipt(project)
    kinds = [(r["kind"], r["outcome"]) for r in rows]
    assert ("turn", "backed") in kinds
    assert [o for k, o in kinds if k == "tool_call"] == ["ok", "ok", "verified", "ok"]
    assert [o for k, o in kinds if k == "decision"] == ["approved", "approved"]
    assert sum(k == "model_call" for k, _ in kinds) == 5
    egress = [r for r in rows if r["kind"] == "egress"]
    assert len(egress) == 5 and all(e["effect"] == "local" and e["args"].endswith("/v1/chat/completions") for e in egress)
    edit = next(r for r in rows if r["tool"] == "edit_file")
    assert edit["verified"] is True and "sha256" in edit["evidence"]
    turn = next(r for r in rows if r["kind"] == "turn")
    assert turn["tokens_in"] == 700 and turn["tokens_out"] == 95 and turn["usd_cost"] is None  # unknown price: not guessed
    assert all(r["channel"] == "rooks-code" for r in rows)


def test_denied_edit_is_not_applied_and_model_is_told(project, fake):
    m = fake([FIX[2], openai_reply("OK, I will leave it.")])
    r = run(project, m, stdin="n\n")
    assert (project / "calc.py").read_text().endswith("a - b\n")
    assert "denied" in m.requests[1]["body"]["messages"][-1]["content"]
    assert [x["outcome"] for x in receipt(project) if x["kind"] in ("decision", "tool_call")] == ["denied", "denied"]
    assert r.returncode == 0


def test_no_input_means_denied(project, fake):
    m = fake([FIX[3], openai_reply("Could not run it.")])
    r = run(project, m, stdin="")
    assert "no input: denied" in r.stdout and "tests pass" not in r.stdout


def test_dry_run_changes_nothing(project, fake):
    m = fake([FIX[2], FIX[3], openai_reply("Would fix it.")])
    r = run(project, m, "--dry-run")
    assert (project / "calc.py").read_text().endswith("a - b\n")
    assert "+    return a + b" in r.stdout and "dry run: not applied" in r.stdout and "tests pass" not in r.stdout
    assert [x["outcome"] for x in receipt(project) if x["kind"] == "tool_call"] == ["dry_run", "dry_run"]


def test_undo_restores_edits_and_removes_new_files(project, fake):
    m = fake([openai_reply("", [("edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a * b"}),
                                ("write_file", {"path": "notes/new.txt", "content": "hello\n"})]),
              openai_reply("Done.")])
    r = run(project, m, "--yes")
    assert r.returncode == 0, r.stderr
    assert (project / "notes/new.txt").exists() and "a * b" in (project / "calc.py").read_text()
    u = rooks(project, "undo", "-C", str(project))
    assert "restored calc.py" in u.stdout and "notes/new.txt (removed" in u.stdout
    assert (project / "calc.py").read_text().endswith("a - b\n") and not (project / "notes/new.txt").exists()
    assert "nothing to undo" in rooks(project, "undo", "-C", str(project)).stdout


def test_token_hard_cap_stops_before_the_next_call(project, fake):
    m = fake([openai_reply("", [("list_files", {})], tokens=(900, 50)), openai_reply("never sent")])
    r = run(project, m, "--max-tokens", "1000")
    assert r.returncode == 1 and "token cap reached" in r.stderr
    assert len(m.requests) == 1
    assert next(x for x in receipt(project) if x["kind"] == "turn")["outcome"] == "budget_paused"


def test_dollar_cap_with_known_price(project, fake):
    m = fake([openai_reply("", [("list_files", {})], tokens=(100_000, 1_000)), openai_reply("never sent")])
    r = rooks(project, "-P", "openai-compatible", "--base-url", m.url, "-m", "gpt-4.1", "--max-usd", "0.2", "go")
    assert "cost cap reached: $0.2080 spent" in r.stderr and len(m.requests) == 1


def test_trusted_folder_runs_without_asking(project, fake, tmp_path):
    (tmp_path / "rooks.toml").write_text(f"trusted_folders = [{json.dumps(str(project))}]\n")
    m = fake([FIX[2], openai_reply("Done.")])
    r = run(project, m)
    assert "trusted folder" in r.stdout and "Apply this change?" not in r.stdout
    assert (project / "calc.py").read_text().endswith("a + b\n")


def test_paths_outside_the_project_are_refused(project, fake):
    m = fake([openai_reply("", [("read_file", {"path": "../rooks.toml"}), ("write_file", {"path": "/tmp/x", "content": ""})]),
              openai_reply("ok")])
    run(project, m, "--yes")
    results = [x["content"] for x in m.requests[1]["body"]["messages"] if x["role"] == "tool"]
    assert all("outside the project folder" in x for x in results)


def test_bad_tool_arguments_go_back_to_the_model(project, fake):
    m = fake([openai_reply("", [("edit_file", "{not json"), ("nope", {})]), openai_reply("ok")])
    run(project, m)
    results = [x["content"] for x in m.requests[1]["body"]["messages"] if x["role"] == "tool"]
    assert "not valid JSON" in results[0] and "unknown tool" in results[1]


def test_step_limit(project, fake):
    m = fake([openai_reply("", [("list_files", {})])] * 5)
    r = run(project, m, "--max-steps", "3")
    assert r.returncode == 1 and "Stopped after 3 model calls" in r.stderr and len(m.requests) == 3


def test_missing_api_key_is_a_clear_error(project):
    r = rooks(project, "-P", "openai", "hi", env={"OPENAI_API_KEY": ""})
    assert r.returncode == 2 and "OPENAI_API_KEY" in r.stderr


def test_interactive_repl_with_slash_commands(project, fake):
    m = fake([FIX[2], openai_reply("Fixed."), openai_reply("Hello again.")])
    r = rooks(project, "-P", "openai-compatible", "--base-url", m.url, "-m", "fake-model",
              stdin="fix it\ny\n/cost\n/undo\nhi\n/exit\n")
    assert r.returncode == 0, r.stderr
    assert "Fixed." in r.stdout and "Hello again." in r.stdout and "restored calc.py" in r.stdout
    assert "40 out tokens" in r.stdout
    assert (project / "calc.py").read_text().endswith("a - b\n")
    # the second prompt carries the whole conversation
    assert [x["content"] for x in m.requests[2]["body"]["messages"] if x["role"] == "user"] == ["fix it", "hi"]


def test_anthropic_protocol(project, fake):
    m = fake([anthropic_reply("Reading.", [("read_file", {"path": "calc.py"})]), anthropic_reply("It subtracts.")])
    r = rooks(project, "-P", "anthropic", "-m", "claude-sonnet-4-5", "--base-url", m.url, "what does add do?",
              env={"ANTHROPIC_API_KEY": "test-key"})
    assert r.returncode == 0, r.stderr
    assert "It subtracts." in r.stdout
    first, second = m.requests
    assert first["path"] == "/v1/messages" and first["headers"]["x-api-key"] == "test-key"
    assert first["body"]["tools"][0]["input_schema"]["type"] == "object"
    msgs = second["body"]["messages"]
    assert [x["role"] for x in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"][1]["type"] == "tool_use" and msgs[2]["content"][0]["type"] == "tool_result"
    assert "return a - b" in msgs[2]["content"][0]["content"]
    turn = next(x for x in receipt(project) if x["kind"] == "turn")
    assert turn["usd_cost"] == round((200 * 3 + 40 * 15) / 1e6, 6)


def test_mcp_stdio_server_tools(project, fake, tmp_path):
    server = Path(__file__).with_name("fake_mcp_server.py")
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"fake": {"command": sys.executable, "args": [str(server)]}}}))
    m = fake([openai_reply("", [("mcp__fake__shout", {"text": "hello mcp"})]), openai_reply("Done.")])
    r = run(project, m, stdin="y\n")
    assert r.returncode == 0, r.stderr
    assert "mcp__fake__shout" in {t["function"]["name"] for t in m.requests[0]["body"]["tools"]}
    assert "Call this tool?" in r.stdout
    assert m.requests[1]["body"]["messages"][-1]["content"] == "HELLO MCP"
    listing = rooks(project, "mcp", "-C", str(project))
    assert "fake: 1 tools" in listing.stdout and "mcp__fake__shout" in listing.stdout


def test_tool_call_printed_as_text_by_a_local_model(project, fake):
    m = fake([openai_reply('I will read it.\n```json\n{"name": "read_file", "arguments": {"path": "calc.py"}}\n```'),
              openai_reply("It subtracts.")])
    r = run(project, m)
    assert r.returncode == 0 and "It subtracts." in r.stdout
    assert "return a - b" in m.requests[1]["body"]["messages"][-1]["content"]
    call = next(x for x in receipt(project) if x["kind"] == "tool_call")
    assert call["from_text"] == 1 and call["outcome"] == "ok"


def test_a_reply_that_announces_a_step_is_nudged_once_more(project, fake):
    m = fake([openai_reply("Now I'll read calc.py."), openai_reply("", [("read_file", {"path": "calc.py"})]),
              openai_reply("It subtracts.")])
    r = run(project, m)
    assert r.returncode == 0 and "It subtracts." in r.stdout and len(m.requests) == 3
    assert "did not call a tool" in m.requests[1]["body"]["messages"][-1]["content"]
    turn = next(x for x in receipt(project) if x["kind"] == "turn")
    assert "nudged 1x" in turn["detail"]


def test_system_prompt_lists_the_project_root(project, fake):
    m = fake([openai_reply("ok")])
    run(project, m)
    system = m.requests[0]["body"]["messages"][0]["content"]
    assert "calc.py, test_calc.py" in system and ".rooks" not in system
