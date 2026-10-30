"""Workbench backend as a desktop sidecar.

The Tauri shell (desktop/src-tauri) starts this program, frozen by PyInstaller into one executable, with:

    workbench-server --port <free port> --data-dir <per-user app data dir> [--exit-with-parent]

and WORKBENCH_DESKTOP_TOKEN in the environment. It then:
  1. loads APP_SECRET and TOOLS_API_KEY from the OS keychain, creating them on first run
     (falls back to a 0600 file in the data directory when no keychain is usable, e.g. a headless Linux box),
  2. points Workbench at the per-user data directory and the bundled frontend,
  3. serves on 127.0.0.1 only.

It also links the app to the two command-line agents installed next to it:
  * writes app.json (url + bridge key, 0600) to ~/.config/ai-workspace/ (Windows: %APPDATA%\\ai-workspace\\), so
    `rooks` (Rooks Code) and `rooks-browser` find the running app and use its default model (the smart router, "auto", unless changed) with no keys of
    their own; the file is removed when the app quits,
  * starts `rooks-browser serve` (when installed) on a free loopback port and points BROWSER_URL at it, so the
    Browser agent page and the assistant's browse tool drive the user's own Chrome or Edge,
  * starts `rooks-desk serve` (when installed) the same way and points DESK_URL at it, so the Computer page and the
    assistant's use_computer tool can operate the user's apps, terminal and files (asking before anything sensitive).

It is also runnable unfrozen for development: python desktop/sidecar/workbench_server.py --port 8765 --data-dir /tmp/wb
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

SERVICE = "ai-workspace"
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


def _bundle_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", REPO / "workbench" / "backend"))


def _static_dir() -> Path:
    frozen = _bundle_dir() / "static"
    return frozen if frozen.exists() else REPO / "workbench" / "frontend" / "dist"


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    kr = keyring.get_keyring()
    if isinstance(kr, fail.Keyring):
        return None
    # A Secret Service with no unlocked collection either raises here or, with no prompter
    # available, blocks forever waiting for an unlock dialog. Probe in a thread with a deadline
    # so a broken keychain degrades to the file fallback instead of a stuck splash screen.

    result: dict = {}

    def probe():
        try:
            kr.get_password(SERVICE, "probe")
            result["ok"] = True
        except Exception:  # noqa: BLE001
            result["ok"] = False

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(float(os.environ.get("WORKBENCH_KEYCHAIN_PROBE_TIMEOUT", "15")))
    if not result.get("ok"):
        print("workbench: OS keychain unavailable or locked; using the file fallback", file=sys.stderr)
        return None
    return keyring


def load_master_secrets(data_dir: Path) -> tuple[dict, str]:
    """APP_SECRET and TOOLS_API_KEY: (values, where they live: "os-keychain" or "file")."""
    wanted = {"APP_SECRET": 48, "TOOLS_API_KEY": 32, "BRIDGE_API_KEY": 32}
    kr = _keyring()
    if kr is not None:
        values = {}
        for name, size in wanted.items():
            v = kr.get_password(SERVICE, name)
            if not v:
                v = secrets.token_urlsafe(size)
                kr.set_password(SERVICE, name, v)
            values[name] = v
        return values, "os-keychain"
    path = data_dir / ".desktop-secrets.json"
    values = json.loads(path.read_text()) if path.exists() else {}
    if any(name not in values for name in wanted):
        values = {name: values.get(name) or secrets.token_urlsafe(size) for name, size in wanted.items()}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(values, f)
    return values, "file"


_CLEANUP: list = []


def _cleanup() -> None:
    while _CLEANUP:
        try:
            _CLEANUP.pop()()
        except Exception:  # noqa: BLE001
            pass


def app_link_path() -> Path:
    """Where the running app tells local agents how to reach it. Same rule in rooks_code and rooks_browser."""
    if os.environ.get("AI_WORKSPACE_APP_FILE"):
        return Path(os.environ["AI_WORKSPACE_APP_FILE"])
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "ai-workspace" / "app.json"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "ai-workspace" / "app.json"


def write_app_link(url: str, key: str) -> Path | None:
    path = app_link_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"url": url, "key": key, "pid": os.getpid()}, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"workbench: could not write {path}: {e}", file=sys.stderr)
        return None

    def remove():
        try:
            if json.loads(path.read_text()).get("pid") == os.getpid():
                path.unlink()
        except (OSError, ValueError):
            pass

    _CLEANUP.append(remove)
    return path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_rooks_browser() -> str | None:
    """The rooks-browser command: $ROOKS_BROWSER_BIN, PATH, or where the installer puts it (an app started from Finder or
    the Start menu does not see the shell's PATH)."""
    if os.environ.get("ROOKS_BROWSER_BIN"):
        return os.environ["ROOKS_BROWSER_BIN"] if Path(os.environ["ROOKS_BROWSER_BIN"]).exists() else None
    found = shutil.which("rooks-browser")
    if found:
        return found
    exe = "rooks-browser.exe" if sys.platform == "win32" else "rooks-browser"
    for d in (Path.home() / ".local" / "bin", Path.home() / ".ai-workspace" / "bin"):
        if (d / exe).exists():
            return str(d / exe)
    return None


def start_rooks_browser(app_url: str, key: str, log_dir: Path) -> str | None:
    """Run `rooks-browser serve` for the Browser agent page. Returns its URL, or None when it is not installed or off."""
    if os.environ.get("BROWSER_URL") or os.environ.get("WORKBENCH_ROOKS_BROWSER", "1") == "0":
        return None
    exe = find_rooks_browser()
    if not exe:
        print("workbench: rooks-browser is not installed; the Browser agent page stays off", file=sys.stderr)
        return None
    port = _free_port()
    headed = sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if os.environ.get("ROOKS_BROWSER_HEADLESS") == "1":
        headed = False
    env = {**os.environ, "ROOKS_BROWSER_TOKEN": key, "WORKBENCH_URL": app_url, "WORKBENCH_API_KEY": key,
           "ROOKS_BROWSER_PROVIDER": os.environ.get("ROOKS_BROWSER_PROVIDER", "workbench")}
    args = [exe, "serve", "--port", str(port), "--exit-with-parent", "--headed" if headed else "--headless"]
    log = open(log_dir / "rooks-browser.log", "ab")  # noqa: SIM115 - lives as long as the child
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log, creationflags=flags)
    except OSError as e:
        print(f"workbench: could not start rooks-browser: {e}", file=sys.stderr)
        return None

    def stop():
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()

    _CLEANUP.append(stop)
    print(f"workbench: rooks-browser serving on http://127.0.0.1:{port} ({'headed' if headed else 'headless'})", flush=True)
    return f"http://127.0.0.1:{port}"


