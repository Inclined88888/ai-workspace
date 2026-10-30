"""rooks -P claude-code / -P codex: the job goes to the user's own signed-in CLI, approvals are asked here, no key leaks."""

import json
import sys
from pathlib import Path

import pytest

from conftest import rooks

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake CLIs are POSIX scripts")
FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def env(tmp_path):
    log = tmp_path / "cli.log"
    return log, {"ROOKS_CLAUDE_BIN": str(FIX / "fake_claude"), "ROOKS_CODEX_BIN": str(FIX / "fake_codex"),
                 "FAKE_CLAUDE_LOG": str(log), "FAKE_CODEX_LOG": str(log), "ANTHROPIC_API_KEY": "sk-should-not-leak",
                 "OPENAI_API_KEY": "sk-should-not-leak"}


def calls(log):
    return [json.loads(x) for x in log.read_text().splitlines() if '"-p"' in x or '"exec"' in x]


def test_claude_asks_then_resumes_with_only_the_approved_steps(project, env):
    log, e = env
    r = rooks(project, "-P", "claude-code", "add hello.txt", stdin="y\n", env=e)
    assert r.returncode == 0, r.stderr
    assert "paused for your approval" in r.stdout and "create" in r.stdout and "run: echo tested" in r.stdout
    assert (project / "hello.txt").read_text() == "hello from claude\n"
    runs = calls(log)
    assert len(runs) == 2
    first, second = runs[0]["argv"], runs[1]["argv"]
    assert first[first.index("--permission-mode") + 1] == "manual" and "--allowedTools" not in first
    assert second[second.index("--allowedTools") + 1] == "Write,Bash(echo tested)" and "--resume" in second
    assert not any(c["has_anthropic_key"] for c in runs)


def test_claude_declined_changes_nothing(project, env):
    log, e = env
    r = rooks(project, "-P", "claude-code", "add hello.txt", stdin="n\n", env=e)
    assert r.returncode == 1
    assert not (project / "hello.txt").exists()
    assert len(calls(log)) == 1


def test_yes_uses_accept_edits_and_dry_run_uses_plan(project, env):
    log, e = env
    rooks(project, "-P", "claude-code", "--yes", "add hello.txt", stdin="n\n", env=e)
    rooks(project, "-P", "claude-code", "--dry-run", "add hello.txt", stdin="n\n", env=e)
    modes = [c["argv"][c["argv"].index("--permission-mode") + 1] for c in calls(log)]
    assert modes == ["acceptEdits", "plan"]


def test_signed_out_and_missing(project, env, tmp_path):
    _, e = env
    r = rooks(project, "-P", "claude-code", "x", env={**e, "FAKE_CLAUDE_STATE": "signed_out"})
    assert r.returncode == 2 and "not signed in" in r.stderr + r.stdout
    r = rooks(project, "-P", "claude-code", "x", env={**e, "ROOKS_CLAUDE_BIN": str(tmp_path / "nope")})
    assert r.returncode == 2 and "npm install -g @anthropic-ai/claude-code" in r.stderr + r.stdout


def test_doctor_reports_plan_sign_in(project, env):
    _, e = env
    r = rooks(project, "doctor", "-C", str(project), "-P", "claude-code", env=e)
    assert r.returncode == 0 and "signed in with your Claude plan" in r.stdout


def test_codex_read_only_then_write_after_yes(project, env):
    log, e = env
    r = rooks(project, "-P", "codex", "add codex.txt", stdin="y\n", env=e)
    assert r.returncode == 0, r.stderr
    runs = calls(log)
    assert 'sandbox_mode="read-only"' in runs[0]["argv"] and 'sandbox_mode="workspace-write"' in runs[1]["argv"]
    assert "resume" in runs[1]["argv"]
    assert (project / "codex.txt").exists()
    assert not any(c["has_openai_key"] for c in runs)
