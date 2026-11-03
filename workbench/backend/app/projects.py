"""Projects with memory (feat/v02-core).

A project is whatever someone's work is organised around: a job, a client, a legal matter, a course, a study, a
studio brief. It holds files, work sites, chats, notes, people and deadlines. The assistant works out which project a
chat or file belongs to (deterministic scoring on names, keywords, people and sites, so it is instant, free and works
offline) and pulls that project's context, plus what the learn store knows about it, into the turn.

Free: FREE_LIMITS["projects"] active projects. Pro: unlimited (features_v02).
Notes and person details are encrypted at rest with the workspace key, like the learn store.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import config, db, features_v02
from .security import decrypt, encrypt, require_user

log = logging.getLogger("projects")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'other',          -- job | client | matter | course | research | studio | other
  description TEXT NOT NULL DEFAULT '',
  keywords TEXT NOT NULL DEFAULT '',            -- comma separated words that mean this project (site no., client, code)
  status TEXT NOT NULL DEFAULT 'active',        -- active | archived
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS project_items (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                           -- file | site | chat | note | person | deadline | link
  ref TEXT NOT NULL DEFAULT '',                 -- file id, site id, conversation id or URL; '' for notes, people, deadlines
  title TEXT NOT NULL DEFAULT '',
  body_enc TEXT NOT NULL DEFAULT '',            -- note text, or a person's role and contact (encrypted)
  due TEXT,                                     -- YYYY-MM-DD for deadlines
  done INTEGER NOT NULL DEFAULT 0,
  auto INTEGER NOT NULL DEFAULT 0,              -- 1 when the assistant filed it (shown with "Filed automatically")
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS project_items_ref ON project_items(project_id, kind, ref) WHERE ref != '';
CREATE INDEX IF NOT EXISTS project_items_kind_ref ON project_items(kind, ref);
CREATE TABLE IF NOT EXISTS project_dismissed (
  project_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  PRIMARY KEY (project_id, kind, ref)
);
"""
KINDS = {"job": "Job", "client": "Client", "matter": "Legal matter", "course": "Course or subject", "research": "Research",
         "studio": "Studio or creative brief", "other": "Something else"}
ITEM_KINDS = ("file", "site", "chat", "note", "person", "deadline", "link")
REF_KINDS = ("file", "site", "chat", "link")
STOP = set("the a an and or of for to in on at by with from my our your this that job project client course matter new "
           "work site page file files chat notes note please can you me i we it is are be".split())

_ready: set[str] = set()
_lock = threading.Lock()


def ensure() -> None:
    key = str(config.DB_PATH)
    if key in _ready:
        return
    with _lock:
        if key not in _ready:
            db.conn().executescript(SCHEMA)
            _ready.add(key)


def _seal(text: str) -> str:
    return encrypt(text) if text else ""


def _open(value: str) -> str:
    return decrypt(value) if value else ""


# Projects ----------------------------------------------------------------------------------------------------------
def get(pid: int) -> dict:
    ensure()
    p = db.one("SELECT * FROM projects WHERE id = ?", int(pid))
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def active_count() -> int:
    ensure()
    return db.one("SELECT COUNT(*) AS n FROM projects WHERE status = 'active'")["n"]


def limit_info() -> dict:
    unlimited = features_v02.allowed("projects_unlimited")
    free = features_v02.FREE_LIMITS["projects"]
    return {"unlimited": unlimited, "free_limit": free, "active": active_count(),
            "can_add": unlimited or active_count() < free}


def _check_limit() -> None:
    if not limit_info()["can_add"]:
        d = features_v02.upgrade_detail("projects_unlimited")
        d["message"] = (f"The free plan keeps {features_v02.FREE_LIMITS['projects']} project going at a time. Archive one, "
                        "or upgrade to Pro for unlimited projects.")
        raise HTTPException(402, d)


