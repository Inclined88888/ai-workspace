"""Run terminal commands with streaming output. POSIX: the user's login shell (`$SHELL -lc`), so PATH and aliases match
their terminal. Windows: PowerShell. One working directory persists across commands in a run (a `cd` sticks)."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass

MARK = "__ROOKS_DESK_CWD__"
# Rooks Desk's own secrets (its API token and the app's bridge key) never reach commands the model runs.
SECRET_ENV = ("ROOKS_DESK_TOKEN", "DESK_API_KEY", "BROWSER_API_KEY", "WORKBENCH_API_KEY")


def scrubbed_env(base: dict | None = None) -> dict:
    env = dict(os.environ if base is None else base)
    for k in SECRET_ENV:
        env.pop(k, None)
    return env


@dataclass
class ShellResult:
    command: str
    exit_code: int | None
    output: str
    cwd: str
    seconds: float
    timed_out: bool = False
    stopped: bool = False

    def tail(self, n: int = 4000) -> str:
        return self.output if len(self.output) <= n else "…" + self.output[-n:]

    def to_dict(self) -> dict:
        return {"command": self.command, "exit_code": self.exit_code, "output": self.tail(), "cwd": self.cwd,
                "seconds": round(self.seconds, 2), "timed_out": self.timed_out, "stopped": self.stopped}


class Shell:
    def __init__(self, cwd: str | None = None, env: dict | None = None):
        self.cwd = cwd or os.path.expanduser("~")
        self.env = env
        self.proc: subprocess.Popen | None = None

    def argv(self, command: str) -> list[str]:
        if os.name == "nt":
            exe = shutil.which("pwsh") or "powershell"
            # Render the command's objects as text inside its own pipeline: otherwise PowerShell's table formatter can
            # swallow them (and our cwd marker) when the script exits, e.g. `pwd` or `Get-ChildItem` printed nothing.
            wrapped = (f"& {{\n{command}\n}} | Out-String -Stream -Width 400\n"
                       f"$__c=$LASTEXITCODE; [Console]::Out.WriteLine(\"{MARK}$((Get-Location).Path)\"); exit $__c")
            return [exe, "-NoProfile", "-NonInteractive", "-Command", wrapped]
        sh = os.environ.get("SHELL") or "/bin/bash"
        if not os.path.exists(sh):
            sh = "/bin/sh"
        wrapped = f"{command}\n__rc=$?; printf '\\n{MARK}%s\\n' \"$PWD\"; exit $__rc"
        return [sh, "-lc", wrapped] if os.path.basename(sh) in ("bash", "zsh", "sh", "dash", "ksh") else ["/bin/sh", "-c", wrapped]

    def run(self, command: str, timeout: float = 120, on_line=None, should_stop=lambda: False) -> ShellResult:
        t0 = time.time()
        kw = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000200}  # CREATE_NEW_PROCESS_GROUP
        p = subprocess.Popen(self.argv(command), cwd=self.cwd if os.path.isdir(self.cwd) else None, env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                             encoding="utf-8", errors="replace", bufsize=1, **kw)
        self.proc = p
        lines: list[str] = []
        new_cwd = None

        def reader():
            nonlocal new_cwd
            for line in p.stdout:  # type: ignore[union-attr]
                if line.startswith(MARK):
                    new_cwd = line[len(MARK):].strip()
                    continue
                lines.append(line)
                if on_line:
                    try:
                        on_line(line.rstrip("\n"))
                    except Exception:  # noqa: BLE001
                        pass

        th = threading.Thread(target=reader, daemon=True)
        th.start()
        timed_out = stopped = False
        while p.poll() is None:
            if time.time() - t0 > timeout:
                timed_out = True
            elif should_stop():
                stopped = True
            if timed_out or stopped:
                self._kill(p)
                break
            time.sleep(0.05)
        th.join(2)
        self.proc = None
        if new_cwd and os.path.isdir(new_cwd):
            self.cwd = new_cwd
        out = "".join(lines)
        if out.endswith("\n\n"):
            out = out[:-1]
        if timed_out:
            out += f"\n[stopped after {timeout:g}s]"
        return ShellResult(command, p.returncode, out, self.cwd, time.time() - t0, timed_out, stopped)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self._kill(self.proc)

    @staticmethod
    def _kill(p: subprocess.Popen):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            else:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(2)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def terminal_command() -> list[str] | None:
    """A command that opens a visible terminal window, for when the person wants to watch."""
    import sys
    if sys.platform == "darwin":
        return ["open", "-a", "Terminal"]
    if os.name == "nt":
        return ["wt"] if shutil.which("wt") else ["cmd", "/c", "start", "powershell"]
    for t in ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "alacritty", "kitty", "xterm"):
        if shutil.which(t):
            return [t]
    return None
