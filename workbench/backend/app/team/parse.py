"""Plain-English parsing for the team lead (feat/team-lead). Deterministic, instant and offline.

  assignment("give Sam the Smith St quote by Friday, urgent")
      -> {"title": "Smith St quote", "member": <Sam>, "due": "2026-11-06", "priority": 3, ...}
  reply("blocked: waiting on parts")  -> {"status": "blocked", "note": "waiting on parts"}
  reply("50%") / "half done" / "done" / "on it" / "T12 done"
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from . import store

WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3,
            "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
VERBS = (r"(?:please\s+)?(?:can you\s+)?(?:give|assign|allocate|hand|send|pass|get|ask|have|tell|put|book|schedule|let)")


def due_from(text: str, base: date | None = None) -> tuple[str | None, str]:
    """The first date phrase in text (as YYYY-MM-DD) and the text without it."""
    base = base or store.today()
    t = text
    pats: list[tuple[str, callable]] = [
        (r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
        (r"\b(?:end of (?:the )?day|eod|today|tonight)\b", lambda m: base),
        (r"\b(?:tomorrow|tmrw|tmr)\b", lambda m: base + timedelta(days=1)),
        (r"\b(?:end of (?:the )?week|eow|this week)\b", lambda m: base + timedelta(days=(4 - base.weekday()) % 7)),
        (r"\bnext week\b", lambda m: base + timedelta(days=7 - base.weekday())),
        (r"\bin (\d{1,2}) days?\b", lambda m: base + timedelta(days=int(m[1]))),
        (r"\bin (?:a|one) week\b", lambda m: base + timedelta(days=7)),
        (r"\b(next )?(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b",
         lambda m: base + timedelta(days=((WEEKDAYS[m[2]] - base.weekday()) % 7 or 7) + (7 if m[1] else 0))),
        (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")[a-z]*\b", lambda m: _roll(base, MONTHS[m[2]], int(m[1]))),
        (r"\b(" + "|".join(MONTHS) + r")[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", lambda m: _roll(base, MONTHS[m[1]], int(m[2]))),
        (r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", lambda m: _dmy(base, int(m[1]), int(m[2]), m[3])),
    ]
    for p, fn in pats:
        m = re.search(r"(?:\b(?:by|due|before|on|for|until|from)\s+)?" + p, t, re.I)
        if m:
            inner = re.search(p, m.group(0), re.I)
            try:
                d = fn([inner.group(0)] + [g.lower() if g else g for g in inner.groups()])
            except ValueError:
                continue
            return d.isoformat(), (t[:m.start()] + " " + t[m.end():]).strip()
    return None, text


def _roll(base: date, month: int, day: int) -> date:
    d = date(base.year, month, day)
    return d if d >= base - timedelta(days=31) else date(base.year + 1, month, day)


def _dmy(base: date, d: int, m: int, y: str | None) -> date:
    year = int(y) + (2000 if y and len(y) == 2 else 0) if y else base.year
    return _roll(base, m, d) if not y else date(year, m, d)  # Australian order: day/month


def priority_from(text: str) -> tuple[int | None, str]:
    rules = [(3, r"\b(?:urgent(?:ly)?|asap|a\.s\.a\.p\.?|right away|emergency|top priority)\b"),
             (2, r"\b(?:high priority|important|priority)\b"), (1, r"\b(?:low priority|no rush|whenever|when you can)\b")]
    for p, pat in rules:
        m = re.search(pat, text, re.I)
        if m:
            return p, (text[:m.start()] + text[m.end():]).strip()
    return None, text


def _clean_title(s: str) -> str:
    s = re.sub(r"[,;:.!?]+\s*$", "", s.strip())
    s = re.sub(r"^(?:to|the|a|an|on|with|do|doing|finish|work on)\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:the|a|an)\s+", "", s, flags=re.I)
    s = re.sub(r"\s+(?:please|thanks|thank you|pls)$", "", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,-")
    return s[:1].upper() + s[1:] if s else s


def assignment(text: str) -> dict:
    """Who, what, when and how urgent from one sentence. member is None when nobody is named (suggest someone)."""
    raw = (text or "").strip()
    due, rest = due_from(raw)
    prio, rest = priority_from(rest)
    found, mstart, mend = None, -1, -1
    for m in sorted(store.members(), key=lambda x: -len(x["name"])):
        for nm in (m["name"], m["first_name"]):
            if not nm:
                continue
            hit = re.search(rf"\b{re.escape(nm)}(?:'s)?\b", rest, re.I)
            if hit:
                found, mstart, mend = m, hit.start(), hit.end()
                break
        if found:
            break
    if found:
        before, after = rest[:mstart], rest[mend:]
        before = re.sub(rf"^\s*{VERBS}\s*(?:it\s+)?(?:to|for)?\s*$", "", before, flags=re.I).strip()
        after = re.sub(r"^\s*(?:to|with|on)\s+", "", after, flags=re.I) if not before else after
        title = before if before and not after.strip(" ,.") else (after if not before else f"{before} {after}")
        # "assign the quote to Sam" -> before holds the verb + task
        title = re.sub(rf"^\s*{VERBS}\s+", "", title, flags=re.I)
        title = re.sub(r"\s+(?:to|for)\s*$", "", title.strip(), flags=re.I)
    else:
        title = re.sub(rf"^\s*{VERBS}\s+(?:someone|somebody|anyone|the team)?\s*(?:to\s+)?", "", rest, flags=re.I)
    title = _clean_title(title)
    proj = None
    try:
        from .. import projects

        hits = projects.score(raw)
        proj = hits[0] if hits else None
    except Exception:  # noqa: BLE001
        proj = None
    return {"title": title or raw[:120], "member": found, "due": due, "priority": prio if prio is not None else 0,
            "project_id": (proj or {}).get("id"), "project": (proj or {}).get("name", "")}


DONE = r"\b(?:done|finished|complete(?:d)?|all good|sorted|wrapped up|nailed it|that'?s it|ok done|signed off)\b|✅|👍"
DOING = r"\b(?:started|starting|on it|on my way|working on it|in progress|underway|begun|will do|heading there)\b"
BLOCKED = r"\b(?:blocked|stuck|waiting (?:on|for)|can'?t|cannot|unable|delayed|held up|problem|issue|need help)\b"


def reply(text: str) -> dict:
    """A member's reply to a task message: status, percent, note and an optional task reference (T12 / #12)."""
    raw = (text or "").strip()
    first = "\n".join(line for line in raw.splitlines() if not line.startswith(">"))  # drop quoted email text
    first = re.split(r"\n\s*(?:On .{0,80}wrote:|-----Original Message-----|From: )", first)[0].strip()
    out: dict = {"status": "", "percent": None, "note": "", "task_ref": None}
    ref = re.search(r"(?:\[?\bT|#|task\s*#?)\s*(\d{1,7})\]?", first, re.I)
    if ref:
        out["task_ref"] = int(ref.group(1))
        first = (first[:ref.start()] + first[ref.end():]).strip(" :-")
    low = first.lower()
    pct = re.search(r"\b(\d{1,3})\s*(?:%|percent|per cent)", low)
    if pct:
        out["percent"] = max(0, min(100, int(pct.group(1))))
    elif re.search(r"\bhalf(?:way)?\b", low):
        out["percent"] = 50
    elif re.search(r"\b(?:nearly|almost) (?:done|finished|there)\b", low):
        out["percent"] = 90
    m = re.match(r"\s*(?:blocked|stuck)\s*[:\-–]\s*(.+)", first, re.I | re.S)
    if m:
        out["status"], out["note"] = "blocked", m.group(1).strip()
    elif re.search(BLOCKED, low) and not re.search(r"\bno (?:problem|issue)s?\b", low):
        out["status"], out["note"] = "blocked", first
    elif out["percent"] is None and re.search(r"\b(?:nearly|almost|not)\s+(?:done|finished)\b", low):
        out["status"] = "doing"
    elif re.search(DONE, low):
        out["status"] = "done"
    elif re.search(DOING, low):
        out["status"] = "doing"
    if out["percent"] == 100:
        out["status"] = "done"
    if not out["note"]:
        rest = re.sub(DONE + "|" + DOING + r"|\b\d{1,3}\s*(?:%|percent)", "", first, flags=re.I).strip(" .,!-:")
        out["note"] = rest[:500] if len(rest) > 2 else ""
    out["understood"] = bool(out["status"] or out["percent"] is not None)
    if not out["understood"] and first:
        out["note"] = first[:500]
    return out