def create(name: str, kind: str = "other", description: str = "", keywords: str = "") -> dict:
    ensure()
    name = name.strip()
    if len(name) < 2:
        raise HTTPException(400, "Give the project a name, like the job address, client or course.")
    if db.one("SELECT id FROM projects WHERE lower(name) = lower(?) AND status = 'active'", name):
        raise HTTPException(409, f"There is already a project called {name}.")
    _check_limit()
    pid = db.run("INSERT INTO projects(name, kind, description, keywords, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                 name[:120], kind if kind in KINDS else "other", description.strip()[:2000], _clean_keywords(keywords),
                 db.now(), db.now())
    _invalidate()
    scan(pid)
    return detail(pid)


def _clean_keywords(k: str) -> str:
    return ", ".join(dict.fromkeys(w.strip() for w in re.split(r"[,;\n]", k or "") if w.strip()))[:500]


def update(pid: int, **fields) -> dict:
    p = get(pid)
    if fields.get("status") == "active" and p["status"] != "active":
        _check_limit()
    sets = {}
    for k in ("name", "kind", "description", "keywords", "status"):
        v = fields.get(k)
        if v is None:
            continue
        if k == "kind" and v not in KINDS:
            v = "other"
        if k == "status" and v not in ("active", "archived"):
            raise HTTPException(400, "status is active or archived")
        if k == "keywords":
            v = _clean_keywords(v)
        sets[k] = v.strip() if isinstance(v, str) else v
    if sets:
        db.run(f"UPDATE projects SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ? WHERE id = ?",
               *sets.values(), db.now(), pid)
        _invalidate()
    if fields.get("status") == "archived" and db.get_setting("active_project_id") == str(pid):
        db.set_setting("active_project_id", "")
    return detail(pid)


def delete(pid: int) -> dict:
    get(pid)
    db.run("DELETE FROM project_items WHERE project_id = ?", pid)
    db.run("DELETE FROM project_dismissed WHERE project_id = ?", pid)
    db.run("DELETE FROM projects WHERE id = ?", pid)
    if db.get_setting("active_project_id") == str(pid):
        db.set_setting("active_project_id", "")
    _invalidate()
    return {"ok": True, "note": "The project is gone. Its files, chats and sites are still in the app."}


def listing(include_archived: bool = False) -> list[dict]:
    ensure()
    rows = db.all("SELECT * FROM projects " + ("" if include_archived else "WHERE status = 'active' ") +
                  "ORDER BY status, COALESCE(last_used_at, updated_at) DESC")
    today = date.today().isoformat()
    for p in rows:
        counts = {r["kind"]: r["n"] for r in db.all("SELECT kind, COUNT(*) AS n FROM project_items WHERE project_id = ? GROUP BY kind", p["id"])}
        p["counts"] = counts
        nxt = db.one("SELECT title, due FROM project_items WHERE project_id = ? AND kind = 'deadline' AND done = 0 AND due IS NOT NULL "
                     "ORDER BY due LIMIT 1", p["id"])
        p["next_deadline"] = nxt | {"overdue": nxt["due"] < today} if nxt else None
        p["kind_label"] = KINDS.get(p["kind"], "Project")
    return rows


def active_id() -> int | None:
    v = db.get_setting("active_project_id", "") or ""
    if v.isdigit() and db.one("SELECT 1 FROM projects WHERE id = ? AND status = 'active'", int(v)):
        return int(v)
    return None


def set_active(pid: int | None) -> dict:
    if pid:
        get(pid)
    db.set_setting("active_project_id", str(pid) if pid else "")
    return {"active_id": active_id()}


# Items -------------------------------------------------------------------------------------------------------------
def _item_out(r: dict) -> dict:
    out = {k: r[k] for k in ("id", "project_id", "kind", "ref", "title", "due", "done", "auto", "reason", "created_at")}
    out["body"] = _open(r["body_enc"])
    out["done"] = bool(r["done"])
    out["auto"] = bool(r["auto"])
    return out


