"""Checked answers (feat/v02-core, Pro).

"Check this" takes one assistant answer and:

1. lists the sources that answer was built from: the files, notes, web pages, emails and work-site records its tools read
   in that turn (each gets a number, S1, S2...);
2. pass one: the model splits the answer into factual claims and says which sources each claim came from;
3. claims with no source get candidate sources found again: passages in Files, notes, site records, and the web when a
   search provider is set up;
4. pass two: the model re-reads each claim against its sources and gives a verdict with the exact supporting quote;
5. plain checks that need no model: the quote must really be in the source, and the numbers in the claim must appear in
   it. A claim that fails is downgraded, so a confident-sounding model can't talk its way past the source text.

The result is the claim list (verdict, source, quote) plus the answer split into segments so the screen can underline
supported claims with their source number and flag unsupported ones inline. With check mode on for a chat, the system
prompt asks the assistant to rely on sources and cite them, and the app checks each answer when it finishes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import config, db, features_v02, llm
from .security import require_user

log = logging.getLogger("checked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS checked_modes (conversation_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS checked_results (
  id INTEGER PRIMARY KEY, conversation_id INTEGER, content_hash TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(conversation_id, content_hash)
);
"""
_ready: set[str] = set()


def ensure() -> None:
    if str(config.DB_PATH) not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(str(config.DB_PATH))


# Mode ---------------------------------------------------------------------------------------------------------------
def mode(conversation_id: int | None) -> bool:
    ensure()
    if conversation_id:
        r = db.one("SELECT enabled FROM checked_modes WHERE conversation_id = ?", conversation_id)
        if r:
            return bool(r["enabled"])
    return db.get_setting("checked_default") == "1"


def set_mode(conversation_id: int | None, on: bool, default: bool = False) -> bool:
    ensure()
    if on:
        features_v02.require("checked_answers")
    if default or not conversation_id:
        db.set_setting("checked_default", "1" if on else "0")
    if conversation_id:
        db.run("INSERT INTO checked_modes(conversation_id, enabled) VALUES (?, ?) ON CONFLICT(conversation_id) DO UPDATE SET "
               "enabled = excluded.enabled", conversation_id, 1 if on else 0)
    return on


PROMPT = ("Check mode is on: the user will verify every claim. Base each factual statement on something you looked up this turn "
          "(their files, notes, email, work sites, or the web) and use the tools to look it up rather than answering from memory. "
          "Put the source in brackets after the sentence, e.g. [Budget.xlsx, Sheet1] or [example.com]. If you can't find support "
          "for something, say so plainly instead of guessing.")


def context(ctx) -> list[str]:
    try:
        return [PROMPT] if mode(getattr(ctx, "conversation_id", None)) and features_v02.allowed("checked_answers") else []
    except Exception:  # noqa: BLE001
        return []


# Sources ------------------------------------------------------------------------------------------------------------
KIND_OF = {"read_file": "file", "search_files": "file", "analyse_spreadsheet": "file", "list_files": "file", "web_search": "web",
           "read_page": "web", "browse_web": "web", "search_notes": "note", "read_note": "note", "search_email": "email",
           "read_email": "email", "list_sites": "site", "site_records": "site", "site_check": "site", "search_memories": "memory",
           "recall": "memory", "get_agenda": "calendar", "list_events": "calendar", "list_tasks": "task", "run_python": "calculation"}
LABEL_KEYS = ("title", "name", "file", "subject", "url", "content")


def _label(d: dict, tool: str) -> str:
    for k in LABEL_KEYS:
        if isinstance(d.get(k), str) and d[k].strip():
            v = d[k].strip()
            loc = d.get("loc") or d.get("sheet") or ""
            return (v[:80] + (f", {loc}" if loc else "")).strip()
    return tool.replace("_", " ")


def _text(d) -> str:
    if isinstance(d, str):
        return d
    return json.dumps(d, ensure_ascii=False, default=str)


