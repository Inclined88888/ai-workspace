"""Claude Code / Codex hand-off with fake CLIs: detection, live events, approvals, diffs, and no token handling."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake CLIs are POSIX scripts")
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def fakes(client, tmp_path, monkeypatch):
    from app import cli_agents

    log = tmp_path / "claude.log"
    monkeypatch.setenv("WORKBENCH_CLAUDE_BIN", str(FIX / "fake_claude"))
    monkeypatch.setenv("WORKBENCH_CODEX_BIN", str(FIX / "fake_codex"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "codex.log"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-app-key-should-not-reach-cli")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-app-key-should-not-reach-cli")
    cli_agents._DETECT_CACHE.clear()
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    (proj / "README.md").write_text("demo\n")
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "add", "."], cwd=proj, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "init"], cwd=proj, check=True)
    return {"log": log, "proj": proj, "tmp": tmp_path}


def wait(client, rid, until=("done", "failed", "needs_approval", "stopped"), timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/cli-agents/runs/{rid}").json()
        if r["status"] in until:
            return r
        time.sleep(0.1)
    raise AssertionError(f"run stuck in {r['status']}")


def test_detects_signed_in_claude_and_codex(client, fakes):
    r = client.get("/api/cli-agents?fresh=true").json()
    by = {a["id"]: a for a in r["agents"]}
    assert by["claude"]["installed"] and by["claude"]["signed_in"] and by["claude"]["auth"] == "subscription"
    assert by["claude"]["version"].startswith("2.1.314")
    assert by["claude"]["install"] == "npm install -g @anthropic-ai/claude-code"
    assert by["codex"]["ready"] and by["codex"]["auth"] == "subscription"
    # nothing identifying or secret comes back
    assert not any(k in json.dumps(r).lower() for k in ("email", "token", "oauth"))


def test_signed_out_and_missing(client, fakes, monkeypatch):
    from app import cli_agents

    monkeypatch.setenv("FAKE_CLAUDE_STATE", "signed_out")
    monkeypatch.setenv("WORKBENCH_CODEX_BIN", str(fakes["tmp"] / "nope"))
    cli_agents._DETECT_CACHE.clear()
    by = {a["id"]: a for a in client.get("/api/cli-agents?fresh=true").json()["agents"]}
    assert by["claude"]["installed"] and not by["claude"]["signed_in"] and "claude" in by["claude"]["login_hint"]
    assert not by["codex"]["installed"]
    r = client.post("/api/cli-agents/runs", json={"agent": "claude", "task": "hi", "folder": str(fakes["proj"])})
    assert r.status_code == 409 and "signed in" in r.json()["detail"]


def test_api_key_sign_in_is_reported(client, fakes, monkeypatch):
    from app import cli_agents

    monkeypatch.setenv("FAKE_CLAUDE_STATE", "api_key")
    cli_agents._DETECT_CACHE.clear()
    c = cli_agents.detect("claude", fresh=True)
    assert c["signed_in"] and c["auth"] == "api_key" and "API key" in c["detail"]


def test_claude_job_asks_then_resumes_with_only_the_approved_steps(client, fakes):
    r = client.post("/api/cli-agents/runs", json={"agent": "claude", "task": "Add hello.txt and test it", "folder": str(fakes["proj"])})
    assert r.status_code == 200, r.text
    run = wait(client, r.json()["id"])
    assert run["status"] == "needs_approval"
    rules = [p["rule"] for p in run["pending"]]
    assert rules == ["Write", "Bash(echo tested)"]
    assert not (fakes["proj"] / "hello.txt").exists()       # nothing changed before approval
    kinds = [e["kind"] for e in run["events"]]
    assert {"user", "start", "thinking", "text", "tool", "tool_result", "result"} <= set(kinds)

    assert client.post(f"/api/cli-agents/runs/{run['id']}/approve", json={}).status_code == 200
    run = wait(client, run["id"], until=("done", "failed"))
    assert run["status"] == "done", run
    assert (fakes["proj"] / "hello.txt").read_text() == "hello from claude\n"
    assert run["files"] == ["hello.txt"] and "+hello from claude" in run["diff"]
    assert run["cost_usd"] == pytest.approx(0.0123) and run["tokens_in"] >= 1200

    calls = [json.loads(line) for line in fakes["log"].read_text().splitlines() if "-p" in line]
    first, second = calls
    assert first["argv"][first["argv"].index("--permission-mode") + 1] == "manual"
    assert "--resume" not in first["argv"] and "--allowedTools" not in first["argv"]
    assert second["argv"][second["argv"].index("--allowedTools") + 1] == "Write,Bash(echo tested)"
    assert "--resume" in second["argv"]
    # the app's own Anthropic key never reaches the CLI: the user's own sign-in is used
    assert not any(c["has_anthropic_key"] for c in calls)
    # and the round shows up in the run log
    from app import db
    rows = db.all("SELECT * FROM actions WHERE channel = 'cli:claude'")
    assert {r["outcome"] for r in rows} >= {"interrupted", "no_claim"}


def test_deny_stops_without_changes(client, fakes):
    rid = client.post("/api/cli-agents/runs", json={"agent": "claude", "task": "x", "folder": str(fakes["proj"])}).json()["id"]
    wait(client, rid)
    assert client.post(f"/api/cli-agents/runs/{rid}/deny").status_code == 200
    run = client.get(f"/api/cli-agents/runs/{rid}").json()
    assert run["status"] == "done" and not run["pending"] and not (fakes["proj"] / "hello.txt").exists()


def test_accept_edits_mode_and_plan_mode(client, fakes):
    rid = client.post("/api/cli-agents/runs", json={"agent": "claude", "task": "x", "folder": str(fakes["proj"]), "mode": "edits"}).json()["id"]
    run = wait(client, rid)
    assert (fakes["proj"] / "hello.txt").exists()
    assert [p["rule"] for p in run["pending"]] == ["Bash(echo tested)"]   # commands still ask
    (fakes["proj"] / "hello.txt").unlink()
    rid = client.post("/api/cli-agents/runs", json={"agent": "claude", "task": "x", "folder": str(fakes["proj"]), "mode": "plan"}).json()["id"]
    wait(client, rid)
    assert not (fakes["proj"] / "hello.txt").exists()
    modes = [c["argv"][c["argv"].index("--permission-mode") + 1] for c in map(json.loads, fakes["log"].read_text().splitlines()) if "-p" in c["argv"]]
    assert modes[-2:] == ["acceptEdits", "plan"]


def test_signed_out_mid_run_is_a_clear_failure(client, fakes, monkeypatch):
    from app import cli_agents

    rid = client.post("/api/cli-agents/runs", json={"agent": "claude", "task": "x", "folder": str(fakes["proj"])}).json()["id"]
    wait(client, rid)
    monkeypatch.setenv("FAKE_CLAUDE_STATE", "signed_out")
    run = cli_agents.RUNS[rid]
    client.post(f"/api/cli-agents/runs/{rid}/approve", json={})
    run = wait(client, rid, until=("done", "failed"))
    assert run["status"] == "failed" and "Not logged in" in run["error"]


def test_codex_read_only_first_then_workspace_write(client, fakes):
    rid = client.post("/api/cli-agents/runs", json={"agent": "codex", "task": "add codex.txt", "folder": str(fakes["proj"])}).json()["id"]
    run = wait(client, rid)
    assert run["status"] == "needs_approval" and run["pending"][0]["rule"] == "codex:edits"
    assert not (fakes["proj"] / "codex.txt").exists()
    client.post(f"/api/cli-agents/runs/{rid}/approve", json={})
    run = wait(client, rid, until=("done", "failed"))
    assert run["status"] == "done" and (fakes["proj"] / "codex.txt").exists() and run["files"] == ["codex.txt"]
    calls = [json.loads(x) for x in (fakes["tmp"] / "codex.log").read_text().splitlines() if '"exec"' in x]
    assert 'sandbox_mode="read-only"' in calls[0]["argv"] and "resume" in calls[1]["argv"]
    assert 'sandbox_mode="workspace-write"' in calls[1]["argv"]
    assert not any(c["has_openai_key"] for c in calls)


def test_folder_must_exist(client, fakes):
    r = client.post("/api/cli-agents/runs", json={"agent": "claude", "task": "x", "folder": str(fakes["tmp"] / "missing")})
    assert r.status_code == 400
    r = client.post("/api/cli-agents/runs", json={"agent": "claude", "task": "x", "folder": "/"})
    assert r.status_code == 400


def test_non_git_folder_diff_comes_from_edit_steps(client, fakes, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    rid = client.post("/api/cli-agents/runs", json={"agent": "claude", "task": "x", "folder": str(plain), "mode": "edits"}).json()["id"]
    run = wait(client, rid)
    assert "+hello from claude" in run["diff"] and run["files"][0].endswith("hello.txt")


def test_subscriptions_list_offers_claude_through_claude_code(client):
    opts = {o["id"]: o for o in client.get("/api/auth/subscriptions").json()["options"]}
    assert opts["claude"]["available"] and opts["claude"]["via"] == "claude_code"
    assert "legal-and-compliance" in opts["claude"]["source"]