def add_item(pid: int, kind: str, title: str = "", body: str = "", ref: str = "", due: str | None = None,
             auto: bool = False, reason: str = "") -> dict:
    p = get(pid)
    if kind not in ITEM_KINDS:
        raise HTTPException(400, f"kind is one of {', '.join(ITEM_KINDS)}")
    ref = str(ref or "").strip()
    if kind in REF_KINDS and not ref:
        raise HTTPException(400, f"A {kind} needs ref (its id, or the URL for a link).")
    if kind not in REF_KINDS:
        ref = ""
    title = (title or "").strip() or _ref_title(kind, ref)
    if kind in ("note",) and not (body or "").strip():
        raise HTTPException(400, "A note needs some text.")
    if kind in ("person", "deadline") and not title:
        raise HTTPException(400, f"A {kind} needs a name.")
    if due:
        try:
            due = date.fromisoformat(str(due)[:10]).isoformat()
        except ValueError as e:
            raise HTTPException(400, "due is a date like 2026-11-30") from e
    if ref:
        existing = db.one("SELECT * FROM project_items WHERE project_id = ? AND kind = ? AND ref = ?", pid, kind, ref)
        if existing:
            if not auto and existing["auto"]:  # the person confirmed what was filed automatically
                db.run("UPDATE project_items SET auto = 0, reason = '' WHERE id = ?", existing["id"])
            return _item_out(db.one("SELECT * FROM project_items WHERE id = ?", existing["id"]))
        db.run("DELETE FROM project_dismissed WHERE project_id = ? AND kind = ? AND ref = ?", pid, kind, ref)
    iid = db.run("INSERT INTO project_items(project_id, kind, ref, title, body_enc, due, auto, reason, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", pid, kind, ref, title[:300], _seal((body or "").strip()[:20000]),
                 due, int(auto), reason[:200], db.now())
    db.run("UPDATE projects SET updated_at = ? WHERE id = ?", db.now(), pid)
    if kind in ("person", "site", "file"):
        _invalidate()
    if not auto:
        _observe(p, f"Added a {kind} to {p['name']}: {title[:120]}")
    return _item_out(db.one("SELECT * FROM project_items WHERE id = ?", iid))


def update_item(iid: int, **fields) -> dict:
    ensure()
    r = db.one("SELECT * FROM project_items WHERE id = ?", iid)
    if not r:
        raise HTTPException(404, "Not found")
    sets = {}
    if fields.get("title") is not None:
        sets["title"] = fields["title"].strip()[:300]
    if fields.get("body") is not None:
        sets["body_enc"] = _seal(fields["body"].strip()[:20000])
    if fields.get("due") is not None:
        sets["due"] = date.fromisoformat(fields["due"][:10]).isoformat() if fields["due"] else None
    if fields.get("done") is not None:
        sets["done"] = int(bool(fields["done"]))
    if sets:
        db.run(f"UPDATE project_items SET {', '.join(f'{k} = ?' for k in sets)}, auto = 0 WHERE id = ?", *sets.values(), iid)
    return _item_out(db.one("SELECT * FROM project_items WHERE id = ?", iid))


def remove_item(iid: int) -> dict:
    ensure()
    r = db.one("SELECT * FROM project_items WHERE id = ?", iid)
    if not r:
        raise HTTPException(404, "Not found")
    db.run("DELETE FROM project_items WHERE id = ?", iid)
    if r["ref"]:  # never file it here again automatically
        db.run("INSERT OR IGNORE INTO project_dismissed(project_id, kind, ref) VALUES (?, ?, ?)", r["project_id"], r["kind"], r["ref"])
    _invalidate()
    return {"ok": True}


def _ref_title(kind: str, ref: str) -> str:
    try:
        if kind == "file":
            r = db.one("SELECT name FROM work_files WHERE id = ?", int(ref))
            return r["name"] if r else f"File #{ref}"
        if kind == "site":
            r = db.one("SELECT name FROM sites WHERE id = ?", int(ref))
            return r["name"] if r else f"Site #{ref}"
        if kind == "chat":
            r = db.one("SELECT title FROM conversations WHERE id = ?", int(ref))
            return r["title"] if r else f"Chat #{ref}"
    except Exception:  # noqa: BLE001 - tables of optional features may not exist yet
        pass
    return ref


