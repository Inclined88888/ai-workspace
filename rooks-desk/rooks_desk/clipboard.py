"""Clipboard through the OS's own tools: pbcopy/pbpaste (macOS), PowerShell (Windows), wl-copy, xclip or xsel (Linux)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def _cmds():
    if sys.platform == "darwin":
        return ["pbpaste"], ["pbcopy"]
    if os.name == "nt":
        return (["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"])
    if os.getenv("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        return ["wl-paste", "-n"], ["wl-copy"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard", "-i"]
    if shutil.which("xsel"):
        return ["xsel", "-b", "-o"], ["xsel", "-b", "-i"]
    return None, None


def get() -> str:
    g, _ = _cmds()
    if not g:
        raise RuntimeError("no clipboard tool found (install xclip or xsel)")
    r = subprocess.run(g, capture_output=True, text=True, timeout=10)
    return r.stdout


def set(text: str) -> None:  # noqa: A001
    _, s = _cmds()
    if not s:
        raise RuntimeError("no clipboard tool found (install xclip or xsel)")
    # xclip keeps running to own the selection; don't wait for it
    p = subprocess.Popen(s, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    p.communicate(text, timeout=10) if not s[0].startswith("xclip") else (p.stdin.write(text), p.stdin.close())
