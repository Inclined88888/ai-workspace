"""Legal pack: clause library, redlines, defined-term / cross-reference / citation checks and matter timelines
(feat/v02-packs). Every result carries its sources ([file, location]) and the not-legal-advice notice.

Checks are deterministic text rules, so they run offline and give the same answer twice. Citation checks look at the
format only (they cannot confirm a case exists); each citation gets a link to search a free public source.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from urllib.parse import quote_plus

from fastapi import HTTPException

from .. import analysis, db, files
from . import registry, store

PACK = "legal"
DEFAULTS = {"jurisdiction": "AU", "day_first": True, "citation_style": "AGLC"}
NOTICE = ("This is general information to help you review your documents. It is not legal advice. Check every point against "
          "the original and get advice from a qualified lawyer before you rely on it.")


def settings() -> dict:
    return store.settings(PACK, DEFAULTS)


def save_settings(data: dict) -> dict:
    cur = settings()
    cur.update({k: v for k, v in data.items() if k in DEFAULTS})
    store.save_settings(PACK, cur)
    return cur


# --- clause library ---------------------------------------------------------------------------------------------------
def clauses(q: str = "", tag: str = "") -> list[dict]:
    store.ensure()
    rows = db.all("SELECT * FROM legal_clauses ORDER BY uses DESC, updated_at DESC LIMIT 500")
    words = [w.lower() for w in re.findall(r"\w+", q or "")]
    out = []
    for r in rows:
        hay = f"{r['title']} {r['body']} {r['tags']}".lower()
        if words and not all(w in hay for w in words):
            continue
        if tag and tag.lower() not in [t.strip().lower() for t in r["tags"].split(",")]:
            continue
        out.append(r)
    return out


def save_clause(data: dict, cid: int | None = None) -> dict:
    registry.require(PACK)
    title, body = str(data.get("title") or "").strip(), str(data.get("body") or "").strip()
    if not title or not body:
        raise HTTPException(400, "A clause needs a title and its text.")
    tags = ", ".join(t.strip() for t in str(data.get("tags") or "").split(",") if t.strip())[:300]
    source = str(data.get("source") or "typed")[:300]
    if cid:
        clause(cid)
        db.run("UPDATE legal_clauses SET title = ?, body = ?, tags = ?, source = ?, updated_at = ? WHERE id = ?",
               title[:200], body[:20000], tags, source, db.now(), cid)
    else:
        cid = db.run("INSERT INTO legal_clauses (title, body, tags, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                     title[:200], body[:20000], tags, source, db.now(), db.now())
    return clause(cid)


def clause(cid: int) -> dict:
    store.ensure()
    r = db.one("SELECT * FROM legal_clauses WHERE id = ?", int(cid))
    if not r:
        raise HTTPException(404, "No such clause")
    return r


def use_clause(cid: int) -> dict:
    c = clause(cid)
    db.run("UPDATE legal_clauses SET uses = uses + 1 WHERE id = ?", cid)
    return {**c, "uses": c["uses"] + 1}


def delete_clause(cid: int) -> dict:
    clause(cid)
    db.run("DELETE FROM legal_clauses WHERE id = ?", cid)
    return {"ok": True}


# --- text from a file or pasted text ------------------------------------------------------------------------------------
def passages(file_id: int | None = None, text: str = "") -> tuple[str, list[tuple[str, str]]]:
    if file_id:
        d = analysis.extract_of(int(file_id))
        out = []
        for x in files.walk(d):
            for c in x["chunks"]:
                for i, para in enumerate(re.split(r"\n\s*\n", c["text"])):
                    if para.strip():
                        out.append((f"{c['loc']}" + (f", part {i + 1}" if i else ""), para.strip()))
        return d["name"], out
    if not text.strip():
        raise HTTPException(400, "Pick a file or paste the text.")
    return "pasted text", [(f"paragraph {i + 1}", p.strip()) for i, p in enumerate(re.split(r"\n\s*\n", text)) if p.strip()]


# --- defined terms --------------------------------------------------------------------------------------------------------
Q = r"[\"“”'‘’]"
DEF_MEANS = re.compile(Q + r"([A-Z][\w&'’\- ]{0,60}?)" + Q + r"\s+(?:means|has the meaning|includes|refers to|shall mean|is defined)", re.U)
DEF_PAREN = re.compile(r"\((?:the|each|together|collectively|individually)?[\s,]*(?:the\s+|a\s+|an\s+)?(?:\w+\s+)?" + Q + r"([A-Z][\w&'’\- ]{0,60}?)" + Q + r"\)", re.U)
MID_CAPS = re.compile(r"(?<=[a-z,;:)] )(?:the |this |that |each |any |such |a |an |of |by |to |under |in |with )?((?:[A-Z][a-z]+)(?: (?:[A-Z][a-z]+|of|and))*(?: [A-Z][a-z]+)?)\b")
COMMON = {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December",
          "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Australia", "Australian", "Commonwealth",
          "Act", "Court", "Schedule", "Clause", "Section", "Part", "Annexure", "Appendix", "Item", "English", "Mr", "Mrs", "Ms", "Dr",
          "Pty", "Ltd", "Limited", "Inc", "New", "South", "Wales", "Victoria", "Queensland", "Western", "Tasmania", "Northern",
          "Territory", "Capital", "Zealand", "United", "Kingdom", "States", "I", "The", "This", "If", "GST", "ABN", "ACN"}


def defined_terms(paras: list[tuple[str, str]], name: str) -> dict:
    defs: dict[str, str] = {}
    for loc, t in paras:
        for rx in (DEF_MEANS, DEF_PAREN):
            for m in rx.finditer(t):
                term = m.group(1).strip()
                if 1 <= len(term) <= 60 and term not in defs:
                    defs[term] = loc
    full = "\n".join(t for _, t in paras)
    usage = {}
    for term, loc in defs.items():
        n = len(re.findall(r"(?<![\w])" + re.escape(term) + r"(?![\w])", full))
        usage[term] = n - 1  # the definition itself
    candidates: Counter = Counter()
    where: dict[str, str] = {}
    with_article: set[str] = set()

    def seen(phrase: str, loc: str, article: bool) -> None:
        if phrase in COMMON or all(w in COMMON or w in ("of", "and") for w in phrase.split()):
            return
        if any(phrase == d or phrase in d.split() or d in phrase for d in defs):
            return
        candidates[phrase] += 1
        where.setdefault(phrase, loc)
        if article:
            with_article.add(phrase)

    for loc, t in paras:
        for m in MID_CAPS.finditer(t):
            phrase = re.sub(r" (of|and)$", "", m.group(1).strip())
            article = bool(re.search(r"\bthe $", t[max(0, m.start(1) - 4): m.start(1)]))
            seen(phrase, loc, article)
            words = [w for w in phrase.split() if w not in ("of", "and")]
            if len(words) > 1:  # "the Client Access": also count Client and Access on their own
                for j, w in enumerate(words):
                    seen(w, loc, article and j == 0)
    # Used twice, or once as "the Portal": that reads like a defined term.
    undefined = [{"term": p, "uses": n, "first_used": f"[{name}, {where[p]}]"} for p, n in candidates.most_common()
                 if n >= 2 or (p in with_article and " " not in p)][:40]
    return {
        "defined": [{"term": t, "defined_at": f"[{name}, {loc}]", "uses": usage[t]} for t, loc in defs.items()],
        "used_not_defined": undefined,
        "defined_not_used": [{"term": t, "defined_at": f"[{name}, {defs[t]}]"} for t, n in usage.items() if n <= 0],
        "note": "Capitalised words used like defined terms but never defined. Some may be names or headings; check each one.",
    }


# --- cross references --------------------------------------------------------------------------------------------------
HEADING_NUM = re.compile(r"^\s*(?:clause\s+|section\s+)?(\d+(?:\.\d+)*)[.)]?\s+\S", re.I | re.M)
HEADING_SCHED = re.compile(r"^\s*(schedule|annexure|appendix|exhibit|attachment)\s+([A-Z0-9]+)\b", re.I | re.M)
XREF = re.compile(r"\b(clauses?|cl\.|sections?|paragraphs?|schedules?|annexures?|appendix|appendices|exhibits?|attachments?)\s+"
                  r"((?:\d+(?:\.\d+)*|[A-Z])(?:\([a-z0-9]+\))*)", re.I)


def cross_refs(paras: list[tuple[str, str]], name: str) -> dict:
    full = "\n".join(t for _, t in paras)
    nums = set(HEADING_NUM.findall(full))
    # numbered paragraphs inside one chunk: "12.3 The Supplier..." anywhere at a line start
    scheds = {(k.lower().rstrip("s"), v.upper()) for k, v in HEADING_SCHED.findall(full)}
    broken, ok, external = [], 0, 0
    for loc, t in paras:
        for m in XREF.finditer(t):
            kind, target = m.group(1).lower().rstrip("."), m.group(2)
            after = t[m.end(): m.end() + 60]
            if re.match(r"\s*(?:\([a-z0-9]+\)\s*)*(?:of|in|under)\s+(?:the\s+)?[A-Z][\w ]*(?:Act|Regulations?|Code|Rules|Law)\b", after):
                external += 1
                continue
            base = re.sub(r"\(.*", "", target)
            if kind.startswith(("schedule", "annexure", "appendi", "exhibit", "attachment")):
                k = "appendix" if kind.startswith("appendi") else kind.rstrip("s")
                found = (k, base.upper()) in scheds
            else:
                if not nums:
                    continue
                found = base in nums
            if found:
                ok += 1
            else:
                broken.append({"reference": m.group(0), "where": f"[{name}, {loc}]",
                               "problem": f"No {kind.rstrip('s')} {base} in this document"})
    return {"checked": ok + len(broken), "ok": ok, "broken": broken[:100], "external_skipped": external,
            "note": "" if nums else "This document has no numbered clauses that could be read, so clause references were not checked."}


# --- citations ------------------------------------------------------------------------------------------------------------
COURTS = {"HCA", "FCA", "FCAFC", "FamCA", "FamCAFC", "FCCA", "FedCFamC1F", "FedCFamC1A", "FedCFamC2F", "FedCFamC2G", "AATA", "ART",
          "NSWSC", "NSWCA", "NSWCCA", "NSWLEC", "NSWDC", "NSWCATAP", "NSWCATCD", "VSC", "VSCA", "VCC", "VCAT", "QSC", "QCA", "QDC",
          "QCAT", "WASC", "WASCA", "WADC", "SASC", "SASCFC", "SASCA", "SADC", "TASSC", "TASFC", "TASCCA", "ACTSC", "ACTCA", "NTSC",
          "NTCA", "FWC", "FWCFB", "NZSC", "NZCA", "NZHC", "UKSC", "UKPC", "UKHL", "EWCA", "EWHC", "CSIH", "CSOH", "SCC", "ONCA", "IESC"}
REPORTS = {"CLR", "ALR", "ALJR", "FCR", "FLR", "NSWLR", "VR", "Qd R", "WAR", "SASR", "Tas R", "ACTR", "NTR", "IR", "AC", "QB", "KB",
           "Ch", "WLR", "All ER", "NZLR", "US", "S Ct", "F 2d", "F 3d", "F 4th", "SCR", "DLR", "ALD", "AILR", "IPR", "ACSR", "ACLC"}
NEUTRAL = re.compile(r"\[(\d{4})\]\s+([A-Za-z][A-Za-z0-9]{1,11})\s+(\d{1,5})\b")
REPORTED = re.compile(r"[(\[](\d{4})[)\]]\s+(?:(\d{1,4})\s+)?(Qd R|Tas R|All ER|S Ct|F [234]d|F 4th|[A-Z][A-Za-z]{0,6})\s+(\d{1,5})\b")
ACT = re.compile(r"\b((?:[A-Z][A-Za-z'’]*(?:\s+|\s+(?:of|and|for|the|to|on|in)\s+)){0,10}?(?:Act|Regulations?|Rules|Code))\s+(\d{4})"
                 r"(?:\s*\((Cth|NSW|Vic|Qld|WA|SA|Tas|ACT|NT|NZ|UK|Imp)\))?")
YEAR_NOW = date.today().year


def _source_link(text: str, jurisdiction: str) -> str:
    if jurisdiction in ("UK",):
        return f"https://www.bailii.org/cgi-bin/lucy_search_1.cgi?query={quote_plus(text)}"
    if jurisdiction == "NZ":
        return f"https://www.nzlii.org/cgi-bin/sinosrch.cgi?query={quote_plus(text)}"
    return f"https://www.austlii.edu.au/cgi-bin/sinosrch.cgi?query={quote_plus(text)}"


def citations(paras: list[tuple[str, str]], name: str) -> dict:
    s = settings()
    out = []
    seen = set()
    for loc, t in paras:
        for m in NEUTRAL.finditer(t):
            year, court, num = int(m.group(1)), m.group(2), m.group(3)
            if court in REPORTS:
                continue
            problems = []
            if court not in COURTS:
                problems.append(f"'{court}' is not a court abbreviation this check knows; check the spelling")
            if year > YEAR_NOW or year < 1990:
                problems.append(f"{year} looks wrong for a medium neutral citation")
            key = m.group(0)
            if key not in seen:
                seen.add(key)
                out.append({"kind": "case (medium neutral)", "citation": key, "where": f"[{name}, {loc}]", "problems": problems,
                            "source": _source_link(key, s["jurisdiction"])})
        for m in REPORTED.finditer(t):
            series = m.group(3)
            if series in COURTS or series not in REPORTS:
                continue
            year = int(m.group(1))
            problems = [] if 1800 <= year <= YEAR_NOW else [f"{year} looks wrong"]
            if series in ("CLR", "ALR", "FCR", "NSWLR", "VR", "WAR", "SASR", "US") and not m.group(2):
                problems.append(f"{series} citations need a volume number before the series")
            if series == "CLR" and t[m.start()] == "[":
                problems.append("CLR uses round brackets for the year, e.g. (1992) 175 CLR 1")
            key = m.group(0)
            if key not in seen:
                seen.add(key)
                out.append({"kind": "case (reported)", "citation": key, "where": f"[{name}, {loc}]", "problems": problems,
                            "source": _source_link(key, s["jurisdiction"])})
        for m in ACT.finditer(t):
            title = re.sub(r"^(?:The|the|Under|under|In|in|Of|of|By|by)\s+", "", m.group(1).strip())
            if len(title.split()) < 2:
                continue
            key = f"{title} {m.group(2)}"
            problems = []
            if s["jurisdiction"] == "AU" and s["citation_style"] == "AGLC" and not m.group(3):
                problems.append("AGLC style puts the jurisdiction after the year, e.g. (Cth) or (NSW)")
            if int(m.group(2)) > YEAR_NOW:
                problems.append(f"{m.group(2)} is in the future")
            if key not in seen:
                seen.add(key)
                out.append({"kind": "legislation", "citation": m.group(0), "where": f"[{name}, {loc}]", "problems": problems,
                            "source": _source_link(key, s["jurisdiction"])})
    return {"citations": out[:200], "with_problems": sum(1 for c in out if c["problems"]),
            "note": "Format check only: it cannot confirm a case or Act exists or says what is claimed. Open each source link to check."}


def check_document(file_id: int | None = None, text: str = "") -> dict:
    registry.require(PACK)
    name, paras = passages(file_id, text)
    terms, refs, cites = defined_terms(paras, name), cross_refs(paras, name), citations(paras, name)
    issues = len(terms["used_not_defined"]) + len(terms["defined_not_used"]) + len(refs["broken"]) + cites["with_problems"]
    return {"document": name, "summary": f"{len(terms['defined'])} defined terms, {len(terms['used_not_defined'])} possibly used but not defined, "
                                         f"{len(terms['defined_not_used'])} defined but not used; {refs['checked']} cross-references "
                                         f"({len(refs['broken'])} pointing nowhere); {len(cites['citations'])} citations "
                                         f"({cites['with_problems']} to check).",
            "issues": issues, "defined_terms": terms, "cross_references": refs, "citations": cites, "not_legal_advice": NOTICE,
            "cite_as": "Every finding shows [document, location]."}


def compare(a: int, b: int, report: bool = True) -> dict:
    """Reuses the Files compare (paragraph and word level redline), and makes a Word redline with the notice."""
    registry.require(PACK)
    res = analysis.compare(a, b)
    if report:
        res["report"] = analysis.make_word(f"Redline {res['a']} to {res['b']}", f"{res['summary']}\n\n{NOTICE}", compare_with=res)
    res["not_legal_advice"] = NOTICE
    return res


# --- matter timeline -------------------------------------------------------------------------------------------------------
MONTHS = {m.lower(): i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September",
                                              "October", "November", "December"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MON = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
D_MY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + MON + r"\.?,?\s+(\d{4})\b", re.I)
MD_Y = re.compile(r"\b" + MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2})\b")
ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _sentence(t: str, start: int, end: int) -> str:
    a = max(t.rfind(". ", 0, start), t.rfind("\n", 0, start))
    b_candidates = [x for x in (t.find(". ", end), t.find("\n", end)) if x != -1]
    b = min(b_candidates) if b_candidates else len(t)
    s = t[a + 1: b + 1].strip()
    return s if len(s) <= 300 else (t[max(0, start - 140): end + 140].strip() + "…")


def find_dates(t: str, day_first: bool) -> list[tuple[date, int, int, bool]]:
    found = []
    taken: list[tuple[int, int]] = []

    def add(d: date | None, m, ambiguous=False):
        if d and not any(m.start() < e and m.end() > s for s, e in taken):
            taken.append((m.start(), m.end()))
            found.append((d, m.start(), m.end(), ambiguous))

    def mk(y, mo, dd):
        try:
            return date(int(y), int(mo), int(dd))
        except ValueError:
            return None

    for m in ISO.finditer(t):
        add(mk(m.group(1), m.group(2), m.group(3)), m)
    for m in D_MY.finditer(t):
        add(mk(m.group(3), MONTHS[m.group(2).lower()[:3]], m.group(1)), m)
    for m in MD_Y.finditer(t):
        add(mk(m.group(3), MONTHS[m.group(1).lower()[:3]], m.group(2)), m)
    for m in NUM.finditer(t):
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(y) + (2000 if int(y) < 70 else 1900) if len(y) == 2 else int(y)
        dd, mo = (a, b) if day_first else (b, a)
        add(mk(y, mo, dd), m, ambiguous=a <= 12 and b <= 12 and a != b)
    return found


def timeline(file_ids: list[int] | None = None, text: str = "", day_first: bool | None = None) -> dict:
    registry.require(PACK)
    df = settings()["day_first"] if day_first is None else day_first
    entries = []
    sources = [(fid, "") for fid in (file_ids or [])] or [(None, text)]
    for fid, txt in sources:
        name, paras = passages(fid, txt)
        for loc, t in paras:
            for d, s, e, amb in find_dates(t, df):
                entries.append({"date": d.isoformat(), "event": _sentence(t, s, e), "as_written": t[s:e], "source": f"[{name}, {loc}]",
                                "ambiguous": amb})
    seen, out = set(), []
    for x in sorted(entries, key=lambda x: x["date"]):
        k = (x["date"], x["event"][:80])
        if k not in seen:
            seen.add(k)
            out.append(x)
    return {"entries": out[:1000], "day_first": df, "ambiguous": sum(1 for x in out if x["ambiguous"]),
            "note": ("Dates like 03/04/2025 are read " + ("day first (3 April)" if df else "month first (March 4)") +
                     "; those that could be either are marked."), "not_legal_advice": NOTICE}


def timeline_report(file_ids: list[int] | None = None, text: str = "") -> dict:
    t = timeline(file_ids, text)
    body = "\n".join(f"- **{x['date']}** {x['event']} {x['source']}" + (" (date could be read either way)" if x["ambiguous"] else "")
                     for x in t["entries"])
    return {**analysis.make_word("Matter timeline", f"{t['note']}\n\n{body}\n\n{NOTICE}"), "entries": len(t["entries"])}