def detail(pid: int) -> dict:
    p = get(pid)
    items = [_item_out(r) for r in db.all("SELECT * FROM project_items WHERE project_id = ? ORDER BY "
                                          "CASE WHEN due IS NULL THEN 1 ELSE 0 END, due, id DESC", pid)]
    today = date.today().isoformat()
    for it in items:
        if it["kind"] == "deadline":
            it["overdue"] = bool(it["due"] and it["due"] < today and not it["done"])
        if it["kind"] in ("file", "site", "chat"):
            it["title"] = _ref_title(it["kind"], it["ref"]) or it["title"]
    grouped = {k: [i for i in items if i["kind"] == k] for k in ITEM_KINDS}
    return {**p, "kind_label": KINDS.get(p["kind"], "Project"), "items": grouped, "facts": related_facts(p, 8),
            "suggestions": suggestions(pid), "active": active_id() == pid}


# Detection ---------------------------------------------------------------------------------------------------------
_terms_cache: dict[str, list[dict]] = {}


def _invalidate() -> None:
    _terms_cache.clear()


def _norm(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9@.]+", " ", (s or "").lower()).strip() + " "


def _host(url: str) -> str:
    m = re.match(r"^(?:https?://)?([^/:?#]+)", (url or "").strip().lower())
    return (m.group(1) if m else "").removeprefix("www.")


def _terms() -> list[dict]:
    key = str(config.DB_PATH)
    if key in _terms_cache:
        return _terms_cache[key]
    ensure()
    out = []
    for p in db.all("SELECT * FROM projects WHERE status = 'active'"):
        phrases = {}  # phrase -> weight
        name = _norm(p["name"]).strip()
        if name:
            phrases[name] = 3
        for w in name.split():
            if len(w) >= 4 and w not in STOP:
                phrases.setdefault(w, 1)
        for k in (p["keywords"] or "").split(","):
            k = _norm(k).strip()
            if len(k) >= 2:
                phrases[k] = max(phrases.get(k, 0), 2)
        for r in db.all("SELECT kind, ref, title FROM project_items WHERE project_id = ? AND kind IN ('person', 'site', 'file')", p["id"]):
            if r["kind"] == "person":
                full = _norm(r["title"]).strip()
                if len(full) >= 3:
                    phrases[full] = max(phrases.get(full, 0), 2)
            elif r["kind"] == "site":
                t = _ref_title("site", r["ref"])
                h = ""
                try:
                    s = db.one("SELECT url FROM sites WHERE id = ?", int(r["ref"]))
                    h = _host(s["url"]) if s else ""
                except Exception:  # noqa: BLE001
                    pass
                if h:
                    phrases[_norm(h).strip()] = 2
                if t and len(t) >= 4:
                    phrases.setdefault(_norm(t).strip(), 1)
        out.append({"id": p["id"], "name": p["name"], "phrases": phrases})
    _terms_cache[key] = out
    return out


def score(text: str) -> list[dict]:
    """Every active project with how strongly the text points at it, best first."""
    t = _norm(text)
    res = []
    for p in _terms():
        total, why = 0, []
        for phrase, w in p["phrases"].items():
            if f" {phrase} " in t:
                total += w
                why.append(phrase)
        if total:
            res.append({"project_id": p["id"], "name": p["name"], "score": total, "matched": why[:6]})
    res.sort(key=lambda r: -r["score"])
    return res


def detect(text: str) -> dict:
    """{"project_id", "name", "score", "matched", "confident"} or {"project_id": None}."""
    ranked = score(text or "")
    if not ranked:
        return {"project_id": None, "confident": False}
    best = ranked[0]
    second = ranked[1]["score"] if len(ranked) > 1 else 0
    return {**best, "confident": best["score"] >= 3 and best["score"] > second + 1,
            "possible": best["score"] >= 2 and best["score"] > second}


