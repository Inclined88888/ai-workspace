"""Delegate providers: `rooks -P claude-code` and `rooks -P codex` hand the job to the user's own Claude Code or Codex CLI.

Rooks does not sign anyone in and never reads or passes on a token. It finds the `claude` / `codex` program the user
installed and signed in to themselves (with their Claude or ChatGPT plan, or their own key), runs it in this folder and
prints what it does. Anthropic allows a person to use their plan in the unmodified Claude Code binary; it does not allow
other apps to route requests through plan credentials (https://code.claude.com/docs/en/legal-and-compliance), so this is
the only way Rooks uses a Claude plan.

Approvals work the Rooks way: Claude Code runs in its Manual permission mode (Accept edits with --yes, Plan with
--dry-run), never with a bypass flag. Steps it was not allowed to take come back as permission denials; Rooks shows each
one and asks, then resumes the same session with exactly the approved steps allowed (--allowedTools). Codex runs in its
read-only sandbox and, after you say yes, continues in workspace-write.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

AGENTS = {
    "claude-code": {
        "name": "Claude Code", "bin": "claude", "env": "ROOKS_CLAUDE_BIN",
        "install": "npm install -g @anthropic-ai/claude-code",
        "login": "Run `claude` once and choose \"Claude account with subscription\" (Anthropic's sign-in opens in your browser).",
        "strip": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"),
    },
    "codex": {
        "name": "Codex", "bin": "codex", "env": "ROOKS_CODEX_BIN",
        "install": "npm install -g @openai/codex",
        "login": "Run `codex login` and choose Sign in with ChatGPT.",
        "strip": ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"),
    },
}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def find(provider: str) -> str | None:
    a = AGENTS[provider]
    override = os.environ.get(a["env"])
    if override:
        return override if os.path.isfile(override) else None
    extra = [os.path.expanduser("~/.local/bin"), os.path.expanduser("~/.claude/local"), os.path.expanduser("~/.npm-global/bin")]
    return shutil.which(a["bin"]) or shutil.which(a["bin"], path=os.pathsep.join(extra))


def env_for(provider: str) -> dict[str, str]:
    """The child gets the user's environment minus API keys Rooks or the app may hold, so the CLI uses its own sign-in."""
    e = dict(os.environ)
    for k in AGENTS[provider]["strip"]:
        e.pop(k, None)
    return e


def status(provider: str) -> dict:
    """{installed, path, signed_in, auth, detail}. Reads only the CLI's own status output, never its credential files."""
    path = find(provider)
    a = AGENTS[provider]
    if not path:
        return {"installed": False, "path": None, "signed_in": False, "auth": "",
                "detail": f"{a['name']} isn't installed. Install it with: {a['install']}"}
    argv = [path, "auth", "status"] if provider == "claude-code" else [path, "login", "status"]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20, env=env_for(provider), stdin=subprocess.DEVNULL)
        out = (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"installed": True, "path": path, "signed_in": False, "auth": "", "detail": f"could not check sign-in: {e}"}
    if provider == "claude-code":
        try:
            d = json.loads(p.stdout or "{}")
            signed, auth = bool(d.get("loggedIn")), str(d.get("authMethod") or "")
        except ValueError:
            signed, auth = p.returncode == 0 and "not logged in" not in out.lower(), ""
        kind = "your Claude plan" if auth == "claude.ai" else (auth or "")
    else:
        low = out.lower()
        signed = p.returncode == 0 and "logged in" in low and "not logged in" not in low
        kind = "your ChatGPT plan" if "chatgpt" in low else ("an API key" if "api key" in low else "")
    detail = f"signed in{' with ' + kind if kind else ''}" if signed else f"not signed in. {a['login']}"
    return {"installed": True, "path": path, "signed_in": signed, "auth": kind, "detail": detail}


def _manual(path: str) -> str:
    try:
        h = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
        return "manual" if '"manual"' in (h.stdout + h.stderr) else "default"
    except (OSError, subprocess.TimeoutExpired):
        return "default"


def _describe(tool: str, inp: dict) -> str:
    if tool == "Bash":
        return f"run: {inp.get('command', '')}"
    if tool in EDIT_TOOLS:
        return f"{'create' if tool == 'Write' else 'edit'} {inp.get('file_path') or inp.get('notebook_path') or 'a file'}"
    return f"use {tool}"


