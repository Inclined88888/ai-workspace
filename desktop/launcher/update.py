"""`ai-workspace --update`: update channels and the "update available" notice.

Design copied from hermes-agent hermes_cli/update_channel.py:

* The channel is stored per install (in ~/.ai-workspace/install.json), never home global, so a dev checkout cannot flip
  another install's feed.
* ``stable`` follows the newest release tag of the exact shape ``vX.Y.Z`` (no suffix, major capped at three digits so
  nothing odd ever reaches the stable feed). ``edge`` follows every commit on ``main``.
* An explicit choice is persisted by ``--channel``; an install that never chose tracks ``stable``.

Privacy (R8, no egress the user did not cause): the network is only used when the owner runs ``--update`` or
``--status``. A plain launch only reads the cached result of the last check.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CHANNEL_STABLE = "stable"
CHANNEL_EDGE = "edge"
CHANNELS = (CHANNEL_STABLE, CHANNEL_EDGE)
EDGE_REF = "main"
DEFAULT_REPO = "Inclined88888/ai-workspace"
STABLE_TAG_RE = re.compile(r"^v(?:0|[1-9]\d{0,2})\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?$")


def home() -> Path:
    return Path(os.environ.get("AI_WORKSPACE_HOME") or Path.home() / ".ai-workspace")


def _install() -> dict:
    try:
        return json.loads((home() / "install.json").read_text())
    except (OSError, ValueError):
        return {}


def get_channel() -> str:
    ch = _install().get("channel")
    return ch if ch in CHANNELS else CHANNEL_STABLE


def set_channel(channel: str) -> None:
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; use one of {', '.join(CHANNELS)}")
    d = _install()
    d["channel"] = channel
    home().mkdir(parents=True, exist_ok=True)
    (home() / "install.json").write_text(json.dumps(d, indent=1))


def parse_version(v: str) -> tuple | None:
    """Sortable key. A pre release (0.1.0-rc1) sorts before its release (0.1.0)."""
    m = _VERSION_RE.match((v or "").strip())
    if not m:
        return None
    pre = m.group(4)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre or "")


def newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)


def _get_json(url: str, timeout: float = 10) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ai-workspace"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https URL)
        return json.loads(r.read())


def latest_stable(repo: str = DEFAULT_REPO, fetch=_get_json) -> str | None:
    """Newest ``vX.Y.Z`` release tag, or None when there is none yet."""
    try:
        tag = fetch(f"https://api.github.com/repos/{repo}/releases/latest").get("tag_name", "")
    except Exception:  # noqa: BLE001 (offline, 404 when nothing is released, rate limit)
        return None
    return tag if STABLE_TAG_RE.match(tag or "") else None


def resolve_ref(channel: str, repo: str = DEFAULT_REPO, fetch=_get_json) -> str | None:
    return EDGE_REF if channel == CHANNEL_EDGE else latest_stable(repo, fetch)


def _cache() -> Path:
    return home() / "update-check.json"


def check(current: str, repo: str = DEFAULT_REPO, fetch=_get_json) -> dict:
    """Ask GitHub (owner caused) and cache the answer for the launch notice."""
    latest = latest_stable(repo, fetch)
    res = {"checked": int(time.time()), "current": current, "latest": latest,
           "available": bool(latest and newer(latest, current))}
    try:
        home().mkdir(parents=True, exist_ok=True)
        _cache().write_text(json.dumps(res))
    except OSError:
        pass
    return res


def cached_notice(current: str) -> str | None:
    """No network. The line to show at launch when the last check found a newer stable release."""
    try:
        c = json.loads(_cache().read_text())
    except (OSError, ValueError):
        return None
    latest = c.get("latest")
    if latest and newer(latest, current) and get_channel() == CHANNEL_STABLE:
        return f"Update available: {latest} (you have {current}). Run `ai-workspace --update`."
    return None


def install_command(ref: str, repo: str = DEFAULT_REPO, platform: str = sys.platform) -> list[str]:
    """Re-run the official installer at ``ref``: same code path as a fresh install, data folder untouched."""
    if platform == "win32":
        url = f"https://raw.githubusercontent.com/{repo}/{ref}/install.ps1"
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                f"$env:AI_WORKSPACE_REF='{ref}'; $env:AI_WORKSPACE_REPO='{repo}'; irm {url} | iex"]
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/install.sh"
    return ["bash", "-c", f"curl -fsSL '{url}' | AI_WORKSPACE_REF='{ref}' AI_WORKSPACE_REPO='{repo}' bash -s -- --no-start"]


_SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


def run_update(current: str, channel: str | None = None, repo: str = DEFAULT_REPO, check_only: bool = False,
               fetch=_get_json, runner=subprocess.call, out=print) -> int:
    if channel:
        set_channel(channel)
        out(f"channel: {channel} (saved for this install)")
    channel = get_channel()
    if not _SAFE_REF.match(repo):
        out(f"refusing odd repo name {repo!r}")
        return 2
    ref = resolve_ref(channel, repo, fetch)
    if channel == CHANNEL_STABLE:
        res = check(current, repo, fetch)
        if not ref:
            out("No stable release is published yet (or GitHub could not be reached). "
                "Use `ai-workspace --update --channel edge` to follow main.")
            return 1
        if not res["available"]:
            out(f"Up to date: {current} (latest stable {ref}).")
            return 0
        out(f"Update available: {ref} (you have {current}).")
    else:
        out(f"edge: installing the latest commit on {EDGE_REF} (you have {current}).")
    if check_only or not ref:
        return 0
    out("Your chats, keys and settings stay where they are; only the app is replaced.")
    return runner(install_command(ref, repo))