def find_rooks_desk() -> str | None:
    """The rooks-desk command (computer use): $ROOKS_DESK_BIN, PATH, or where the installer puts it."""
    if os.environ.get("ROOKS_DESK_BIN"):
        return os.environ["ROOKS_DESK_BIN"] if Path(os.environ["ROOKS_DESK_BIN"]).exists() else None
    found = shutil.which("rooks-desk")
    if found:
        return found
    exe = "rooks-desk.exe" if sys.platform == "win32" else "rooks-desk"
    for d in (Path.home() / ".local" / "bin", Path.home() / ".ai-workspace" / "bin"):
        if (d / exe).exists():
            return str(d / exe)
    return None


def start_rooks_desk(app_url: str, key: str, log_dir: Path) -> str | None:
    """Run `rooks-desk serve` for the Computer page and the assistant's use_computer tool. Loopback only, with the
    bridge key as its token. It does nothing until a task is started, and asks the user before every sensitive step.
    Off with WORKBENCH_ROOKS_DESK=0. Returns its URL, or None when it is not installed or off."""
    if os.environ.get("DESK_URL") or os.environ.get("WORKBENCH_ROOKS_DESK", "1") == "0":
        return None
    exe = find_rooks_desk()
    if not exe:
        print("workbench: rooks-desk is not installed; the Computer page stays off", file=sys.stderr)
        return None
    port = _free_port()
    env = {**os.environ, "ROOKS_DESK_TOKEN": key, "WORKBENCH_URL": app_url, "WORKBENCH_API_KEY": key,
           "ROOKS_DESK_PROVIDER": os.environ.get("ROOKS_DESK_PROVIDER", "workbench")}
    args = [exe, "serve", "--port", str(port), "--exit-with-parent"]
    log = open(log_dir / "rooks-desk.log", "ab")  # noqa: SIM115 - lives as long as the child
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log, creationflags=flags)
    except OSError as e:
        print(f"workbench: could not start rooks-desk: {e}", file=sys.stderr)
        return None

    def stop():
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()

    _CLEANUP.append(stop)
    print(f"workbench: rooks-desk serving on http://127.0.0.1:{port}", flush=True)
    return f"http://127.0.0.1:{port}"


