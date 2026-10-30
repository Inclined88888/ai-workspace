"""Study & research pack: flashcards with spaced repetition, worked maths checked by sympy, references (BibTeX, RIS,
Zotero) and a lab notebook (feat/v02-packs). Free for students; the lab notebook is Pro (features_v02.FEATURE_TIERS).

Maths runs inside the Files sandbox (compute.run: no network, no access to the user's files), so a typed expression is
never evaluated in the app's own process.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import HTTPException

from .. import analysis, compute, db, files
from . import ai, registry, store

PACK = "study"
LAB = "packs.study.lab_notebook"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- flashcards --------------------------------------------------------------------------------------------------------
CARD_PROMPT = (
    "Make study flashcards from the material. Each card tests one fact, definition, formula, date or idea; the front is a "
    "short question, the back a short exact answer taken from the material. Keep the source tag in [brackets] that comes "
    'before each passage. Reply with only JSON: {{"cards": [{{"front": "...", "back": "...", "source": "[file, location]"}}]}}. '
    "Up to {n} cards, most important first."
)
DEF_LINE = re.compile(r"^\s*(?:[-*•]\s*)?([A-Z][\w ()'’/-]{1,60}?)\s*(?::|\s[-–—]\s|\bmeans\b|\bis defined as\b)\s*(.{3,400})$")
IS_SENT = re.compile(r"(?:^|(?<=[.!?]\s))((?:The |An? )?[A-Z][\w'’-]*(?:\s+[\w'’-]+){0,4}?)\s+(is|are|was|were)\s+((?:a|an|the)\s+[^.]{3,240})\.")


def rule_cards(paras: list[tuple[str, str]], name: str, n: int) -> list[dict]:
    cards, seen = [], set()
    for loc, t in paras:
        for line in t.splitlines():
            m = DEF_LINE.match(line.strip())
            if m and m.group(1).lower() not in seen:
                seen.add(m.group(1).lower())
                cards.append({"front": f"What is {m.group(1).strip()}?", "back": m.group(2).strip(), "source": f"[{name}, {loc}]"})
        for m in IS_SENT.finditer(t):
            subj = m.group(1).strip()
            if subj.lower() in seen or len(subj) < 3:
                continue
            seen.add(subj.lower())
            cards.append({"front": f"What {m.group(2)} {subj[0].lower() + subj[1:] if subj.split()[0] in ('The', 'A', 'An') else subj}?",
                          "back": m.group(3).strip(), "source": f"[{name}, {loc}]"})
        if len(cards) >= n:
            break
    return cards[:n]


def _paras(file_id: int | None, text: str) -> tuple[str, list[tuple[str, str]]]:
    if file_id:
        d = analysis.extract_of(int(file_id))
        return d["name"], [(c["loc"], c["text"]) for x in files.walk(d) for c in x["chunks"] if c["text"].strip()]
    if not text.strip():
        raise HTTPException(400, "Pick a file or paste some notes.")
    return "notes", [(f"paragraph {i + 1}", p) for i, p in enumerate(re.split(r"\n\s*\n", text)) if p.strip()]


async def make_cards(file_id: int | None = None, text: str = "", deck: str = "", n: int = 20) -> dict:
    registry.require(PACK)
    store.ensure()
    n = max(1, min(int(n or 20), 60))
    name, paras = _paras(file_id, text)
    deck = (deck or name.rsplit(".", 1)[0] or "General").strip()[:80]
    material = "\n\n".join(f"[{name}, {loc}] {t}" for loc, t in paras)[:24000]
    data = await ai.try_json(CARD_PROMPT.format(n=n), material, "study-cards")
    raw = data.get("cards") if isinstance(data, dict) else data if isinstance(data, list) else None
    cards = [c for c in (raw or []) if isinstance(c, dict) and str(c.get("front", "")).strip() and str(c.get("back", "")).strip()]
    via = "model"
    if not cards:
        via = "rules"
        cards = rule_cards(paras, name, n)
    made = []
    due = _now().isoformat(timespec="seconds")
    for c in cards[:n]:
        cid = db.run("INSERT INTO study_cards (deck, front, back, source, due_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     deck, str(c["front"]).strip()[:1000], str(c["back"]).strip()[:4000], str(c.get("source") or f"[{name}]")[:300], due, db.now())
        made.append(card(cid))
    return {"deck": deck, "made": len(made), "cards": made, "via": via,
            "note": "" if made else "No facts or definitions could be picked out. Try a file with notes or definitions, or set up a model."}


def card(cid: int) -> dict:
    store.ensure()
    r = db.one("SELECT * FROM study_cards WHERE id = ?", int(cid))
    if not r:
        raise HTTPException(404, "No such card")
    return r


def decks() -> list[dict]:
    store.ensure()
    now = _now().isoformat(timespec="seconds")
    return db.all("SELECT deck, COUNT(*) AS cards, SUM(CASE WHEN due_at <= ? THEN 1 ELSE 0 END) AS due FROM study_cards GROUP BY deck ORDER BY deck", now)


def due(deck: str = "", limit: int = 50) -> list[dict]:
    store.ensure()
    now = _now().isoformat(timespec="seconds")
    if deck:
        return db.all("SELECT * FROM study_cards WHERE deck = ? AND due_at <= ? ORDER BY due_at LIMIT ?", deck, now, limit)
    return db.all("SELECT * FROM study_cards WHERE due_at <= ? ORDER BY due_at LIMIT ?", now, limit)


GRADES = {"again": 1, "hard": 3, "good": 4, "easy": 5}


def sm2(ease: float, interval: float, reps: int, q: int) -> tuple[float, float, int]:
    """SuperMemo-2. q: 0-5. Returns (ease, interval_days, reps). 'Again' brings the card back in ten minutes."""
    if q < 3:
        return max(1.3, ease - 0.2), 10 / (24 * 60), 0
    ease = max(1.3, ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
    reps += 1
    if reps == 1:
        interval = 1
    elif reps == 2:
        interval = 6
    else:
        interval = round(interval * ease, 2)
    if q == 3:
        interval = max(1, round(interval * 0.8, 2)) if reps > 2 else interval
    if q == 5 and reps > 1:
        interval = round(interval * 1.3, 2)
    return round(ease, 3), interval, reps


def review(cid: int, grade: str) -> dict:
    c = card(cid)
    if grade not in GRADES:
        raise HTTPException(400, "Grade is again, hard, good or easy")
    ease, interval, reps = sm2(c["ease"], c["interval_days"], c["reps"], GRADES[grade])
    nxt = _now() + timedelta(days=interval)
    db.run("UPDATE study_cards SET ease = ?, interval_days = ?, reps = ?, lapses = lapses + ?, due_at = ?, last_reviewed_at = ? WHERE id = ?",
           ease, interval, reps, 1 if grade == "again" else 0, nxt.isoformat(timespec="seconds"), db.now(), cid)
    return {**card(cid), "next_in": _human(interval)}


def _human(days: float) -> str:
    if days < 1 / 24:
        return f"{round(days * 24 * 60)} minutes"
    if days < 1:
        return f"{round(days * 24)} hours"
    return f"{round(days)} day" + ("s" if round(days) != 1 else "")


def update_card(cid: int, data: dict) -> dict:
    c = card(cid)
    db.run("UPDATE study_cards SET front = ?, back = ?, deck = ? WHERE id = ?", str(data.get("front", c["front"]))[:1000],
           str(data.get("back", c["back"]))[:4000], str(data.get("deck", c["deck"]))[:80], cid)
    return card(cid)


def delete_card(cid: int) -> dict:
    card(cid)
    db.run("DELETE FROM study_cards WHERE id = ?", cid)
    return {"ok": True}


# --- maths ---------------------------------------------------------------------------------------------------------------
TASKS = ("auto", "solve", "simplify", "factor", "expand", "differentiate", "integrate", "limit", "evaluate")
MATHS_CODE = r'''
import json, sys
import sympy as sp
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application, convert_xor
REQ = json.loads(%(req)r)
T = standard_transformations + (implicit_multiplication_application, convert_xor)
NS = {"e": sp.E, "pi": sp.pi, "i": sp.I, "ln": sp.log, "sqrt": sp.sqrt, "abs": sp.Abs}

def delatex(s):
    import re
    s = s.strip().strip("$")
    for a, b in ((r"\left", ""), (r"\right", ""), (r"\cdot", "*"), (r"\times", "*"), (r"\div", "/"), (r"\pi", "pi"), (r"\ln", "ln"),
                 (r"\sin", "sin"), (r"\cos", "cos"), (r"\tan", "tan"), (r"\exp", "exp"), (r"\infty", "oo"), ("{", "("), ("}", ")")):
        s = s.replace(a, b)
    while r"\frac" in s:
        s = re.sub(r"\\frac\s*(\((?:[^()]|\([^()]*\))*\))\s*(\((?:[^()]|\([^()]*\))*\))", r"(\1)/(\2)", s, count=1)
        if r"\frac(" not in s and r"\frac" in s:
            break
    s = re.sub(r"\\sqrt\s*(\((?:[^()]|\([^()]*\))*\))", r"sqrt\1", s)
    return s.replace("\\", "")

def P(s):
    return parse_expr(delatex(s), transformations=T, local_dict=NS)

def L(x):
    return sp.latex(x)

def parse_side(s):
    if "=" in s:
        a, b = s.split("=", 1)
        return sp.Eq(P(a), P(b))
    return P(s)

def main():
    task, src = REQ["task"], REQ["expression"]
    steps = []
    e = parse_side(src)
    syms = sorted(e.free_symbols, key=lambda s: s.name)
    var = sp.Symbol(REQ["variable"]) if REQ.get("variable") else (syms[0] if syms else sp.Symbol("x"))
    if task == "auto":
        task = "solve" if isinstance(e, sp.Equality) else "simplify"
    steps.append({"say": "Start with", "latex": L(e)})
    answer, check = None, None
    if task == "solve":
        eq = e if isinstance(e, sp.Equality) else sp.Eq(e, 0)
        expr = sp.expand(eq.lhs - eq.rhs)
        steps.append({"say": "Move everything to one side", "latex": L(sp.Eq(expr, 0))})
        f = sp.factor(expr)
        if f != expr:
            steps.append({"say": "Factorise", "latex": L(sp.Eq(f, 0))})
            if isinstance(f, sp.Mul):
                steps.append({"say": "A product is zero when one of its factors is zero",
                              "latex": r" \quad\text{or}\quad ".join(L(sp.Eq(g, 0)) for g in f.args if g.free_symbols)})
        sols = sp.solve(eq, var)
        answer = sols
        steps.append({"say": "Solve for " + str(var), "latex": ", ".join(L(sp.Eq(var, s)) for s in sols) or r"\text{no solution}"})
        ok = all(sp.simplify(eq.lhs.subs(var, s) - eq.rhs.subs(var, s)) == 0 for s in sols)
        check = {"ok": bool(ok), "how": "Each answer substituted back into the original equation"}
        for s in sols:
            import re
            sub_re = r"(?<![\\A-Za-z])" + re.escape(L(var)) + r"(?![A-Za-z])"
            shown = [re.sub(sub_re, lambda _m: r"\left(" + L(s) + r"\right)", L(side)) for side in (eq.lhs, eq.rhs)]
            vals = [L(sp.simplify(side.subs(var, s))) for side in (eq.lhs, eq.rhs)]
            parts = [a + ("" if a == b else " = " + b) for a, b in zip(shown, vals)]
            steps.append({"say": "Check " + str(var) + " = " + str(s) + " in the original",
                          "latex": r"\text{left: }" + parts[0] + r"\qquad \text{right: }" + parts[1]})
    elif task in ("simplify", "factor", "expand", "evaluate"):
        fn = {"simplify": sp.simplify, "factor": sp.factor, "expand": sp.expand, "evaluate": lambda x: sp.N(x, 12)}[task]
        if task == "simplify":
            ex = sp.expand(e)
            if ex != e:
                steps.append({"say": "Expand", "latex": L(ex)})
        answer = fn(e)
        steps.append({"say": task.capitalize(), "latex": L(answer)})
        check = {"ok": bool(sp.simplify(e - answer) == 0) if task != "evaluate" else bool(abs(sp.N(e) - answer) < 1e-9),
                 "how": "The answer minus the original simplifies to 0" if task != "evaluate" else "Compared numerically"}
    elif task == "differentiate":
        steps.append({"say": "Differentiate with respect to " + str(var), "latex": r"\frac{d}{d" + str(var) + "}" + r"\left(" + L(e) + r"\right)"})
        d = sp.diff(e, var)
        steps.append({"say": "Apply the rules term by term", "latex": L(d)})
        answer = sp.simplify(d)
        if answer != d:
            steps.append({"say": "Simplify", "latex": L(answer)})
        h = sp.Symbol("h")
        num = sp.limit((e.subs(var, var + h) - e) / h, h, 0) if len(str(e)) < 200 else None
        check = {"ok": bool(num is None or sp.simplify(num - answer) == 0), "how": "Checked against the limit definition of the derivative" if num is not None else "Not checked"}
    elif task == "integrate":
        steps.append({"say": "Integrate with respect to " + str(var), "latex": r"\int " + L(e) + r"\, d" + str(var)})
        F = sp.integrate(e, var)
        answer = F
        steps.append({"say": "Antiderivative", "latex": L(F) + " + C"})
        check = {"ok": bool(sp.simplify(sp.diff(F, var) - e) == 0), "how": "Differentiating the answer gives back the original"}
    elif task == "limit":
        to = P(REQ.get("to") or "0")
        answer = sp.limit(e, var, to)
        steps.append({"say": "Take the limit as " + str(var) + " approaches " + str(to), "latex": r"\lim_{" + str(var) + r" \to " + L(to) + "} " + L(e) + " = " + L(answer)})
        try:
            near = [sp.N(e.subs(var, to + d)) for d in (sp.Rational(1, 1000), -sp.Rational(1, 1000))] if to.is_finite else []
            check = {"ok": bool(not near or all(abs(n - answer) < 1e-2 for n in near)) if answer.is_finite else True, "how": "Compared with values just either side"}
        except Exception:
            check = {"ok": True, "how": "Not checked numerically"}
    print(json.dumps({"task": task, "steps": steps, "answer": str(answer), "answer_latex": L(answer) if not isinstance(answer, list) else ", ".join(L(a) for a in answer),
                      "check": check, "variable": str(var)}))

def check_steps():
    lines = [l for l in REQ["lines"] if l.strip()]
    out = []
    prev = None
    for i, line in enumerate(lines):
        cur = parse_side(line)
        res = {"line": i + 1, "latex": L(cur), "ok": True, "why": ""}
        if prev is not None:
            if isinstance(prev, sp.Equality) and isinstance(cur, sp.Equality):
                vs = sorted((prev.free_symbols | cur.free_symbols), key=lambda s: s.name)
                v = vs[0] if vs else sp.Symbol("x")
                a, b = set(sp.solve(prev, v)), set(sp.solve(cur, v))
                res["ok"] = a == b
                if not res["ok"]:
                    res["why"] = "This changes the solutions from " + str(sorted(map(str, a))) + " to " + str(sorted(map(str, b)))
            elif isinstance(prev, sp.Equality) or isinstance(cur, sp.Equality):
                res["ok"], res["why"] = True, "Moved between an equation and an expression; not compared"
            else:
                d = sp.simplify(prev - cur)
                res["ok"] = d == 0
                if not res["ok"]:
                    res["why"] = "This is not equal to the line before (they differ by " + str(d) + ")"
        out.append(res)
        prev = cur
    first = next((r["line"] for r in out if not r["ok"]), None)
    print(json.dumps({"lines": out, "first_wrong": first, "all_ok": first is None}))

try:
    check_steps() if REQ.get("mode") == "check" else main()
except Exception as ex:
    print(json.dumps({"error": "I couldn't read that as maths: " + str(ex)[:300]}))
'''


def _run_maths(req: dict) -> dict:
    res = compute.run(MATHS_CODE % {"req": json.dumps(req)}, [], timeout=40)
    out = (res.get("stdout") or "").strip().splitlines()
    try:
        data = json.loads(out[-1]) if out else {}
    except ValueError:
        data = {}
    if not data:
        raise HTTPException(500, "The maths checker didn't answer. " + (res.get("error") or "")[:300])
    if data.get("error"):
        raise HTTPException(400, data["error"])
    data["isolation"] = res.get("isolation")
    return data


def maths(expression: str, task: str = "auto", variable: str = "", to: str = "") -> dict:
    registry.require(PACK)
    if task not in TASKS:
        raise HTTPException(400, f"Task is one of {', '.join(TASKS)}")
    if not expression.strip() or len(expression) > 2000:
        raise HTTPException(400, "Type the maths to work on (up to 2000 characters).")
    if variable and not re.fullmatch(r"[A-Za-z]\w{0,10}", variable):
        raise HTTPException(400, "The variable should be a name like x or t.")
    return _run_maths({"task": task, "expression": expression, "variable": variable, "to": to})


def check_steps(lines: list[str]) -> dict:
    registry.require(PACK)
    lines = [str(x)[:500] for x in lines if str(x).strip()][:40]
    if len(lines) < 2:
        raise HTTPException(400, "Give at least two lines of working.")
    return _run_maths({"mode": "check", "lines": lines})


# --- references ------------------------------------------------------------------------------------------------------------
REF_TYPES = ("article", "book", "chapter", "conference", "report", "thesis", "web", "dataset", "other")


def _clean_ref(d: dict) -> dict:
    authors = []
    for a in d.get("authors") or []:
        if isinstance(a, str):
            parts = [p.strip() for p in (a.split(",", 1) if "," in a else a.rsplit(" ", 1)[::-1])]
            authors.append({"family": parts[0], "given": parts[1] if len(parts) > 1 else ""})
        elif isinstance(a, dict) and (a.get("family") or a.get("literal")):
            authors.append({"family": str(a.get("family") or a.get("literal")), "given": str(a.get("given") or "")})
    out = {k: str(d.get(k) or "").strip()[:1000] for k in ("title", "container", "volume", "issue", "pages", "doi", "url", "publisher", "year", "note")}
    out["type"] = d.get("type") if d.get("type") in REF_TYPES else "article"
    out["authors"] = authors[:50]
    if not out["title"]:
        raise HTTPException(400, "A reference needs at least a title.")
    return out


def _cite_key(r: dict) -> str:
    fam = re.sub(r"\W", "", (r["authors"][0]["family"] if r["authors"] else r["title"].split()[0])).lower() or "ref"
    word = next((w for w in re.findall(r"[A-Za-z]{4,}", r["title"]) if w.lower() not in ("this", "that", "with", "from", "into")), "")
    return f"{fam}{r['year']}{word.lower()}"[:40]


def add_ref(data: dict) -> dict:
    registry.require(PACK)
    store.ensure()
    r = _clean_ref(data)
    key = _cite_key(r)
    base, n = key, 1
    while db.one("SELECT id FROM study_refs WHERE key = ?", key):
        n += 1
        key = f"{base}{chr(96 + n)}"
    rid = db.run("INSERT INTO study_refs (data, key, created_at) VALUES (?, ?, ?)", json.dumps(r), key, db.now())
    return ref(rid)


def ref(rid: int) -> dict:
    store.ensure()
    row = db.one("SELECT * FROM study_refs WHERE id = ?", int(rid))
    if not row:
        raise HTTPException(404, "No such reference")
    return {"id": row["id"], "key": row["key"], **json.loads(row["data"])}


def refs() -> list[dict]:
    store.ensure()
    return [ref(r["id"]) for r in db.all("SELECT id FROM study_refs ORDER BY id DESC LIMIT 2000")]


def delete_ref(rid: int) -> dict:
    ref(rid)
    db.run("DELETE FROM study_refs WHERE id = ?", rid)
    return {"ok": True}


CSL_TYPES = {"article-journal": "article", "book": "book", "chapter": "chapter", "paper-conference": "conference", "report": "report",
             "thesis": "thesis", "webpage": "web", "dataset": "dataset"}


async def from_doi(doi: str) -> dict:
    """Looks a DOI up at doi.org (a read, the user pressed the button) and saves it."""
    registry.require(PACK)
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        raise HTTPException(400, "That doesn't look like a DOI (they start with 10.)")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(f"https://doi.org/{doi}", headers={"Accept": "application/vnd.citationstyles.csl+json"})
    if r.status_code != 200:
        raise HTTPException(404, f"doi.org could not find {doi}.")
    j = r.json()
    year = str(((j.get("issued") or {}).get("date-parts") or [[""]])[0][0] or "")
    return add_ref({"type": CSL_TYPES.get(j.get("type"), "other"), "title": j.get("title") if isinstance(j.get("title"), str) else (j.get("title") or [""])[0],
                    "authors": j.get("author") or [], "year": year, "container": (j.get("container-title") if isinstance(j.get("container-title"), str)
                                                                                 else (j.get("container-title") or [""])[0]),
                    "volume": j.get("volume"), "issue": j.get("issue"), "pages": j.get("page"), "doi": doi, "url": j.get("URL"),
                    "publisher": j.get("publisher")})


BIB_TYPES = {"article": "article", "book": "book", "chapter": "incollection", "conference": "inproceedings", "report": "techreport",
             "thesis": "phdthesis", "web": "online", "dataset": "misc", "other": "misc"}
RIS_TYPES = {"article": "JOUR", "book": "BOOK", "chapter": "CHAP", "conference": "CONF", "report": "RPRT", "thesis": "THES",
             "web": "ELEC", "dataset": "DATA", "other": "GEN"}


def _bib_escape(s: str) -> str:
    return re.sub(r"([&%$#_{}])", r"\\\1", s)


def to_bibtex(items: list[dict]) -> str:
    out = []
    for r in items:
        f = {"title": "{" + _bib_escape(r["title"]) + "}", "author": " and ".join(f"{a['family']}, {a['given']}".strip(", ") for a in r["authors"]),
             "year": r["year"], "doi": r["doi"], "url": r["url"], "volume": r["volume"], "number": r["issue"],
             "pages": r["pages"].replace("-", "--") if r["pages"] and "--" not in r["pages"] else r["pages"], "publisher": r["publisher"], "note": r["note"]}
        if r["container"]:
            f["journal" if r["type"] == "article" else "booktitle" if r["type"] in ("chapter", "conference") else "howpublished"] = r["container"]
        body = ",\n".join(f"  {k} = {{{v}}}" if not v.startswith("{") else f"  {k} = {v}" for k, v in f.items() if v)
        out.append(f"@{BIB_TYPES[r['type']]}{{{r['key']},\n{body}\n}}")
    return "\n\n".join(out) + "\n"


def to_ris(items: list[dict]) -> str:
    out = []
    for r in items:
        lines = [f"TY  - {RIS_TYPES[r['type']]}"]
        lines += [f"AU  - {a['family']}, {a['given']}".rstrip(", ") for a in r["authors"]]
        for tag, k in (("TI", "title"), ("T2", "container"), ("PY", "year"), ("VL", "volume"), ("IS", "issue"), ("DO", "doi"),
                       ("UR", "url"), ("PB", "publisher"), ("N1", "note")):
            if r[k]:
                lines.append(f"{tag}  - {r[k]}")
        if r["pages"]:
            sp, _, ep = r["pages"].replace("--", "-").partition("-")
            lines.append(f"SP  - {sp}")
            if ep:
                lines.append(f"EP  - {ep}")
        lines.append(f"ID  - {r['key']}")
        lines.append("ER  - ")
        out.append("\n".join(lines))
    return "\n".join(out) + "\n"


def import_bibtex(text: str) -> dict:
    registry.require(PACK)
    added = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", text, re.S):
        kind = m.group(1).lower()
        fields = {k.lower(): v.strip().strip("{}").strip('"') for k, v in re.findall(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|\d+)", m.group(3))}
        t = next((k for k, v in BIB_TYPES.items() if v == kind), "article" if kind == "article" else "other")
        authors = [a.strip() for a in fields.get("author", "").split(" and ") if a.strip()]
        try:
            added.append(add_ref({"type": t, "title": fields.get("title", ""), "authors": authors, "year": fields.get("year", ""),
                                  "container": fields.get("journal") or fields.get("booktitle", ""), "volume": fields.get("volume"),
                                  "issue": fields.get("number"), "pages": fields.get("pages", "").replace("--", "-"), "doi": fields.get("doi"),
                                  "url": fields.get("url"), "publisher": fields.get("publisher")}))
        except HTTPException:
            continue
    return {"added": len(added), "refs": added}


# --- Zotero (the desktop app on this computer) -------------------------------------------------------------------------------
ZOTERO = "http://127.0.0.1:23119"
ZOTERO_TYPES = {"article": "journalArticle", "book": "book", "chapter": "bookSection", "conference": "conferencePaper", "report": "report",
                "thesis": "thesis", "web": "webpage", "dataset": "dataset", "other": "document"}


async def zotero_status() -> dict:
    out = {"running": False, "local_api": False, "how": "Open Zotero on this computer. To bring your Zotero library in, turn on "
                                                         "Settings > Advanced > Allow other applications on this computer to communicate with Zotero."}
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{ZOTERO}/connector/ping")
            out["running"] = r.status_code == 200
            if out["running"]:
                r2 = await c.get(f"{ZOTERO}/api/users/0/items/top", params={"limit": 1, "format": "json"})
                out["local_api"] = r2.status_code == 200
    except httpx.HTTPError:
        pass
    return out


async def zotero_import(limit: int = 100) -> dict:
    registry.require(PACK)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{ZOTERO}/api/users/0/items/top", params={"limit": min(limit, 200), "format": "json"})
    except httpx.HTTPError as e:
        raise HTTPException(400, "Zotero isn't open on this computer.") from e
    if r.status_code != 200:
        raise HTTPException(400, "Zotero is open but its local API is off. Turn on Settings > Advanced > Allow other applications "
                                 "on this computer to communicate with Zotero.")
    have = {(x["title"].lower(), x["year"]) for x in refs()}
    added = []
    rev = {v: k for k, v in ZOTERO_TYPES.items()}
    for it in r.json():
        d = it.get("data") or {}
        if d.get("itemType") in ("attachment", "note"):
            continue
        year = (re.search(r"\d{4}", d.get("date") or "") or [""])[0] if d.get("date") else ""
        if (str(d.get("title", "")).lower(), year) in have:
            continue
        try:
            added.append(add_ref({"type": rev.get(d.get("itemType"), "other"), "title": d.get("title"), "year": year,
                                  "authors": [{"family": c.get("lastName") or c.get("name"), "given": c.get("firstName", "")} for c in d.get("creators") or []],
                                  "container": d.get("publicationTitle") or d.get("bookTitle") or d.get("proceedingsTitle"), "volume": d.get("volume"),
                                  "issue": d.get("issue"), "pages": d.get("pages"), "doi": d.get("DOI"), "url": d.get("url"), "publisher": d.get("publisher")}))
        except HTTPException:
            continue
    return {"added": len(added), "refs": added}


async def zotero_send(ids: list[int]) -> dict:
    """Adds the chosen references to the Zotero library on this computer (the user pressed Send to Zotero)."""
    registry.require(PACK)
    items = [ref(i) for i in ids] if ids else refs()
    if not items:
        raise HTTPException(400, "No references to send.")
    payload = {"sessionID": secrets.token_hex(8), "uri": "http://localhost/ai-workspace", "items": [{
        "itemType": ZOTERO_TYPES[r["type"]], "title": r["title"], "date": r["year"], "DOI": r["doi"], "url": r["url"], "volume": r["volume"],
        "issue": r["issue"], "pages": r["pages"], "publisher": r["publisher"], "publicationTitle": r["container"] if r["type"] == "article" else "",
        "creators": [{"lastName": a["family"], "firstName": a["given"], "creatorType": "author"} for a in r["authors"]],
        "tags": [], "notes": [], "attachments": []} for r in items]}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{ZOTERO}/connector/saveItems", json=payload, headers={"X-Zotero-Connector-API-Version": "3"})
    except httpx.HTTPError as e:
        raise HTTPException(400, "Zotero isn't open on this computer.") from e
    if r.status_code not in (200, 201):
        raise HTTPException(502, f"Zotero did not accept the references ({r.status_code}).")
    return {"sent": len(items)}


# --- lab notebook (Pro) -------------------------------------------------------------------------------------------------------
def _entry(row: dict) -> dict:
    row = dict(row)
    row["attachments"] = [a for a in (analysis.get(i) if _exists(i) else {"id": i, "name": "(removed)", "missing": True}
                                      for i in json.loads(row["attachments"] or "[]"))]
    row["attachments"] = [{k: a.get(k) for k in ("id", "name", "kind", "missing")} for a in row["attachments"]]
    row["amendments"] = json.loads(row["amendments"] or "[]")
    return row


def _exists(fid: int) -> bool:
    try:
        analysis.get(fid)
        return True
    except HTTPException:
        return False


def lab_entries(q: str = "") -> list[dict]:
    registry.require(PACK)
    registry.require_feature(LAB)
    store.ensure()
    rows = db.all("SELECT * FROM lab_entries ORDER BY entry_date DESC, id DESC LIMIT 1000")
    words = [w.lower() for w in re.findall(r"\w+", q or "")]
    return [_entry(r) for r in rows if not words or all(w in f"{r['title']} {r['body']} {r['tags']}".lower() for w in words)]


def lab_entry(eid: int) -> dict:
    store.ensure()
    r = db.one("SELECT * FROM lab_entries WHERE id = ?", int(eid))
    if not r:
        raise HTTPException(404, "No such notebook entry")
    return _entry(r)


def _local_today() -> str:
    from ..assistant import local_now

    return local_now().date().isoformat()


def save_lab_entry(data: dict, eid: int | None = None) -> dict:
    registry.require(PACK)
    registry.require_feature(LAB)
    store.ensure()
    title = str(data.get("title") or "").strip()[:200]
    if not title:
        raise HTTPException(400, "Give the entry a title.")
    raw_tags = data.get("tags") or ""
    if isinstance(raw_tags, (list, tuple)):  # the assistant (or an API caller) may send a list
        raw_tags = ", ".join(str(t).strip() for t in raw_tags if str(t).strip())
    body, tags = str(data.get("body") or "")[:100000], str(raw_tags)[:300]
    att = [int(x) for x in data.get("attachments") or [] if str(x).isdigit()][:50]
    for a in att:
        analysis.get(a)
    if eid:
        old = lab_entry(eid)
        amend = old["amendments"] + [{"at": old["updated_at"], "title": old["title"], "body": old["body"], "tags": old["tags"]}]
        db.run("UPDATE lab_entries SET title = ?, body = ?, tags = ?, attachments = ?, amendments = ?, updated_at = ? WHERE id = ?",
               title, body, tags, json.dumps(att or [a["id"] for a in old["attachments"]]), json.dumps(amend[-50:]), db.now(), eid)
        return lab_entry(eid)
    d = str(data.get("entry_date") or _local_today())
    try:
        date.fromisoformat(d)
    except ValueError as e:
        raise HTTPException(400, "Date should look like 2026-10-30") from e
    eid = db.run("INSERT INTO lab_entries (title, body, tags, attachments, entry_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 title, body, tags, json.dumps(att), d, db.now(), db.now())
    return lab_entry(eid)


def lab_export(fmt: str = "md", ids: list[int] | None = None) -> dict:
    entries = [lab_entry(i) for i in ids] if ids else list(reversed(lab_entries()))
    if not entries:
        raise HTTPException(400, "The notebook is empty.")
    if fmt == "json":
        p = analysis.outputs_dir() / f"lab-notebook-{secrets.token_hex(3)}.json"
        p.write_text(json.dumps(entries, indent=2, default=str))
        f = analysis._register_output(p, "Lab notebook.json")
        return {**f, "download": f"/api/files/{f['id']}/download"}
    md = []
    for e in entries:
        md.append(f"## {e['entry_date']}: {e['title']}\n")
        if e["tags"]:
            md.append(f"Tags: {e['tags']}\n")
        md.append(e["body"].strip() + "\n")
        if e["attachments"]:
            md.append("Attachments: " + ", ".join(a["name"] for a in e["attachments"]) + "\n")
        md.append(f"Created {e['created_at']} UTC" + (f"; amended {len(e['amendments'])} time(s), last {e['updated_at']} UTC" if e["amendments"] else "") + "\n")
    text = "\n".join(md)
    if fmt == "pdf":
        return analysis.make_pdf("Lab notebook", text)
    p = analysis.outputs_dir() / f"lab-notebook-{secrets.token_hex(3)}.md"
    p.write_text("# Lab notebook\n\n" + text)
    f = analysis._register_output(p, "Lab notebook.md")
    return {**f, "download": f"/api/files/{f['id']}/download"}