def _split_result(tool: str, raw: str) -> list[dict]:
    """One tool result becomes one or more sources (a search returns several passages or pages)."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = raw
    if isinstance(data, dict):
        for k in ("results", "hits", "items", "records", "passages"):
            if isinstance(data.get(k), list) and data[k] and all(isinstance(x, dict) for x in data[k]):
                data = data[k]
                break
    kind = KIND_OF.get(tool, "tool")
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        return [{"kind": kind, "tool": tool, "label": _label(x, tool), "url": x.get("url") or "",
                 "text": _text(x)[:4000]} for x in data[:12]]
    d = data if isinstance(data, dict) else {}
    return [{"kind": kind, "tool": tool, "label": _label(d, tool), "url": d.get("url") or "", "text": _text(data)[:8000]}]


def turn_sources(conversation_id: int, content: str | None = None) -> tuple[str, list[dict], str]:
    """The answer, the sources its turn used, and the question. Picks the assistant message matching `content`, else the latest."""
    msgs = db.all("SELECT id, role, content, name FROM messages WHERE conversation_id = ? ORDER BY id", conversation_id)
    if not msgs:
        raise HTTPException(404, "No such chat")
    idx = None
    want = (content or "").strip()
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m["role"] == "assistant" and m["content"].strip() and (not want or m["content"].strip() == want or want in m["content"]):
            idx = i
            break
    if idx is None:
        if not want:
            raise HTTPException(404, "There's no answer to check yet.")
        idx = len(msgs)  # check the text given, with this chat's latest turn as its sources
    start = idx - 1
    while start >= 0 and not (msgs[start]["role"] == "user" and msgs[start]["name"] != "claim_check"):
        start -= 1
    question = msgs[start]["content"] if start >= 0 else ""
    sources: list[dict] = []
    for m in msgs[start + 1: idx]:
        if m["role"] == "tool" and m["content"] and not m["content"].startswith('{"error"'):
            sources += _split_result(m["name"], m["content"])
    answer = msgs[idx]["content"] if idx < len(msgs) else want
    return answer, sources, question


async def find_more(claim: str, have: list[dict]) -> list[dict]:
    """Look again for support for an unsourced claim: Files, notes, site records, then the web."""
    out: list[dict] = []
    seen = {s["text"][:200] for s in have}
    try:
        from . import analysis

        for h in analysis.search(claim, limit=3):
            out.append({"kind": "file", "tool": "search_files", "label": f"{h['file']}, {h['loc']}".strip(", "), "url": "",
                        "file_id": h["file_id"], "text": h["text"]})
    except Exception as e:  # noqa: BLE001 - files add-on optional
        log.debug("file search skipped: %s", e)
    try:
        from . import services

        for d in services.list_docs(None, claim, limit=3):
            body = (db.one("SELECT body FROM docs WHERE id = ?", d["id"]) or {}).get("body", "")
            out.append({"kind": "note", "tool": "search_notes", "label": d.get("title") or "Note", "url": "", "text": body[:4000]})
    except Exception as e:  # noqa: BLE001
        log.debug("note search skipped: %s", e)
    try:
        from . import sites

        sites.ensure()
        words = [w for w in re.findall(r"[A-Za-z0-9][\w-]{3,}", claim)][:4]
        if words:
            cond = " OR ".join("(r.title LIKE ? OR r.fields LIKE ?)" for _ in words)
            args = [x for w in words for x in (f"%{w}%", f"%{w}%")]
            for r in db.all(f"SELECT r.*, s.name AS site FROM site_records r JOIN sites s ON s.id = r.site_id WHERE r.removed_at IS NULL "
                            f"AND ({cond}) LIMIT 3", *args):
                out.append({"kind": "site", "tool": "site_records", "label": f"{r['site']}: {r['title']}", "url": r.get("url") or "",
                            "text": json.dumps({k: r[k] for k in ("title", "status", "start", "due", "person", "amount", "location")}
                                               | {"fields": json.loads(r["fields"] or "{}")}, ensure_ascii=False)})
    except Exception as e:  # noqa: BLE001
        log.debug("site records skipped: %s", e)
    try:
        from . import search

        if search.provider():
            for r in (await search.web_search(claim[:200], 3))[:3]:
                out.append({"kind": "web", "tool": "web_search", "label": r.get("title") or r.get("url", ""), "url": r.get("url", ""),
                            "text": _text(r)})
    except Exception as e:  # noqa: BLE001
        log.debug("web search skipped: %s", e)
    return [s for s in out if s["text"][:200] not in seen]


# Model passes -------------------------------------------------------------------------------------------------------
EXTRACT_PROMPT = """You split an assistant's answer into the factual claims it makes and say which numbered sources each came from.
A claim is one checkable statement of fact: a number, date, name, quote, status or finding. Skip greetings, advice, opinions,
questions and offers. Copy each claim's sentence EXACTLY as it appears in the answer (character for character), one claim per
sentence. Reply with JSON only: {"claims": [{"text": "exact sentence", "sources": ["S1"]}]}. Use [] for sources when none fits."""

VERIFY_PROMPT = """You re-check claims against the source passages given. For each claim decide:
- "supported": a source says this (give the source id and copy the shortest exact quote from it that proves it);
- "partly": a source backs part of it, but a number, date, name or detail differs or is missing;
- "contradicted": a source says otherwise (quote it);
- "unsupported": no source says it.
Judge only by the source text, not by what you know. Quotes must be copied exactly from the source text.
Reply with JSON only: {"checks": [{"i": 0, "verdict": "supported", "source": "S1", "quote": "...", "note": "short reason"}]}"""


async def model_json(prompt: str, payload: str, model: str | None = None):
    """One JSON reply (patched in tests)."""
    res = await llm.chat(model or None, [{"role": "system", "content": prompt}, {"role": "user", "content": payload}],
                         trace={"channel": "checked"})
    return llm.parse_json(res["message"].get("content") or "")


SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(*])|\n+")
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(s: str) -> str:
    s = re.sub(r"[*_`#>\[\]]", "", s.lower())
    s = s.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", s).strip()


def _fallback_claims(answer: str) -> list[dict]:
    out = []
    for s in SENT_RE.split(answer):
        t = s.strip().lstrip("-*• ").strip()
        if len(t) > 25 and not t.endswith("?") and (NUM_RE.search(t) or re.search(r"\b[A-Z][a-z]+", t[1:])):
            out.append({"text": t, "sources": []})
    return out[:25]


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(s) if len(n.replace(",", "")) > 0}


def _plain_check(claim: str, src: dict | None, quote: str, verdict: str) -> tuple[str, str]:
    """Downgrade a verdict the source text doesn't bear out. Returns (verdict, note)."""
    if verdict not in ("supported", "partly"):
        return verdict, ""
    if not src:
        return "unsupported", "No source was given for it."
    body = _norm(src["text"])
    if quote and _norm(quote) not in body:
        return "unsupported", "The quoted passage isn't in the source."
    missing = [n for n in _numbers(claim) if n not in body.replace(",", "")]
    if missing and verdict == "supported":
        return "partly", f"The source doesn't show {', '.join(missing[:3])}."
    return verdict, ""


