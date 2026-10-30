"""Workbench backend as a desktop sidecar.

The Tauri shell (desktop/src-tauri) starts this program, frozen by PyInstaller into one executable, with:

    workbench-server --port <free port> --data-dir <per-user app data dir> [--exit-with-parent]

and WORKBENCH_DESKTOP_TOKEN in the environment. It then:
  1. loads APP_SECRET and TOOLS_API_KEY from the OS keychain, creating them on first run
     (falls back to a 0600 file in the data directory when no keychain is usable, e.g. a headless Linux box),
  2. points Workbench at the per-user data directory and the bundled frontend,
  3. serves on 127.0.0.1 only.

It is also runnable unfrozen for development: python desktop/sidecar/workbench_server.py --port 8765 --data-dir /tmp/wb
"""

import argparse
import json
import os
import secrets
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
    wanted = {"APP_SECRET": 48, "TOOLS_API_KEY": 32}
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


def _exit_with_parent() -> None:
    """The shell holds our stdin open; when it exits (even if it crashes) stdin closes and so do we."""
    def watch():
        try:
            while sys.stdin.read(1):
                pass
        finally:
            os._exit(0)

    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="workbench-server")
    p.add_argument("--port", type=int, default=int(os.getenv("WORKBENCH_PORT", "8765")))
    p.add_argument("--data-dir", default=os.getenv("DATA_DIR", ""))
    p.add_argument("--exit-with-parent", action="store_true")
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
    os.environ.update(values)
    os.environ.update(
        DATA_DIR=str(data_dir),
        STATIC_DIR=str(_static_dir()),
        WORKBENCH_DESKTOP="1",
        WORKBENCH_SECRET_BACKEND="keychain" if where == "os-keychain" else "fernet",
        COOKIE_SECURE="0",
    )
    os.environ.setdefault("OLLAMA_URL", "http://127.0.0.1:11434")  # the native Ollama app's default address
    if args.exit_with_parent:
        _exit_with_parent()

    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(REPO / "workbench" / "backend"))
    import uvicorn

    from app.main import app

    print(f"workbench-server listening on http://127.0.0.1:{args.port} (secrets: {where})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info", proxy_headers=False)


if __name__ == "__main__":
    main()
