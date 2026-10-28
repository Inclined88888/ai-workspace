"""Coding agents through the companion: detection, the rules before a task runs, the exact command line, progress
parsing and Stop. The agents here are stand-in programs that replay output recorded from the real CLIs (Codex CLI
0.165.0 and Claude Code 2.1.312, see fixtures/); live_coding_agents.py runs the real CLIs."""

import json
import os
import stat
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from rooks_companion import agents  # noqa: E402
from rooks_companion.engine import Engine  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")

# The companion passes the agent only a few environment variables (that is under test), so the stand-ins read their
# settings from fake.json next to them instead
FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
F = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake.json")))
real_env = sorted(os.environ)
os.environ.update({k: str(v) for k, v in F.items() if k.startswith("FAKE_")})
a = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(json.dumps({"argv": a, "env": real_env}) + "\n")
if a == ["--version"]:
    print("codex-cli 0.165.0"); sys.exit(0)
if a[:2] == ["login", "status"]:
    s = os.environ.get("FAKE_LOGIN", "chatgpt")
    print({"chatgpt": "Logged in using ChatGPT", "api_key": "Logged in using an API key - sk-proj-***ABCD", "none": "Not logged in"}[s])
    sys.exit(1 if s == "none" else 0)
if a[0] == "sandbox":
    cmd = a[a.index("--") + 1:]
    if os.environ.get("FAKE_SANDBOX") == "broken":
        subprocess.run(cmd)  # writes anywhere
    else:
        open("inside", "w").write("y")  # only the working folder is writable
    sys.exit(0)
if a[0] == "exec":
    if os.environ.get("FAKE_SLOW"):
        time.sleep(30)
    for line in open(os.environ["FAKE_FIXTURE"]):
        sys.stdout.write(line); sys.stdout.flush()
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
F = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake.json")))
real_env = sorted(os.environ)
os.environ.update({k: str(v) for k, v in F.items() if k.startswith("FAKE_")})
a = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(json.dumps({"argv": a, "env": real_env}) + "\n")
if a == ["--version"]:
    print("2.1.312 (Claude Code)"); sys.exit(0)
for line in open(os.environ["FAKE_FIXTURE"]):
    sys.stdout.write(line)
