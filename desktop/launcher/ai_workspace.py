"""`ai-workspace`: the one launch command that install.sh / install.ps1 put on the PATH.

  ai-workspace              open the app (the native desktop app if installed, else an app window in Chrome/Edge)
  ai-workspace --web        always use the app window (skip the native app)
  ai-workspace --no-open    start the backend only and print the sign-in link (servers, testing)
  ai-workspace --stop       stop a copy started by this launcher
  ai-workspace --status     is it running, where, and which agents are linked

The app window is Chrome or Edge in --app mode with its own profile: no tabs, no address bar, its own icon in the dock or
taskbar. Closing that window stops the backend. Data lives in the same folder the native app uses, so switching between
them later keeps chats, keys and settings.

Standard library only; runs with the Python of the install's venv (~/.ai-workspace/venv).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # the installed source tree (contains workbench/ and desktop/)
IDENT = "com.inclined88888.aiworkspace"  # desktop/src-tauri/tauri.conf.json identifier: same data folder as the native app
NAME = "AI Workspace"


def data_dir() -> Path:
    if os.environ.get("AI_WORKSPACE_DATA_DIR"):
        return Path(os.environ["AI_WORKSPACE_DATA_DIR"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / IDENT
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / IDENT
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / IDENT


def state_file() -> Path:
    return Path(os.environ.get("AI_WORKSPACE_HOME") or Path.home() / ".ai-workspace") / "launcher.json"


def native_app() -> list[str] | None:
    """The installed native app, if any, as a command that opens it."""
    if os.environ.get("AI_WORKSPACE_NO_NATIVE") == "1":
        return None
    if sys.platform == "darwin":
        for d in (Path("/Applications"), Path.home() / "Applications"):
            if (d / f"{NAME}.app").exists():
                return ["open", "-a", str(d / f"{NAME}.app")]
    elif sys.platform == "win32":
        roots = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", ""), os.environ.get("LOCALAPPDATA", "") + r"\Programs"]
        for r in filter(None, roots):
            for exe in (f"{NAME}.exe", "ai-workspace-desktop.exe"):
                p = Path(r) / NAME / exe
                if p.exists():
                    return [str(p)]
    else:
        for p in (Path.home() / ".ai-workspace" / "bin" / "AI-Workspace.AppImage", Path("/usr/bin/ai-workspace-desktop")):
            if p.exists() and os.access(p, os.X_OK):
                return [str(p)]
    return None


def window_browser() -> str | None:
    """Chrome, Edge or Chromium, for an app-mode window."""
    if os.environ.get("AI_WORKSPACE_BROWSER"):
        return os.environ["AI_WORKSPACE_BROWSER"]
    if sys.platform == "darwin":
        for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"):
            if Path(p).exists():
                return p
    elif sys.platform == "win32":
        for r in filter(None, (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA"))):
            for p in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
                if (Path(r) / p).exists():
                    return str(Path(r) / p)
    else:
        for n in ("google-chrome", "google-chrome-stable", "microsoft-edge", "chromium", "chromium-browser"):
            if shutil.which(n):
                return shutil.which(n)
    # Playwright's Chromium, installed for Rooks Browser when the computer has no Chrome or Edge
    cache = {"darwin": Path.home() / "Library" / "Caches" / "ms-playwright",
             "win32": Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"}.get(sys.platform, Path.home() / ".cache" / "ms-playwright")
    pats = {"darwin": "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
            "win32": "chromium-*/chrome-win*/chrome.exe"}.get(sys.platform, "chromium-*/chrome-linux*/chrome")
    found = sorted(cache.glob(pats)) if cache.exists() else []
    return str(found[-1]) if found else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def healthy(url: str, timeout: float = 1.5) -> bool:
    try:
        urllib.request.urlopen(f"{url}/healthz", timeout=timeout).close()
        return True
    except OSError:
        return False


def read_state() -> dict:
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {}


def write_state(d: dict) -> None:
    f = state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(d, fh)


_REDIRECTS: list[Path] = []


def _private_redirect(url: str, folder: Path) -> str:
    """A file:// page (0600, in the private data folder) that forwards to url, so the sign-in token is never on a
    command line where other local users could read it (ps / Task Manager). Removed a few seconds later."""
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        folder.chmod(0o700)
    for old in folder.glob("open-*.html"):  # left over from a launcher that exited early
        if old.stat().st_mtime < time.time() - 60:
            old.unlink(missing_ok=True)
    f = folder / f"open-{secrets.token_hex(8)}.html"
    _REDIRECTS.append(f)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f'<!doctype html><meta charset="utf-8"><title>{NAME}</title>'
                 f'<script>location.replace({json.dumps(url)})</script>')
    t = threading.Timer(15, lambda: f.unlink(missing_ok=True))
    t.daemon = True
    t.start()
    return f.as_uri()


def open_window(url: str, profile: Path) -> subprocess.Popen | None:
    exe = window_browser()
    if exe:
        profile.mkdir(parents=True, exist_ok=True)
        if "token=" in url:
            url = _private_redirect(url, data_dir() / "run")
        args = [exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                "--window-size=1280,860", f"--class={NAME}"]
        if os.environ.get("AI_WORKSPACE_WINDOW_ARGS"):
            args += os.environ["AI_WORKSPACE_WINDOW_ARGS"].split()
        try:
            return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    webbrowser.open(url)
    return None


def window_pid(profile: Path) -> int | None:
    """The pid of a Chrome/Edge app window still running on our profile (POSIX: its SingletonLock is host-pid)."""
    try:
        pid = int(os.readlink(profile / "SingletonLock").rsplit("-", 1)[1])
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError, IndexError):
        return None


def close_window(profile: Path) -> None:
    pid = None if sys.platform == "win32" else window_pid(profile)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def stop(state: dict) -> bool:
    close_window(data_dir() / "window")
    pid = state.get("pid")
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ai-workspace", description="Open AI Workspace.")
    p.add_argument("--web", action="store_true", help="use the app window even when the native app is installed")
    p.add_argument("--no-open", action="store_true", help="start the backend only and print the sign-in link")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--stop", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--version", action="store_true")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # progress lines show up at once, even when piped to a log

    st = read_state()
    running = bool(st.get("url")) and healthy(st["url"])
    if a.version:
        print((ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "dev")
        return 0
    if a.status:
        print(f"app: {'running at ' + st['url'] if running else 'not running'}")
        print(f"data: {data_dir()}")
        print(f"native app: {' '.join(native_app() or ['not installed (uses an app window in Chrome/Edge)'])}")
        for tool in ("rooks", "rooks-browser"):
            print(f"{tool}: {shutil.which(tool) or 'not on PATH'}")
        return 0
    if a.stop:
        ok = running and stop(st)
        print("stopped" if ok else "not running")
        return 0

    native = None if (a.web or a.no_open) else native_app()
    if native:
        print(f"Opening the {NAME} app…")
        subprocess.Popen(native)
        return 0

    if running:  # one copy at a time: reopen its window with a fresh sign-in link
        print(f"{NAME} is already running at {st['url']}")
        if not a.no_open:
            open_window(st["session_url"], data_dir() / "window")
            time.sleep(5)  # let the window read the forwarding page, then remove it
            for f in _REDIRECTS:
                f.unlink(missing_ok=True)
        return 0

    dd = data_dir()
    dd.mkdir(parents=True, exist_ok=True)
    close_window(dd / "window")  # a window left over from a backend that is gone would swallow the new one
    logs = dd / "logs"
    logs.mkdir(exist_ok=True)
    port = a.port or free_port()
    token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "WORKBENCH_DESKTOP_TOKEN": token}
    log = open(logs / "workbench.log", "ab")  # noqa: SIM115
    server = [sys.executable, str(ROOT / "desktop" / "sidecar" / "workbench_server.py"), "--port", str(port), "--data-dir", str(dd),
              "--exit-with-parent"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(server, env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    print(f"Starting {NAME}…", flush=True)
    deadline = time.monotonic() + 90
    while not healthy(url, 1):
        if proc.poll() is not None or time.monotonic() > deadline:
            print(f"{NAME} did not start. Last lines of {logs / 'workbench.log'}:", file=sys.stderr)
            try:
                print("\n".join((logs / "workbench.log").read_text(errors="replace").splitlines()[-25:]), file=sys.stderr)
            except OSError:
                pass
            proc.kill()
            return 1
        time.sleep(0.3)
    session = f"{url}/api/desktop/session?token={token}"
    write_state({"url": url, "session_url": session, "pid": proc.pid})
    print(f"{NAME} is running at {url}  (logs: {logs})")
    rc = 0
    try:
        if a.no_open:
            print(f"Sign in: {session}")
            print("Press Ctrl+C to stop.")
            proc.wait()
        else:
            started = time.monotonic()
            win = open_window(session, dd / "window")
            if win:
                print("Close the window (or press Ctrl+C) to quit.")
                win.wait()
                if time.monotonic() - started < 5:  # handed off to a browser already running on this profile
                    print("Opened in the existing app window. Press Ctrl+C here to quit.")
                    proc.wait()
            else:
                print("Opened in your browser. Press Ctrl+C here to quit.")
                proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.stdin.close()  # --exit-with-parent: the backend stops itself (and Rooks Browser) when stdin closes
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if read_state().get("pid") == proc.pid:
            state_file().unlink(missing_ok=True)
        print(f"{NAME} stopped.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