def _rule(tool: str, inp: dict) -> str:
    return f"Bash({inp['command']})" if tool == "Bash" and inp.get("command") else tool


class Session:
    """One delegated conversation; follow-up prompts resume the same CLI session."""

    def __init__(self, provider: str, s, ui, path: str):
        self.provider, self.s, self.ui, self.path = provider, s, ui, path
        self.session_id = ""
        self.allowed: list[str] = []
        self.codex_write = False
        self.mode = "plan" if s.dry_run else ("acceptEdits" if s.auto_approve or s.trusted() else "ask")
        self.claude_mode = ""
        self.last_text = ""

    def argv(self, resume: bool) -> list[str]:
        if self.provider == "claude-code":
            mode = self.mode
            if mode == "ask":
                self.claude_mode = self.claude_mode or _manual(self.path)
                mode = self.claude_mode
            a = [self.path, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", mode]
            if self.s.model and self.s.model != "default":
                a += ["--model", self.s.model]
            if self.allowed:
                a += ["--allowedTools", ",".join(self.allowed)]
            if resume and self.session_id:
                a += ["--resume", self.session_id]
            return a
        sandbox = "workspace-write" if (self.mode == "acceptEdits" or self.codex_write) else "read-only"
        common = ["--json", "--skip-git-repo-check", "-c", f'sandbox_mode="{sandbox}"']
        if self.s.model and self.s.model != "default":
            common += ["-m", self.s.model]
        if resume and self.session_id:
            return [self.path, "exec", "resume", *common, self.session_id, "-"]
        return [self.path, "exec", *common, "--color", "never", "--sandbox", sandbox, "-"]

    def _round(self, prompt: str, resume: bool) -> dict:
        """Run the CLI once and print its stream. Returns {"ok", "result", "denials"}."""
        out = {"ok": True, "result": "", "denials": [], "error": ""}
        try:
            p = subprocess.Popen(self.argv(resume), cwd=str(self.s.root), env=env_for(self.provider), stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        except OSError as e:
            self.ui.error(f"could not start {AGENTS[self.provider]['name']}: {e}")
            return {**out, "ok": False, "error": str(e)}
        assert p.stdin and p.stdout and p.stderr
        p.stdin.write(prompt)
        p.stdin.close()
        try:
            for line in p.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                (self._claude if self.provider == "claude-code" else self._codex)(ev, out)
        except KeyboardInterrupt:
            p.terminate()
            self.ui.warn("stopped")
            out.update(ok=False, error="stopped")
        err = p.stderr.read()
        code = p.wait()
        if code != 0 and out["ok"] and not out["result"]:
            out.update(ok=False, error=(err.strip().splitlines() or [f"exit code {code}"])[-1][:500])
        if not out["ok"] and out["error"] and out["error"] != "stopped":
            self.ui.error(out["error"])
        return out

    def _claude(self, ev: dict, out: dict) -> None:
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            self.session_id = ev.get("session_id") or self.session_id
        elif t == "assistant":
            for part in (ev.get("message") or {}).get("content") or []:
                if part.get("type") == "text" and part.get("text", "").strip():
                    self.last_text = part["text"].strip()
                    self.ui.assistant(self.last_text, final=False)
                elif part.get("type") == "tool_use":
                    name, inp = part.get("name", ""), part.get("input") or {}
                    self.ui.tool_note(_describe(name, inp), read=name in ("Read", "Glob", "Grep", "LS"))
        elif t == "result":
            self.session_id = ev.get("session_id") or self.session_id
            out["result"] = ev.get("result") or ""
            if ev.get("is_error"):
                out.update(ok=False, error=out["result"] or ev.get("subtype", "error"))
            seen: set[str] = set()
            for d in ev.get("permission_denials") or []:
                name, inp = d.get("tool_name", ""), d.get("tool_input") or {}
                r = _rule(name, inp)
                if r not in seen and r not in self.allowed:
                    seen.add(r)
                    out["denials"].append({"rule": r, "label": _describe(name, inp), "tool": name, "input": inp})

    def _codex(self, ev: dict, out: dict) -> None:
        t = ev.get("type", "")
        it = ev.get("item") or {}
        if t == "thread.started":
            self.session_id = ev.get("thread_id") or self.session_id
        elif t == "item.started" and it.get("type") == "command_execution":
            self.ui.tool_note(f"run: {it.get('command', '')}")
        elif t == "item.completed" and it.get("type") == "file_change":
            self.ui.tool_note("change " + ", ".join(str(c.get("path")) for c in it.get("changes") or []))
        elif t == "item.completed" and it.get("type") == "agent_message":
            out["result"] = it.get("text", "")
        elif t == "turn.completed":
            out.update(ok=True, error="")
        elif t == "turn.failed":
            e = ev.get("error")
            out.update(ok=False, error=(e.get("message") if isinstance(e, dict) else ev.get("message")) or "Codex stopped with an error")
        elif t == "error":
            out.update(ok=False, error=ev.get("message") or "Codex reported an error")

    def _yes(self, question: str) -> bool:
        self.ui.out.write(self.ui.c("33", question) + " [y/N] ")
        self.ui.out.flush()
        if sys.stdin.isatty():
            ans = input()
        else:
            ans = sys.stdin.readline()
            self.ui.p(ans.strip())
        return ans.strip().lower() in ("y", "yes")

    def run(self, prompt: str) -> bool:
        res = self._round(prompt, resume=bool(self.session_id))
        while res["ok"]:
            if self.provider == "claude-code" and res["denials"] and self.mode == "ask":
                self.ui.p(self.ui.c("33", f"{AGENTS[self.provider]['name']} paused for your approval:"))
                for d in res["denials"]:
                    self.ui.p(f"  - {d['label']}")
                    if d["tool"] in EDIT_TOOLS and d["input"].get("new_string") is not None:
                        self.ui.p(self.ui.c("2", f"      - {str(d['input'].get('old_string', ''))[:200]}\n      + {str(d['input']['new_string'])[:200]}"))
                    elif d["tool"] == "Write":
                        self.ui.p(self.ui.c("2", "      " + str(d["input"].get("content", ""))[:300].rstrip().replace("\n", "\n      ")))
                if not self._yes("Allow these steps and continue?"):
                    self.ui.p("Not allowed. Nothing else ran.")
                    return False
                self.allowed += [d["rule"] for d in res["denials"]]
                steps = "; ".join(d["label"] for d in res["denials"])
                res = self._round(f"I approved these steps: {steps}. Please go ahead and finish the task.", resume=True)
                continue
            if self.provider == "codex" and self.mode == "ask" and not self.codex_write and res["result"]:
                if self._yes("Codex worked read-only. Let it edit files in this folder and continue?"):
                    self.codex_write = True
                    res = self._round("You may now edit files in this folder. Go ahead and make the changes.", resume=True)
                    continue
            break
        if res["ok"] and res["result"] and res["result"].strip() != self.last_text:
            self.ui.assistant(res["result"].strip(), final=True)
        return res["ok"]


def main(provider: str, s, ui, prompt: str | None) -> int:
    a = AGENTS[provider]
    st = status(provider)
    if not st["installed"]:
        ui.error(f"{a['name']} isn't installed. Install it with:\n    {a['install']}\nthen sign in: {a['login']}")
        return 2
    if not st["signed_in"]:
        ui.error(f"{a['name']} is {st['detail']}")
        return 2
    sess = Session(provider, s, ui, st["path"])
    mode = {"ask": "asks before changes", "acceptEdits": "edits allowed", "plan": "plan only"}[sess.mode]
    ui.p(ui.c("2", f"rooks → {a['name']} ({st['detail']}) · {s.root} · {mode}"))
    if prompt:
        return 0 if sess.run(prompt) else 1
    ui.p(ui.c("2", "Type a task; follow-ups continue the same session. Ctrl-D to quit."))
    while True:
        try:
            if sys.stdin.isatty():
                line = input(ui.c("36", "› "))
            else:
                ui.out.write("› "); ui.out.flush()
                line = sys.stdin.readline()
                if not line:
                    raise EOFError
        except EOFError:
            ui.p()
            return 0
        except KeyboardInterrupt:
            ui.p()
            continue
        line = line.strip()
        if line in ("/exit", "/quit"):
            return 0
        if line:
            sess.run(line)


def doctor(provider: str) -> int:
    st = status(provider)
    print(f"provider: {provider} (delegates to {AGENTS[provider]['name']}{' at ' + st['path'] if st['path'] else ''})")
    print(f"  {st['detail']}")
    return 0 if st["installed"] and st["signed_in"] else 1


__all__ = ["AGENTS", "Session", "doctor", "main", "status", "Path"]