'''


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    bins = tmp_path / "bin"
    bins.mkdir()
    for name, src in (("codex", FAKE_CODEX), ("claude", FAKE_CLAUDE)):
        p = bins / name
        p.write_text(src)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bins}:{os.environ['PATH']}")
    settings = {"FAKE_LOG": str(log), "FAKE_FIXTURE": os.path.join(FIX, "codex_exec.jsonl")}

    def fake(**kw):
        settings.update(kw)
        (bins / "fake.json").write_text(json.dumps(settings))
    fake()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-pass")
    monkeypatch.setenv("SOME_SECRET", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    cfg = {"folders": [str(work)], "stop_file": str(tmp_path / "STOP"), "coding_codex": True}
    eng = Engine(None, cfg, journal_path=str(home / "journal.json"))
    eng.n = 0

    def act(kind, approved=False, **args):
        eng.n += 1
        return eng.execute({"id": eng.n, "kind": kind, "args": args, "approved": approved})
    eng.act = act

    def calls():
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    eng.fake = fake
    return eng, work, calls, cfg


def wait(eng, run, until=10):
    end = time.time() + until
    while time.time() < end:
        r = eng.act("agent_poll", run=run)
        if r["ok"] and r["result"]["status"] != "running":
            return r["result"]
        time.sleep(0.1)
    raise AssertionError("still running")


def test_detects_both_agents_and_reports_billing_without_reading_tokens(setup):
    eng, _, calls, _ = setup
    assert "coding" in eng.capabilities()
    d = eng.act("agents_detect")["result"]
    assert d["codex"]["installed"] and d["codex"]["sign_in"] == "chatgpt" and d["codex"]["sandbox"] == "enforced" and d["codex"]["ready"]
    assert "ChatGPT plan" in d["codex"]["billing"]
    assert d["claude"]["installed"] and not d["claude"]["ready"] and "never with a Claude subscription" in d["claude"]["reason"]
    # only version, sign-in mode and the sandbox self-test were asked; nothing reads ~/.codex or the Claude credentials
    assert {tuple(c["argv"][:2]) for c in calls()} <= {("--version",), ("login", "status"), ("sandbox", "-c")}


def test_an_api_key_sign_in_is_named_but_the_key_is_not_kept(setup, monkeypatch):
    eng, *_ = setup
    eng.fake(FAKE_LOGIN="api_key")
    d = eng.act("agents_detect")["result"]["codex"]
    assert d["sign_in"] == "api_key" and "OpenAI bills" in d["billing"] and "ABCD" not in json.dumps(d)


def test_not_signed_in_or_unconfined_codex_does_not_run(setup, monkeypatch):
    eng, work, _, _ = setup
    eng.fake(FAKE_LOGIN="none")
    r = eng.act("agent_start", approved=True, agent="codex", task="Add a README", folder=str(work))
    assert r["refused"] and "codex login" in r["error"]
    eng.fake(FAKE_LOGIN="chatgpt")
    eng.fake(FAKE_SANDBOX="broken")
    eng.agents._probe.clear()
    r = eng.act("agent_start", approved=True, agent="codex", task="Add a README", folder=str(work))
    assert r["refused"] and "sandbox did not keep" in r["error"]


def test_a_task_needs_approval_and_an_allowed_folder(setup, tmp_path):
    eng, work, _, _ = setup
    r = eng.act("agent_start", approved=False, agent="codex", task="Add a README", folder=str(work))
    assert r["refused"] and "approve" in r["error"]
    other = tmp_path / "other"
    other.mkdir()
    r = eng.act("agent_start", approved=True, agent="codex", task="Add a README", folder=str(other))
    assert r["refused"] and "outside the folders" in r["error"]


def test_codex_needs_to_be_turned_on_on_this_computer(setup):
    eng, work, calls, cfg = setup
    cfg["coding_codex"] = False
    d = eng.act("agents_detect")["result"]["codex"]
    assert not d["ready"] and "allow-codex on" in d["reason"]
    r = eng.act("agent_start", approved=True, agent="codex", task="Add a README", folder=str(work))
    assert r["refused"] and "allow-codex on" in r["error"]
    assert not any(c["argv"][0] == "exec" for c in calls())


def test_read_only_computer_refuses_a_task(setup):
    eng, work, _, cfg = setup
    cfg["read_only"] = True
    r = eng.act("agent_start", approved=True, agent="codex", task="Add a README", folder=str(work))
    assert r["refused"] and "read only" in r["error"]


def test_codex_runs_sandboxed_with_a_minimal_environment_and_reports_progress(setup):
    eng, work, calls, _ = setup
    r = eng.act("agent_start", approved=True, agent="codex", task="Create hello.txt", folder=str(work))
    assert r["ok"], r
    res = wait(eng, r["result"]["run"])
    assert res["status"] == "done" and res["result"].startswith("I created hello.txt")
    kinds = [e["kind"] for e in res["events"]]
    assert "command" in kinds and "command_done" in kinds and "final" in kinds
    ex = next(c for c in calls() if c["argv"][0] == "exec")
    a = ex["argv"]
    assert a[:6] == ["exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check", "-C"] and a[6] == str(work)
    assert not any("dangerously" in x or "bypass" in x for x in a)
    assert "OPENAI_API_KEY" not in ex["env"] and "SOME_SECRET" not in ex["env"]


def test_a_failed_codex_run_says_why(setup, monkeypatch):
    eng, work, _, _ = setup
    eng.fake(FAKE_EXIT="1")
    r = eng.act("agent_start", approved=True, agent="codex", task="Create hello.txt", folder=str(work))
    res = wait(eng, r["result"]["run"])
    assert res["status"] == "failed" and res["error"]


def test_stop_ends_a_running_task(setup, monkeypatch):
    eng, work, _, _ = setup
    eng.fake(FAKE_SLOW="1")
    run = eng.act("agent_start", approved=True, agent="codex", task="Slow job", folder=str(work))["result"]["run"]
    r = eng.act("agent_start", approved=True, agent="codex", task="Second job", folder=str(work))
    assert r["refused"] and "already running" in r["error"]
    assert eng.act("agent_stop", run=run)["result"]["status"] == "cancelled"
    assert wait(eng, run)["status"] == "cancelled"


def test_claude_code_runs_only_bare_with_an_api_key_you_allowed(setup, monkeypatch):
    eng, work, calls, cfg = setup
    eng.fake(FAKE_FIXTURE=os.path.join(FIX, "claude_stream.jsonl"))
    cfg["claude_api_billing"] = True
    r = eng.act("agent_start", approved=True, agent="claude", task="Create hello.txt", folder=str(work))
    assert r["refused"] and "ANTHROPIC_API_KEY" in r["error"]  # allowed, but no key: a subscription is never the fallback
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    r = eng.act("agent_start", approved=True, agent="claude", task="Create hello.txt", folder=str(work))
    assert r["ok"], r
    res = wait(eng, r["result"]["run"])
    assert res["status"] == "done" and res["result"] == "I created hello.txt."
    assert any(e["kind"] == "cost" for e in res["events"])
    run = [c for c in calls() if c["argv"][0] == "--bare"][-1]
    assert run["argv"][:3] == ["--bare", "-p", "Create hello.txt"] and "--tools=Read,Edit,Write,Glob,Grep" in run["argv"]
    assert "--disallowedTools=Bash" in run["argv"] and not any(x.startswith("--allowedTools") for x in run["argv"])
    assert not any("dangerously" in x or "bypassPermissions" in x for x in run["argv"])
    assert "ANTHROPIC_API_KEY" in run["env"] and "OPENAI_API_KEY" not in run["env"]


def test_parsers_on_recorded_real_output():
    codex = [i for line in open(os.path.join(FIX, "codex_exec.jsonl")) for i in agents.parse_codex(json.loads(line))]
    assert [i["kind"] for i in codex] == ["note", "command", "command_done", "final"]
    claude = [i for line in open(os.path.join(FIX, "claude_stream.jsonl")) for i in agents.parse_claude(json.loads(line))]
    assert [i["kind"] for i in claude] == ["command", "final", "cost"]
    assert agents.codex_sign_in("Not logged in", 1) == "none"
