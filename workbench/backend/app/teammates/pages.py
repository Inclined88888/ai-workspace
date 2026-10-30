"""Pages: save a report, plan or dashboard from a chat as a page you can keep, edit and share (feat/grok-style).

A page is Markdown (with the live-docs blocks from feat/v02-automations: tables, charts) stored locally. Export makes one
self-contained HTML file (inline styles in the chosen theme, no scripts, no outside requests), ready to email or print.
Share is off by default; turning it on mints a long random link that works on this computer and, when phone access is on,
your Wi-Fi. Turning it off kills the link.
"""

from __future__ import annotations

import html
import re
import secrets

from fastapi import HTTPException

from .. import config, db

SCHEMA = """
CREATE TABLE IF NOT EXISTS tm_pages (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  teammate_id INTEGER,
  conversation_id INTEGER,
  share_token TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""
_ready: set[str] = set()

# Light editorial look, matched to Sunshine; theme variables cannot travel into a standalone file, so the export carries
# a small fixed palette per theme.
PALETTES = {
    "sunshine": {"bg": "#fffdf7", "ink": "#1d1a14", "muted": "#6b6457", "accent": "#e8a317", "line": "#ece5d3"},
    "paper": {"bg": "#ffffff", "ink": "#111111", "muted": "#666666", "accent": "#111111", "line": "#e6e6e6"},
    "cherry": {"bg": "#fffafa", "ink": "#16121a", "muted": "#6a5f66", "accent": "#c8102e", "line": "#efe2e4"},
    "neon": {"bg": "#0b0b0b", "ink": "#f4f4f0", "muted": "#a3a39a", "accent": "#c6ff00", "line": "#262626"},
    "dark": {"bg": "#141414", "ink": "#f2efe8", "muted": "#a39e93", "accent": "#e8a317", "line": "#2b2b2b"},
}


def ensure() -> None:
    key = str(config.DB_PATH)
    if key not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(key)


def _out(r: dict | None, full: bool = True) -> dict:
    if not r:
        raise HTTPException(404, "Page not found")
    o = {k: r[k] for k in ("id", "title", "teammate_id", "conversation_id", "created_at", "updated_at")}
    o["shared"] = bool(r.get("share_token"))
    o["share_path"] = f"/p/{r['share_token']}" if r.get("share_token") else ""
    if full:
        o["body"] = r["body"]
    else:
        o["preview"] = re.sub(r"[#*_`>|-]+", " ", r["body"])[:160].strip()
    return o


def listing(teammate_id: int | None = None) -> list[dict]:
    ensure()
    return [_out(r, False) for r in db.all("SELECT * FROM tm_pages WHERE (? IS NULL OR teammate_id = ?) ORDER BY updated_at DESC LIMIT 200",
                                           teammate_id, teammate_id)]


def get(pid: int) -> dict:
    ensure()
    return _out(db.one("SELECT * FROM tm_pages WHERE id = ?", pid))


def create(title: str, body: str, teammate_id: int | None = None, conversation_id: int | None = None) -> dict:
    ensure()
    title = (title or "").strip()[:160] or "Untitled page"
    pid = db.run("INSERT INTO tm_pages(title, body, teammate_id, conversation_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                 title, (body or "")[:400_000], teammate_id, conversation_id, db.now(), db.now())
    return get(pid)


def from_message(message_id: int, title: str = "") -> dict:
    m = db.one("SELECT id, conversation_id, content, role FROM messages WHERE id = ?", message_id)
    if not m or m["role"] != "assistant" or not (m["content"] or "").strip():
        raise HTTPException(404, "That reply can't be saved as a page")
    from . import store

    t, _ = store.teammate_for(m["conversation_id"])
    first = next((ln.lstrip("# ").strip() for ln in m["content"].splitlines() if ln.strip()), "Page")
    return create(title or first[:120], m["content"], t["id"] if t else None, m["conversation_id"])


def update(pid: int, title: str | None, body: str | None) -> dict:
    p = get(pid)
    db.run("UPDATE tm_pages SET title = ?, body = ?, updated_at = ? WHERE id = ?",
           (title if title is not None else p["title"]).strip()[:160] or p["title"], (body if body is not None else p["body"])[:400_000],
           db.now(), pid)
    return get(pid)


def delete(pid: int) -> dict:
    ensure()
    db.run("DELETE FROM tm_pages WHERE id = ?", pid)
    return {"deleted": pid}


def share(pid: int, on: bool) -> dict:
    get(pid)
    db.run("UPDATE tm_pages SET share_token = ? WHERE id = ?", secrets.token_urlsafe(24) if on else None, pid)
    return get(pid)


def by_token(token: str) -> dict:
    ensure()
    if not token or len(token) < 20:
        raise HTTPException(404, "Page not found")
    return _out(db.one("SELECT * FROM tm_pages WHERE share_token = ?", token))


# Markdown to safe HTML (small, dependency free: headings, lists, tables, bold, italics, code, links) ----------------
def _inline(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noreferrer">\1</a>', t)
    return t


def md_to_html(md: str) -> str:
    out: list[str] = []
    lines = (md or "").replace("\r", "").split("\n")
    i = 0
    in_list = ""
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            code = []
            while j < len(lines) and not lines[j].startswith("```"):
                code.append(lines[j])
                j += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
            i = j + 1
            continue
        if "|" in ln and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and "|" in lines[j]:
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(h)}</th>" for h in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")
            i = j
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        lm = re.match(r"^\s*([-*]|\d+\.)\s+(.*)", ln)
        if in_list and not lm:
            out.append(f"</{in_list}>")
            in_list = ""
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
        elif lm:
            kind = "ol" if lm.group(1)[0].isdigit() else "ul"
            if in_list != kind:
                if in_list:
                    out.append(f"</{in_list}>")
                out.append(f"<{kind}>")
                in_list = kind
            out.append(f"<li>{_inline(lm.group(2))}</li>")
        elif ln.strip().startswith(">"):
            out.append(f"<blockquote>{_inline(ln.strip()[1:].strip())}</blockquote>")
        elif ln.strip():
            out.append(f"<p>{_inline(ln)}</p>")
        i += 1
    if in_list:
        out.append(f"</{in_list}>")
    return "\n".join(out)


def export_html(pid: int, theme: str = "sunshine") -> str:
    p = get(pid)
    c = PALETTES.get(theme, PALETTES["sunshine"])
    css = (f"body{{margin:0;background:{c['bg']};color:{c['ink']};font:17px/1.6 'Hanken Grotesk',system-ui,sans-serif}}"
           f"main{{max-width:760px;margin:0 auto;padding:56px 24px 80px}}"
           f"h1,h2,h3{{font-family:'Bodoni Moda',Georgia,serif;font-weight:600;letter-spacing:-.01em;line-height:1.15}}"
           f"h1{{font-size:44px;margin:0 0 8px}}.meta{{color:{c['muted']};font-size:13px;text-transform:uppercase;letter-spacing:.12em;"
           f"border-bottom:1px solid {c['line']};padding-bottom:18px;margin-bottom:28px}}"
           f"a{{color:inherit;text-decoration-color:{c['accent']};text-underline-offset:3px}}"
           f"table{{border-collapse:collapse;width:100%;margin:18px 0;font-size:15px}}th,td{{border-bottom:1px solid {c['line']};"
           f"padding:8px 10px;text-align:left}}th{{font-weight:600}}code,pre{{font-family:'JetBrains Mono',monospace;font-size:14px}}"
           f"pre{{background:{c['line']};padding:14px;border-radius:10px;overflow:auto}}"
           f"blockquote{{border-left:3px solid {c['accent']};margin:16px 0;padding:2px 16px;color:{c['muted']}}}"
           f"@media print{{body{{background:#fff;color:#000}}}}")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">"
            f"<title>{html.escape(p['title'])}</title><style>{css}</style></head><body><main>"
            f"<h1>{html.escape(p['title'])}</h1><div class=\"meta\">AI Workspace page · updated {html.escape(p['updated_at'][:10])}</div>"
            f"{md_to_html(p['body'])}</main></body></html>")