def _exit_with_parent() -> None:
    """The shell holds our stdin open; when it exits (even if it crashes) stdin closes and so do we."""
    def watch():
        try:
            while sys.stdin.read(1):
                pass
        finally:
            _cleanup()
            os._exit(0)

    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


def main(argv: list[str] | None = None) -> None:
    raw = sys.argv[1:] if argv is None else argv
    if raw[:1] == ["--run-python"]:  # the frozen app's own interpreter for the Files sandbox (app/compute.py)
        from app import compute

        compute.run_frozen(raw[1:])
        return
    p = argparse.ArgumentParser(prog="workbench-server")
    p.add_argument("--port", type=int, default=int(os.getenv("WORKBENCH_PORT", "8765")))
    p.add_argument("--data-dir", default=os.getenv("DATA_DIR", ""))
    p.add_argument("--exit-with-parent", action="store_true")
    p.add_argument("--no-agents", action="store_true", help="do not start rooks-browser, rooks-desk or write app.json (tests)")
    p.add_argument("--print-secret-backend", action="store_true", help="report where secrets would live and exit")
    args = p.parse_args(argv)
    if not args.data_dir:
        p.error("--data-dir is required")
    data_dir = Path(args.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)

    values, where = load_master_secrets(data_dir)
    if args.print_secret_backend:
        print(where)
        return
    bridge_key = values.pop("BRIDGE_API_KEY")
    os.environ.update(values)
    os.environ.update(
        DATA_DIR=str(data_dir),
        STATIC_DIR=str(_static_dir()),
        WORKBENCH_DESKTOP="1",
        WORKBENCH_SECRET_BACKEND="keychain" if where == "os-keychain" else "fernet",
        COOKIE_SECURE="0",
    )
    os.environ.setdefault("OLLAMA_URL", "http://127.0.0.1:11434")  # the native Ollama app's default address
    # The /v1 bridge (Rooks Code, Rooks Browser) and the browser service share this key; loopback only.
    os.environ.setdefault("BROWSER_API_KEY", bridge_key)
    app_url = f"http://127.0.0.1:{args.port}"
    import atexit

    atexit.register(_cleanup)
    if args.exit_with_parent:
        _exit_with_parent()
    if not args.no_agents:
        browser_url = start_rooks_browser(app_url, os.environ["BROWSER_API_KEY"], data_dir)
        if browser_url:
            os.environ["BROWSER_URL"] = browser_url
        desk_url = start_rooks_desk(app_url, os.environ["BROWSER_API_KEY"], data_dir)
        if desk_url:
            os.environ["DESK_URL"] = desk_url
            os.environ.setdefault("DESK_API_KEY", os.environ["BROWSER_API_KEY"])
        write_app_link(app_url, os.environ["BROWSER_API_KEY"])

    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(REPO / "workbench" / "backend"))
    import uvicorn

    from app.main import app

    print(f"workbench-server listening on http://127.0.0.1:{args.port} (secrets: {where})", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info", proxy_headers=False)
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