def project_for_conversation(cid: int) -> dict | None:
    ensure()
    return db.one("SELECT p.* FROM project_items i JOIN projects p ON p.id = i.project_id WHERE i.kind = 'chat' AND i.ref = ? "
                  "AND p.status = 'active' ORDER BY i.id DESC LIMIT 1", str(cid))


def move_conversation(cid: int, pid: int | None) -> dict:
    ensure()
    db.run("DELETE FROM project_items WHERE kind = 'chat' AND ref = ?", str(cid))
    if pid:
        add_item(pid, "chat", ref=str(cid))
    return {"project": project_for_conversation(cid)}


def link_conversation(cid: int, user_text: str) -> tuple[dict | None, str]:
    """The project this chat belongs to: the one it is filed in, a clearly named other one, or the active project."""
    if not cid:
        return None, ""
    current = project_for_conversation(cid)
    d = detect(user_text)
    if d.get("confident") and (not current or current["id"] != d["project_id"]):
        p = get(d["project_id"])
        if not db.one("SELECT 1 FROM project_dismissed WHERE project_id = ? AND kind = 'chat' AND ref = ?", p["id"], str(cid)):
            add_item(p["id"], "chat", ref=str(cid), auto=True, reason="mentions " + ", ".join(d["matched"][:3]))
            _touch(p["id"])
            return p, "detected"
    if current:
        return current, "filed"
    aid = active_id()
    if aid:
        add_item(aid, "chat", ref=str(cid), auto=True, reason="started while this project was open")
        _touch(aid)
        return get(aid), "active"
    return None, ""


def _touch(pid: int) -> None:
    db.run("UPDATE projects SET last_used_at = ? WHERE id = ?", db.now(), pid)
    p = db.one("SELECT * FROM projects WHERE id = ?", pid)
    if p:
        _observe(p, f"Worked on {p['name']}")


def _observe(p: dict, summary: str) -> None:
    from . import learn_hook  # routine spotting in the learn store sees project work too

    learn_hook.observe("project", summary, f"project:{p['id']}")


def _candidates() -> list[tuple[str, str, str]]:
    """Recent things not yet in any project: (kind, ref, text to score)."""
    out = []
    try:
        from . import analysis

        analysis.ensure()
        for f in db.all("SELECT id, name, summary FROM work_files WHERE source != 'output' ORDER BY id DESC LIMIT 300"):
            out.append(("file", str(f["id"]), f"{f['name']} {f['name'].rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')} {f['summary']}"))
    except Exception as e:  # noqa: BLE001
        log.debug("files not scanned: %s", e)
    try:
        from . import sites

        sites.ensure()
        for s in db.all("SELECT id, name, url FROM sites ORDER BY id DESC LIMIT 100"):
            out.append(("site", str(s["id"]), f"{s['name']} {_host(s['url'])}"))
    except Exception as e:  # noqa: BLE001
        log.debug("sites not scanned: %s", e)
    for c in db.all("SELECT id, title FROM conversations WHERE channel IN ('web', 'cli', 'mobile', 'telegram') ORDER BY updated_at DESC LIMIT 200"):
        out.append(("chat", str(c["id"]), c["title"]))
    linked = {(r["kind"], r["ref"]) for r in db.all("SELECT kind, ref FROM project_items WHERE ref != ''")}
    return [c for c in out if (c[0], c[1]) not in linked]


def scan(pid: int | None = None) -> int:
    """File what clearly belongs to one project automatically. Returns how many were filed."""
    ensure()
    filed = 0
    dismissed = {(r["project_id"], r["kind"], r["ref"]) for r in db.all("SELECT * FROM project_dismissed")}
    for kind, ref, text in _candidates():
        d = detect(text)
        if d.get("confident") and (pid is None or d["project_id"] == pid) and (d["project_id"], kind, ref) not in dismissed:
            add_item(d["project_id"], kind, ref=ref, auto=True, reason="name mentions " + ", ".join(d["matched"][:3]))
            filed += 1
    return filed


