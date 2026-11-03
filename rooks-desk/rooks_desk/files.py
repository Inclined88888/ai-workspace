"""File tools. Inside the allowed folders they run without asking; outside, the agent asks first (policy.py).
Deleting never destroys: the file moves to Rooks Desk's trash with a timestamp, so it can be put back."""

from __future__ import annotations

import fnmatch
import os
import shutil
import time
from pathlib import Path

MAX_READ = 200_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "Library", "AppData", ".cache"}


def expand(p: str) -> Path:
    return Path(os.path.expandvars(str(p))).expanduser()


def read_file(path: str, max_chars: int = 20000) -> dict:
    p = expand(path)
    data = p.read_bytes()[:MAX_READ]
    if b"\x00" in data[:4096]:
        return {"path": str(p), "binary": True, "size": p.stat().st_size, "text": ""}
    text = data.decode("utf-8", errors="replace")
    return {"path": str(p), "size": p.stat().st_size, "text": text[:max_chars], "truncated": len(text) > max_chars}


def write_file(path: str, text: str, append: bool = False) -> dict:
    p = expand(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    with open(p, "a" if append else "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return {"path": str(p), "bytes": len(text.encode()), "created": not existed, "appended": append}


def list_dir(path: str, limit: int = 200) -> dict:
    p = expand(path)
    out = []
    for e in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))[:limit]:
        try:
            st = e.stat()
            out.append({"name": e.name + ("/" if e.is_dir() else ""), "size": st.st_size if e.is_file() else None,
                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
        except OSError:
            out.append({"name": e.name})
    return {"path": str(p), "entries": out}


def search_files(root: str, pattern: str = "*", text: str = "", limit: int = 100) -> dict:
    """Find files by name glob and, optionally, containing text."""
    base = expand(root)
    hits = []
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if not fnmatch.fnmatch(f.lower(), pattern.lower()):
                continue
            fp = Path(dirpath) / f
            if text:
                try:
                    if fp.stat().st_size > 5_000_000 or text.lower() not in fp.read_text("utf-8", errors="ignore").lower():
                        continue
                except OSError:
                    continue
            hits.append(str(fp))
            if len(hits) >= limit:
                return {"root": str(base), "matches": hits, "truncated": True}
    return {"root": str(base), "matches": hits, "truncated": False}


def move_file(src: str, dst: str) -> dict:
    s, d = expand(src), expand(dst)
    if d.is_dir():
        d = d / s.name
    if d.exists():
        raise FileExistsError(f"{d} already exists")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"from": str(s), "to": str(d)}


def delete_file(path: str, trash: Path) -> dict:
    p = expand(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / f"{time.strftime('%Y%m%d-%H%M%S')}-{p.name}"
    shutil.move(str(p), str(dest))
    return {"path": str(p), "moved_to_trash": str(dest)}