def _locate(answer: str, text: str) -> tuple[int, int] | None:
    i = answer.find(text)
    if i >= 0:
        return i, i + len(text)
    # tolerate markdown or whitespace differences: match on a squeezed version
    pat = r"\s*".join(re.escape(c) for c in re.sub(r"\s+", "", text)[:300])
    m = re.search(pat, answer) if pat else None
    return (m.start(), m.end()) if m else None


def segments(answer: str, claims: list[dict]) -> list[dict]:
    """The answer cut into plain text and claim spans, in order, for inline marks."""
    spans = sorted([(c["start"], c["end"], i) for i, c in enumerate(claims) if c.get("start") is not None])
    out, pos = [], 0
    for s, e, i in spans:
        if s < pos:
            continue
        if s > pos:
            out.append({"text": answer[pos:s]})
        out.append({"text": answer[s:e], "claim": i})
        pos = e
    if pos < len(answer):
        out.append({"text": answer[pos:]})
    return out


VERDICT_TEXT = {"supported": "Backed by a source", "partly": "Partly backed", "unsupported": "No source found",
                "contradicted": "A source says otherwise"}


async def check(conversation_id: int, content: str | None = None, model: str | None = None) -> dict:
    features_v02.require("checked_answers")
    ensure()
    answer, sources, question = turn_sources(conversation_id, content)
    key = hashlib.sha256(answer.encode()).hexdigest()
    cached = db.one("SELECT result FROM checked_results WHERE conversation_id = ? AND content_hash = ?", conversation_id, key)
    if cached:
        return json.loads(cached["result"])
    if not model:
        conv = db.one("SELECT model FROM conversations WHERE id = ?", conversation_id) or {}
        model = conv.get("model") or None
    for i, s in enumerate(sources):
        s["id"] = f"S{i + 1}"

    def listing(ss):
        return "\n\n".join(f"[{s['id']}] ({s['kind']}) {s['label']}\n{s['text'][:3000]}" for s in ss)

    # Pass 1: claims and the sources the answer drew on
    raw = await model_json(EXTRACT_PROMPT, f"Question: {question[:2000]}\n\nAnswer:\n{answer[:12000]}\n\nSources:\n{listing(sources) or '(none)'}", model)
    items = raw.get("claims") if isinstance(raw, dict) else raw if isinstance(raw, list) else None
    claims = [{"text": str(c.get("text", "")).strip(), "sources": [x for x in (c.get("sources") or []) if isinstance(x, str)]}
              for c in (items or []) if isinstance(c, dict) and str(c.get("text", "")).strip()]
    if not claims:
        claims = _fallback_claims(answer)
    claims = claims[:25]
    ids = {s["id"] for s in sources}
    for c in claims:
        c["sources"] = [x for x in c["sources"] if x in ids]
    # Look again for claims with nothing behind them
    for c in [c for c in claims if not c["sources"]][:6]:
        for s in await find_more(c["text"], sources):
            s["id"] = f"S{len(sources) + 1}"
            sources.append(s)
            c["sources"].append(s["id"])
    by_id = {s["id"]: s for s in sources}
    # Pass 2: verify each claim against only its sources
    checks: dict[int, dict] = {}
    if claims and sources:
        payload = "\n\n".join(f"Claim {i}: {c['text']}\nSources:\n" + (listing([by_id[x] for x in c["sources"]]) or "(none)")
                              for i, c in enumerate(claims))
        raw2 = await model_json(VERIFY_PROMPT, payload[:60000], model)
        for v in (raw2.get("checks") if isinstance(raw2, dict) else raw2 if isinstance(raw2, list) else []) or []:
            if isinstance(v, dict) and isinstance(v.get("i"), int) and 0 <= v["i"] < len(claims):
                checks[v["i"]] = v
    out_claims = []
    for i, c in enumerate(claims):
        v = checks.get(i, {})
        verdict = v.get("verdict") if v.get("verdict") in VERDICT_TEXT else "unsupported"
        sid = v.get("source") if v.get("source") in by_id else (c["sources"][0] if c["sources"] and verdict != "unsupported" else None)
        quote = str(v.get("quote") or "")[:600]
        verdict2, why = _plain_check(c["text"], by_id.get(sid), quote, verdict)
        loc = _locate(answer, c["text"])
        out_claims.append({"text": c["text"], "verdict": verdict2, "label": VERDICT_TEXT[verdict2], "source": sid,
                           "quote": quote if verdict2 != "unsupported" else "", "note": why or str(v.get("note") or "")[:300],
                           "downgraded": verdict2 != verdict, "start": loc[0] if loc else None, "end": loc[1] if loc else None})
    counts = {k: sum(1 for c in out_claims if c["verdict"] == k) for k in VERDICT_TEXT}
    flagged = counts["unsupported"] + counts["contradicted"]
    summary = ("Nothing in this answer needed checking." if not out_claims else
               f"All {len(out_claims)} claims are backed by a source." if counts["supported"] == len(out_claims) else
               f"{counts['supported']} of {len(out_claims)} claims backed" + (f", {counts['partly']} partly" if counts["partly"] else "")
               + (f", {flagged} flagged" if flagged else "") + ".")
    result = {"conversation_id": conversation_id, "answer": answer, "summary": summary, "counts": counts, "claims": out_claims,
              "sources": [{k: s.get(k) for k in ("id", "kind", "label", "url", "tool", "file_id")} | {"excerpt": s["text"][:400]}
                          for s in sources],
              "segments": segments(answer, out_claims), "checked_at": db.now()}
    db.run("INSERT OR REPLACE INTO checked_results(conversation_id, content_hash, result, created_at) VALUES (?, ?, ?, ?)",
           conversation_id, key, json.dumps(result), db.now())
    return result


# API ----------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/checked", dependencies=[Depends(require_user)])


class CheckIn(BaseModel):
    conversation_id: int
    content: str | None = Field(None, max_length=60000)


class ModeIn(BaseModel):
    conversation_id: int | None = None
    on: bool
    default: bool = False


@router.post("/check")
async def api_check(data: CheckIn):
    return await check(data.conversation_id, data.content)


@router.get("/mode")
def api_mode(conversation_id: int | None = None):
    return {"on": mode(conversation_id), "default": db.get_setting("checked_default") == "1",
            "allowed": features_v02.allowed("checked_answers"), "upgrade": features_v02.upgrade_detail("checked_answers")}


@router.put("/mode")
def api_set_mode(data: ModeIn):
    return {"on": set_mode(data.conversation_id, data.on, data.default)}