def suggestions(pid: int) -> list[dict]:
    """Things that might belong here (one click to add, or dismiss)."""
    dismissed = {(r["kind"], r["ref"]) for r in db.all("SELECT kind, ref FROM project_dismissed WHERE project_id = ?", pid)}
    out = []
    for kind, ref, text in _candidates():
        if (kind, ref) in dismissed:
            continue
        for r in score(text)[:2]:
            if r["project_id"] == pid and r["score"] >= 1:
                out.append({"kind": kind, "ref": ref, "title": _ref_title(kind, ref), "matched": r["matched"][:3], "score": r["score"]})
                break
    out.sort(key=lambda s: -s["score"])
    return out[:12]


def dismiss(pid: int, kind: str, ref: str) -> dict:
    get(pid)
    db.run("INSERT OR IGNORE INTO project_dismissed(project_id, kind, ref) VALUES (?, ?, ?)", pid, kind, str(ref))
    return {"ok": True}


# Context for the assistant ----------------------------------------------------------------------------------------
def related_facts(p: dict, limit: int = 6) -> list[dict]:
    """What the learn store knows that relates to this project (encrypted facts, BM25 over their words)."""
    try:
        from .adapt import knowledge

        q = " ".join([p["name"], p["keywords"].replace(",", " "), p["description"][:200]])
        return [{"id": f["id"], "content": f["content"], "category": f["category"]} for f in knowledge.search(q, limit)]
    except Exception as e:  # noqa: BLE001
        log.debug("facts unavailable: %s", e)
        return []


def context_block(p: dict, why: str = "") -> str:
    d = detail(p["id"])
    it = d["items"]
    lines = [f"Current project: {p['name']} ({KINDS.get(p['kind'], 'project')}). "
             + (f"Linked because it {'was detected in the message' if why == 'detected' else 'is open' if why == 'active' else 'is where this chat is filed'}. " if why else "")
             + "Use this context; say which project you are working in if it matters. If the user means a different project, say so."]
    if p["description"]:
        lines.append("About it: " + p["description"][:600])
    if it["person"]:
        lines.append("People: " + "; ".join(f"{x['title']}" + (f" ({x['body'][:80]})" if x["body"] else "") for x in it["person"][:12]))
    open_deadlines = [x for x in it["deadline"] if not x["done"]]
    if open_deadlines:
        lines.append("Deadlines: " + "; ".join(f"{x['title']} {x['due'] or 'no date'}" + (" OVERDUE" if x.get("overdue") else "")
                                               for x in open_deadlines[:12]))
    if it["file"]:
        lines.append("Files in this project (read_file with the id): " + "; ".join(f"#{x['ref']} {x['title']}" for x in it["file"][:20]))
    if it["site"]:
        lines.append("Work sites for it (site_records / check_site with the id): " + "; ".join(f"#{x['ref']} {x['title']}" for x in it["site"][:10]))
    if it["link"]:
        lines.append("Links: " + "; ".join(x["ref"] for x in it["link"][:10]))
    if it["note"]:
        lines.append("Project notes:\n" + "\n".join(f"- {x['body'][:300]}" for x in it["note"][:8]))
    if d["facts"]:
        lines.append("What you know that relates to it:\n" + "\n".join(f"- {f['content']}" for f in d["facts"][:6]))
    others = [x["title"] for x in it["chat"][:6]]
    if others:
        lines.append("Earlier chats in this project: " + "; ".join(others))
    return "\n".join(lines)[:5000]


# API -----------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/projects", dependencies=[Depends(require_user), Depends(ensure)])


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: str = "other"
    description: str = Field("", max_length=2000)
    keywords: str = Field("", max_length=500)


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    kind: str | None = None
    description: str | None = Field(None, max_length=2000)
    keywords: str | None = Field(None, max_length=500)
    status: str | None = None


class ItemIn(BaseModel):
    kind: str
    ref: str = ""
    title: str = Field("", max_length=300)
    body: str = Field("", max_length=20000)
    due: str | None = None


class ItemPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    body: str | None = Field(None, max_length=20000)
    due: str | None = None
    done: bool | None = None


