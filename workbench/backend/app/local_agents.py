"""Start the browser on this computer when a person has allowed a look and it is not already running.

The desktop app starts it on launch. A dev server often does not, which used to hide the tool and make chat
say a work site was missing. Tests never start a process (PYTEST_CURRENT_TEST).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from . import config

_PROC: subprocess.Popen | None = None
_DESK: subprocess.Popen | None = None


def _installed() -> str | None:
    if os.environ.get("ROOKS_BROWSER_BIN"):
        p = Path(os.environ["ROOKS_BROWSER_BIN"])
        return str(p) if p.exists() else None
    found = shutil.which("rooks-browser")
    if found:
        return found
    for d in (Path.home() / ".local" / "bin", Path.home() / ".ai-workspace" / "bin"):
        exe = d / "rooks-browser"
        if exe.exists():
            return str(exe)
    return None


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _up(url: str) -> bool:
    try:
        urllib.request.urlopen(f"{url}/healthz", timeout=0.4).close()
        return True
    except Exception:  # noqa: BLE001 - not up yet, or not our process
        return False


def unavailable() -> str | None:
    """None when a browser job can be posted. A short sentence when it cannot, for the model to say plainly."""
    if config.BROWSER_URL and config.BROWSER_API_KEY:
        return None
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("WORKBENCH_ROOKS_BROWSER") == "0":
        return "The browser is not running on this computer. Say that plainly. Do not tell them to add a site."
    exe = _installed()
    if not exe:
        return "The browser is not installed on this computer. Say that plainly. Do not tell them to add a site."
    if not _start(exe):
        return "The browser did not start. Say that plainly. Do not tell them to add a site."
    return None


def _start(exe: str) -> bool:
    global _PROC
    if _PROC and _PROC.poll() is None and config.BROWSER_URL and _up(config.BROWSER_URL):
        return True
    key = config.BROWSER_API_KEY or os.environ.get("BROWSER_API_KEY") or ""
    if not key:
        import secrets
        key = secrets.token_urlsafe(32)
    port = _port()
    url = f"http://127.0.0.1:{port}"
    app_url = os.environ.get("WORKBENCH_URL") or "http://127.0.0.1:8000"
    env = {**os.environ, "ROOKS_BROWSER_TOKEN": key, "WORKBENCH_URL": app_url, "WORKBENCH_API_KEY": key,
           "ROOKS_BROWSER_PROVIDER": os.environ.get("ROOKS_BROWSER_PROVIDER", "workbench")}
    headed = os.environ.get("ROOKS_BROWSER_HEADLESS") != "1"
    log_dir = Path(config.DATA_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "rooks-browser.log", "ab")  # noqa: SIM115 - lives with the child
    try:
        _PROC = subprocess.Popen(
            [exe, "serve", "--port", str(port), "--headed" if headed else "--headless"],
            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        )
    except OSError:
        return False
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if _up(url):
            config.BROWSER_URL = url
            config.BROWSER_API_KEY = key
            return True
        if _PROC.poll() is not None:
            return False
        time.sleep(0.2)
    return False


def _desk_installed() -> str | None:
    if os.environ.get("ROOKS_DESK_BIN"):
        p = Path(os.environ["ROOKS_DESK_BIN"])
        return str(p) if p.exists() else None
    found = shutil.which("rooks-desk")
    if found:
        return found
    for d in (Path.home() / ".local" / "bin", Path.home() / ".ai-workspace" / "bin"):
        exe = d / "rooks-desk"
        if exe.exists():
            return str(exe)
    return None


def computer_unavailable() -> str | None:
    """None when a computer job can be posted. A short sentence when it cannot."""
    if config.DESK_URL and config.DESK_API_KEY:
        return None
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("WORKBENCH_ROOKS_DESK") == "0":
        return "Computer use is not running on this computer. Say that plainly."
    exe = _desk_installed()
    if not exe:
        return "Computer use is not installed on this computer. Say that plainly."
    if not _start_desk(exe):
        return "Computer use did not start. Say that plainly."
    return None


def _start_desk(exe: str) -> bool:
    global _DESK
    if _DESK and _DESK.poll() is None and config.DESK_URL and _up(config.DESK_URL):
        return True
    key = config.DESK_API_KEY or config.BROWSER_API_KEY or os.environ.get("DESK_API_KEY") or os.environ.get("BROWSER_API_KEY") or ""
    if not key:
        import secrets
        key = secrets.token_urlsafe(32)
    port = _port()
    url = f"http://127.0.0.1:{port}"
    app_url = os.environ.get("WORKBENCH_URL") or "http://127.0.0.1:8000"
    env = {**os.environ, "ROOKS_DESK_TOKEN": key, "WORKBENCH_URL": app_url, "WORKBENCH_API_KEY": key,
           "ROOKS_DESK_PROVIDER": os.environ.get("ROOKS_DESK_PROVIDER", "workbench")}
    log_dir = Path(config.DATA_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "rooks-desk.log", "ab")  # noqa: SIM115 - lives with the child
    try:
        _DESK = subprocess.Popen(
            [exe, "serve", "--port", str(port)],
            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        )
    except OSError:
        return False
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if _up(url):
            config.DESK_URL = url
            config.DESK_API_KEY = key
            return True
        if _DESK.poll() is not None:
            return False
        time.sleep(0.2)
    return False
