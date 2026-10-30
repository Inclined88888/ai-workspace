"""Keeps getting better: thumbs, comments and the person's own edits become lessons.

An edit is compared with what the assistant wrote. Clear patterns (shorter, a different greeting or sign-off, bullet
points, a word always swapped) become a lesson without a model; otherwise the model is asked for the one lesson the
edit teaches. Lessons about a playbook go into its notes (used on the next run); everything else becomes a preference
in "What I know about you". The caller shows the lesson as "Learned from your change".
"""

from __future__ import annotations

import difflib
import re

from fastapi import HTTPException

from .. import db
from . import knowledge, playbooks, store

TARGETS = ("message", "run", "playbook", "note", "draft", "other")


def _first_line(t: str) -> str:
    return next((l.strip() for l in t.strip().splitlines() if l.strip()), "")


def _last_line(t: str) -> str:
    return next((l.strip() for l in reversed(t.strip().splitlines()) if l.strip()), "")


def heuristic_lessons(original: str, edited: str) -> list[str]:
    o, e = original.strip(), edited.strip()
    if not o or not e or o == e:
        return []
    out = []
    lo, le = len(o.split()), len(e.split())
    if le < lo * 0.7 and lo > 15:
        out.append(f"Keep it shorter (about {le} words rather than {lo}).")
    elif le > lo * 1.4 and le > 15:
        out.append("Include more detail; shorter versions were not enough.")
    fo, fe = _first_line(o), _first_line(e)
    greet = re.compile(r"^(hi|hello|hey|dear|g'day|good (morning|afternoon))\b", re.I)
    if greet.match(fe) and fo != fe and (not greet.match(fo) or greet.match(fo).group(0).lower() != greet.match(fe).group(0).lower()):
        out.append(f"Start messages with \u201c{greet.match(fe).group(0)}\u201d.")
    so, se = _last_line(o), _last_line(e)
    if se != so and len(se) <= 40 and len(e.splitlines()) > 1:
        out.append(f"Sign off with \u201c{se}\u201d.")
    bullets = re.compile(r"^\s*([-*\u2022]|\d+[.)])\s", re.M)
    if len(bullets.findall(e)) >= 2 and len(bullets.findall(o)) == 0:
        out.append("Use bullet points.")
    elif len(bullets.findall(o)) >= 2 and len(bullets.findall(e)) == 0:
        out.append("Write in sentences, not bullet points.")
    ow, ew = re.findall(r"[\w'$%.-]+", o), re.findall(r"[\w'$%.-]+", e)
    swaps = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=[w.lower() for w in ow], b=[w.lower() for w in ew], autojunk=False).get_opcodes():
        if tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1 and ow[i1].lower() != ew[j1].lower():
            swaps[(ow[i1], ew[j1])] = swaps.get((ow[i1], ew[j1]), 0) + 1
    for (a, b), _n in list(swaps.items())[:3]:
        if len(a) > 2 and len(b) > 2 and not (a.isdigit() or b.isdigit()):
            out.append(f"Say \u201c{b}\u201d, not \u201c{a}\u201d.")
    return out[:4]


LESSON_PROMPT = (
    "A person corrected their assistant's work. Compare the assistant's version with the person's version and write "
    "the ONE general lesson the assistant should follow next time for this person (tone, format, length, wording, facts "
    'about their work). Reply with only JSON: {"lesson": "short imperative sentence", "about": "preference|format|work"} '
    'or {"lesson": ""} if the change teaches nothing general.'
)


async def record(target_kind: str, target_id: str = "", rating: int = 0, original: str = "", edited: str = "",
                 comment: str = "", model: str | None = None) -> dict:
    store.ensure()
    if target_kind not in TARGETS:
        raise HTTPException(400, f"target_kind must be one of {', '.join(TARGETS)}")
    rating = max(-1, min(1, int(rating or 0)))
    lessons: list[str] = []
    if comment.strip():
        lessons.append(comment.strip().rstrip(".")[:300] + ".")
    lessons += heuristic_lessons(original, edited)
    about = "format"
    if not lessons and edited.strip() and original.strip() and edited.strip() != original.strip():
        try:
            out = await knowledge.model_json(LESSON_PROMPT, f"ASSISTANT VERSION:\n{original[:6000]}\n\nPERSON'S VERSION:\n{edited[:6000]}", model, "feedback")
            if isinstance(out, dict) and isinstance(out.get("lesson"), str) and out["lesson"].strip():
                lessons.append(out["lesson"].strip()[:300])
                about = out.get("about") if out.get("about") in ("preference", "format", "work") else "preference"
        except Exception:  # noqa: BLE001 - feedback is still recorded without a model
            pass
    playbook_id = None
    if target_kind == "run" and target_id:
        r = db.one("SELECT playbook_id FROM adapt_runs WHERE id = ?", int(target_id))
        if r:
            playbook_id = r["playbook_id"]
            db.run("UPDATE adapt_runs SET rating = ? WHERE id = ?", rating, int(target_id))
    elif target_kind == "playbook" and target_id:
        playbook_id = int(target_id)
    applied = []
    for lesson in lessons:
        if playbook_id:
            playbooks.add_note(playbook_id, lesson)
            where = f"playbook:{playbook_id}"
        else:
            f = knowledge.add(lesson, about, "feedback", evidence=(edited or comment)[:200], confidence=0.9) if knowledge.learning_on() else None
            where = f"fact:{f['id']}" if f else ""
        db.run("INSERT INTO adapt_lessons(target_kind, target_id, rating, lesson_enc, applied_to, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               target_kind, str(target_id), rating, store.seal(lesson), where, db.now())
        applied.append({"lesson": lesson, "applied_to": where})
    if not lessons:
        db.run("INSERT INTO adapt_lessons(target_kind, target_id, rating, lesson_enc, applied_to, created_at) VALUES (?, ?, ?, ?, '', ?)",
               target_kind, str(target_id), rating, store.seal(""), db.now())
    note = ("Learned from your change: " + " ".join(a["lesson"] for a in applied)) if applied else (
        "Thanks, noted." if rating else "")
    return {"learned": applied, "note": note}


def recent(limit: int = 30) -> list[dict]:
    store.ensure()
    rows = db.all("SELECT * FROM adapt_lessons WHERE lesson_enc != '' ORDER BY id DESC LIMIT ?", limit)
    return [{"id": r["id"], "lesson": store.open_text(r["lesson_enc"]), "target_kind": r["target_kind"], "target_id": r["target_id"],
             "applied_to": r["applied_to"], "rating": r["rating"], "created_at": r["created_at"]} for r in rows]