class ActiveIn(BaseModel):
    project_id: int | None = None


class RefIn(BaseModel):
    kind: str
    ref: str


class TextIn(BaseModel):
    text: str = Field(max_length=20000)


@router.get("")
def api_list(archived: bool = False):
    scan()
    return {"projects": listing(archived), "active_id": active_id(), "kinds": KINDS, "limit": limit_info()}


@router.post("")
def api_create(data: ProjectIn):
    return create(data.name, data.kind, data.description, data.keywords)


@router.put("/active")
def api_active(data: ActiveIn):
    return set_active(data.project_id)


@router.post("/detect")
def api_detect(data: TextIn):
    return detect(data.text)


@router.get("/for-conversation/{cid}")
def api_for_conv(cid: int):
    return {"project": project_for_conversation(cid)}


@router.put("/for-conversation/{cid}")
def api_move_conv(cid: int, data: ActiveIn):
    return move_conversation(cid, data.project_id)


@router.put("/items/{iid}")
def api_update_item(iid: int, data: ItemPatch):
    return update_item(iid, **data.model_dump())


@router.delete("/items/{iid}")
def api_remove_item(iid: int):
    return remove_item(iid)


@router.get("/{pid}")
def api_detail(pid: int):
    scan(pid)
    return detail(pid)


@router.put("/{pid}")
def api_update(pid: int, data: ProjectPatch):
    return update(pid, **data.model_dump())


@router.delete("/{pid}")
def api_delete(pid: int):
    return delete(pid)


@router.post("/{pid}/items")
def api_add_item(pid: int, data: ItemIn):
    return add_item(pid, data.kind, data.title, data.body, data.ref, data.due)


@router.post("/{pid}/dismiss")
def api_dismiss(pid: int, data: RefIn):
    return dismiss(pid, data.kind, data.ref)


# Assistant tools ---------------------------------------------------------------------------------------------------
def build_tools(Tool, S, I) -> list:  # noqa: N803 - the registry's own constructors
    def _find(project) -> dict:
        ensure()
        if isinstance(project, int) or str(project).strip().isdigit():
            return get(int(project))
        p = db.one("SELECT * FROM projects WHERE lower(name) = lower(?)", str(project).strip())
        if p:
            return p
        d = detect(str(project))
        if d.get("project_id"):
            return get(d["project_id"])
        raise HTTPException(404, f"No project called {project}. list_projects shows them.")

    def _list():
        return [{k: p[k] for k in ("id", "name", "kind", "description", "counts", "next_deadline")} for p in listing()]

    def _context(project):
        return {"context": context_block(_find(project))}

    def _create(name, kind="other", description="", keywords=""):
        p = create(name, kind, description, keywords)
        return {"id": p["id"], "name": p["name"]}

    def _add(project, kind, title="", body="", ref="", due=""):
        p = _find(project)
        return add_item(p["id"], kind, title, body, ref, due or None) | {"project": p["name"]}

    return [
        Tool("list_projects", "The user's projects (jobs, clients, legal matters, courses, studies...) with counts and next deadline.",
             {}, _list),
        Tool("project_context", "Everything in one project: people, deadlines, files, sites, notes and related memories.",
             {"project": S("project id or name")}, _context, ("project",)),
        Tool("create_project", "Start a project when the user begins a new job, client, matter, course or piece of work. "
             "keywords: comma separated words that identify it (site number, client, case number, course code).",
             {"name": S(), "kind": S("job, client, matter, course, research, studio or other"), "description": S(), "keywords": S()},
             _create, ("name",), effect="write"),
        Tool("add_to_project", "File something in a project: kind note (body), person (title = name, body = role/contact), "
             "deadline (title, due YYYY-MM-DD), file (ref = file id), site (ref = site id), chat (ref = conversation id), link (ref = URL).",
             {"project": S("project id or name"), "kind": S(), "title": S(), "body": S(), "ref": S(), "due": S()},
             _add, ("project", "kind"), effect="write"),
    ]
