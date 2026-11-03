"""Images in prompts: `@shot.png` mentions and `/image PATH [question]` send the picture itself to the model.

Only real images (checked by their first bytes: PNG, JPEG, WebP, GIF) up to 10 MB are sent; anything else stays a
plain mention the model can open with its file tools. Saved sessions keep a short note instead of the pixels, so
.rooks/runs does not fill up with base64.
"""

from __future__ import annotations

import base64
import re
import shlex
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 6
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MENTION = re.compile(r"(?<![\w/])@((?:\\ |[^\s])+)")


def sniff(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return ""


def _resolve(root: Path, raw: str) -> Path:
    p = Path(raw.replace("\\ ", " ")).expanduser()
    return p if p.is_absolute() else root / p


def load(path: Path) -> tuple[str, str] | None:
    """(mime, base64) of an image file, or None when it is not one we can send."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    mime = sniff(raw)
    return (mime, base64.b64encode(raw).decode()) if mime else None


def find(prompt: str, root: Path) -> list[tuple[str, str, str]]:
    """(name, mime, base64) for every @mention in the prompt that is an image file."""
    out, seen = [], set()
    for m in MENTION.finditer(prompt):
        raw = m.group(1).rstrip(".,;:!?)")
        if not raw.lower().endswith(EXTS) or raw in seen:
            continue
        seen.add(raw)
        got = load(_resolve(root, raw))
        if got and len(out) < MAX_IMAGES:
            out.append((raw, *got))
    return out


def content(prompt: str, root: Path) -> str | list[dict]:
    """The user message: plain text, or OpenAI-shaped parts when the prompt mentions images."""
    imgs = find(prompt, root)
    if not imgs:
        return prompt
    return [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}} for _name, mime, b64 in imgs]


def text_of(c) -> str:
    """The text of a message's content, with a short note per image (summaries, saved sessions, estimates)."""
    if isinstance(c, list):
        return "\n".join(p.get("text", "") if p.get("type") == "text" else "[image]"
                         for p in c if isinstance(p, dict))
    return c or ""


def without_pixels(messages: list[dict]) -> list[dict]:
    return [{**m, "content": text_of(m["content"])} if isinstance(m.get("content"), list) else m for m in messages]


def image_command(rest: str, root: Path) -> tuple[str | None, str]:
    """`/image PATH [question]` -> (prompt to run, error). The path may be quoted or contain escaped spaces."""
    rest = rest.strip()
    if not rest:
        return None, "usage: /image PATH [question], e.g. /image screenshot.png what is wrong here?"
    whole = _resolve(root, rest)
    if whole.is_file():  # an unquoted path with spaces and no question
        path, question = rest, ""
    else:
        try:
            parts = shlex.split(rest)
        except ValueError:
            parts = rest.split(" ", 1)
        path, question = parts[0], " ".join(parts[1:])
    p = _resolve(root, path)
    if not p.is_file():
        return None, f"no such file: {path}"
    if load(p) is None:
        return None, f"{path} is not a PNG, JPEG, WebP or GIF image under {MAX_BYTES // (1024 * 1024)} MB"
    return f"@{str(p).replace(' ', chr(92) + ' ')} {question or 'What does this image show?'}", ""
