"""Projects: a workspace for one piece of ongoing work, so ROOKS knows where things stand without being told again.

A project holds its goals and instructions, decisions and open questions, and is linked to its conversations,
missions, tasks, notes and documents, files and research. Memories saved in a project stay in that project: they are
recalled only there (and never in other projects); things about you (profile) stay user-wide.

Per project you can narrow permissions (never widen them beyond Settings), require local models only, prefer a model,
and set a monthly budget. Archive hides a project; Delete moves it to the bin for 30 days with everything still linked;
only a permanent delete, confirmed with the project's name, removes it (its conversations and files are unlinked and
kept, its project memories are removed).
"""

import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from . import audit, db

LINKED = ("conversations", "missions", "memories", "research", "docs", "files", "tasks")
MODES = ("on", "ask", "off")
BIN_DAYS = 30


def _row(pid: int, include_deleted: bool = False) -> dict:
    p = db.one("SELECT * FROM projects WHERE id = ?", pid)
    if not p or (p["deleted_at"] and not include_deleted):
        raise HTTPException(404, "Project not found")
    p["permissions"] = json.loads(p["permissions"] or "{}")
    return p


def get(pid: int | None) -> dict | None:
    if not pid:
        return None
    try:
        return _row(pid)
    except HTTPException:
        return None


def _clean_permissions(perms: dict | None) -> dict:
    from .permissions import GROUPS

    out = {}
    for g, m in (perms or {}).items():
        if g not in GROUPS or m not in MODES:
            raise HTTPException(400, f"Unknown permission {g}={m}")
        out[g] = m
    return out


def create(name: str, description: str = "", goals: str = "", instructions: str = "", model: str = "", budget_cents: int = 0,
           local_only: bool = False, permissions: dict | None = None, isolated: bool = False) -> dict:
    name = name.strip()
    if not name:
        raise HTTPException(400, "A project needs a name.")
    if db.one("SELECT 1 FROM projects WHERE lower(name) = lower(?) AND deleted_at IS NULL", name):
        raise HTTPException(409, f"A project called {name} already exists.")
    now = db.now()
    pid = db.run("INSERT INTO projects(name, description, goals, instructions, model, budget_cents, local_only, permissions, status, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)", name[:120], description[:4000], goals[:4000], instructions[:8000], model[:300],
                 max(0, int(budget_cents)), int(local_only), json.dumps(_clean_permissions(permissions)), now, now)
    if isolated:
        db.run("UPDATE projects SET isolated = 1 WHERE id = ?", pid)
    audit.record("user", "project created", name)
    return _row(pid)


def update(pid: int, **fields) -> dict:
    p = _row(pid)
    allowed = {"name", "description", "goals", "instructions", "model", "budget_cents", "local_only", "permissions", "isolated"}
    changes = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "permissions" in changes:
        changes["permissions"] = json.dumps(_clean_permissions(changes["permissions"]))
    for flag in ("local_only", "isolated"):
        if flag in changes:
            changes[flag] = int(bool(changes[flag]))
    if "name" in changes:
        changes["name"] = changes["name"].strip()[:120]
        if not changes["name"]:
            raise HTTPException(400, "A project needs a name.")
        if db.one("SELECT 1 FROM projects WHERE lower(name) = lower(?) AND id != ? AND deleted_at IS NULL", changes["name"], pid):
            raise HTTPException(409, f"A project called {changes['name']} already exists.")
    if changes:
        changes["updated_at"] = db.now()
        db.run(f"UPDATE projects SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?", *changes.values(), pid)
        audit.record("user", "project changed", p["name"], ", ".join(k for k in changes if k != "updated_at"))
    return _row(pid)


def set_status(pid: int, status: str) -> dict:
    p = _row(pid, include_deleted=True)
    if status == "archived":
        db.run("UPDATE projects SET status = 'archived', updated_at = ? WHERE id = ?", db.now(), pid)
    elif status == "active":
        db.run("UPDATE projects SET status = 'active', deleted_at = NULL, updated_at = ? WHERE id = ?", db.now(), pid)
    elif status == "deleted":
        db.run("UPDATE projects SET deleted_at = ?, updated_at = ? WHERE id = ?", db.now(), db.now(), pid)
    audit.record("user", f"project {status}", p["name"])
    return _row(pid, include_deleted=True)


def purge(pid: int, confirm: str) -> dict:
    """Permanent delete: only from the bin, only with the exact name typed. Linked work is kept, unlinked."""
    p = _row(pid, include_deleted=True)
    if not p["deleted_at"]:
        raise HTTPException(409, "Move the project to the bin first.")
    if confirm.strip() != p["name"]:
        raise HTTPException(400, "Type the project's exact name to delete it for good.")
    removed = db.changed("DELETE FROM memories WHERE project_id = ?", pid)
    for t in LINKED:
        if t != "memories":
            db.run(f"UPDATE {t} SET project_id = NULL WHERE project_id = ?", pid)
    db.run("DELETE FROM project_notes WHERE project_id = ?", pid)
    db.run("DELETE FROM projects WHERE id = ?", pid)
    audit.record("user", "project deleted for good", p["name"], f"{removed} project memories removed; conversations, files and missions kept")
    return {"deleted": pid, "memories_removed": removed}


def listing(q: str = "", include_archived: bool = False, bin_: bool = False) -> list[dict]:
    where = ["deleted_at IS NOT NULL"] if bin_ else ["deleted_at IS NULL"] + ([] if include_archived else ["status = 'active'"])
    args: list = []
    if q:
        where.append("(name LIKE ? OR description LIKE ? OR goals LIKE ?)")
        args += [f"%{q}%"] * 3
    rows = db.all(f"SELECT id, name, description, status, updated_at, deleted_at FROM projects WHERE {' AND '.join(where)} ORDER BY updated_at DESC", *args)
    for r in rows:
        r["counts"] = {t: db.one(f"SELECT COUNT(*) AS n FROM {t} WHERE project_id = ?", r["id"])["n"] for t in ("conversations", "missions", "tasks")}
    return rows


def note(pid: int, kind: str, text: str) -> dict:
    _row(pid)
    if kind not in ("decision", "question"):
        raise HTTPException(400, "kind must be decision or question")
    nid = db.run("INSERT INTO project_notes(project_id, kind, text, created_at) VALUES (?, ?, ?, ?)", pid, kind, text.strip()[:2000], db.now())
    db.run("UPDATE projects SET updated_at = ? WHERE id = ?", db.now(), pid)
    return db.one("SELECT * FROM project_notes WHERE id = ?", nid)


def resolve(nid: int, answer: str = "") -> dict:
    n = db.one("SELECT * FROM project_notes WHERE id = ?", nid)
    if not n:
        raise HTTPException(404, "Not found")
    db.run("UPDATE project_notes SET resolved = 1, answer = ?, resolved_at = ? WHERE id = ?", answer[:2000], db.now(), nid)
    return db.one("SELECT * FROM project_notes WHERE id = ?", nid)


def month_spend(pid: int) -> float:
    from .usage import month_start

    return db.one("SELECT COALESCE(SUM(u.cost_cents), 0) AS c FROM usage u JOIN conversations c ON c.id = u.conversation_id "
                  "WHERE c.project_id = ? AND u.ts >= ?", pid, month_start())["c"]


def overview(pid: int) -> dict:
    p = _row(pid, include_deleted=True)
    one = lambda sql, *a: db.all(sql, *a)  # noqa: E731
    out = {**p,
           "conversations": one("SELECT id, title, updated_at FROM conversations WHERE project_id = ? ORDER BY updated_at DESC LIMIT 20", pid),
           "missions": one("SELECT id, goal, status, updated_at FROM missions WHERE project_id = ? ORDER BY id DESC LIMIT 20", pid),
           "tasks": one("SELECT id, title, status, due FROM tasks WHERE project_id = ? ORDER BY status = 'done', due IS NULL, due LIMIT 50", pid),
           "docs": one("SELECT id, kind, title, updated_at FROM docs WHERE project_id = ? ORDER BY updated_at DESC LIMIT 30", pid),
           "files": one("SELECT id, name, size, created_at FROM files WHERE project_id = ? ORDER BY id DESC LIMIT 30", pid),
           "research": one("SELECT id, question, status, created_at FROM research WHERE project_id = ? ORDER BY id DESC LIMIT 20", pid),
           "memories": one("SELECT id, content, kind, source, updated_at FROM memories WHERE project_id = ? ORDER BY updated_at DESC LIMIT 50", pid),
           "decisions": one("SELECT * FROM project_notes WHERE project_id = ? AND kind = 'decision' ORDER BY id DESC LIMIT 50", pid),
           "questions": one("SELECT * FROM project_notes WHERE project_id = ? AND kind = 'question' ORDER BY resolved, id DESC LIMIT 50", pid),
           "spend_cents": round(month_spend(pid), 2)}
    timeline = [{"at": c["updated_at"], "what": f"Conversation: {c['title']}", "link": f"/assistant/{c['id']}"} for c in out["conversations"][:10]]
    timeline += [{"at": m["updated_at"], "what": f"Mission {m['status']}: {m['goal'][:80]}", "link": f"/missions/{m['id']}"} for m in out["missions"][:10]]
    timeline += [{"at": r["created_at"], "what": f"Research: {r['question'][:80]}", "link": f"/research/{r['id']}"} for r in out["research"][:10]]
    timeline += [{"at": n["created_at"], "what": f"{n['kind'].title()}: {n['text'][:80]}", "link": ""} for n in out["decisions"][:10] + out["questions"][:10]]
    out["timeline"] = sorted(timeline, key=lambda t: t["at"] or "", reverse=True)[:30]
    return out


def brief(pid: int) -> str:
    """What the assistant is told about the project at the start of every turn: enough to carry on without asking."""
    p = get(pid)
    if not p:
        return ""
    decisions = db.all("SELECT text FROM project_notes WHERE project_id = ? AND kind = 'decision' ORDER BY id DESC LIMIT 10", pid)
    questions = db.all("SELECT id, text FROM project_notes WHERE project_id = ? AND kind = 'question' AND resolved = 0 ORDER BY id DESC LIMIT 10", pid)
    missions = db.all("SELECT goal, status FROM missions WHERE project_id = ? ORDER BY id DESC LIMIT 5", pid)
    tasks = db.all("SELECT title, due FROM tasks WHERE project_id = ? AND status != 'done' ORDER BY due IS NULL, due LIMIT 10", pid)
    episodes = db.all("SELECT content FROM memories WHERE project_id = ? AND kind = 'episode' ORDER BY updated_at DESC LIMIT 5", pid)
    parts = [f"You are working in the project \"{p['name']}\"." + (f" {p['description']}" if p["description"] else "")]
    if p["goals"]:
        parts.append(f"Goals: {p['goals']}")
    if p["instructions"]:
        parts.append(f"Project instructions from the user: {p['instructions']}")
    if decisions:
        parts.append("Decisions so far:\n" + "\n".join(f"- {d['text']}" for d in decisions))
    if questions:
        parts.append("Open questions:\n" + "\n".join(f"- [q{q['id']}] {q['text']}" for q in questions))
    if tasks:
        parts.append("Open tasks: " + "; ".join(t["title"] + (f" (due {t['due']})" if t["due"] else "") for t in tasks))
    if missions:
        parts.append("Recent missions: " + "; ".join(f"{m['goal'][:60]} ({m['status']})" for m in missions))
    if episodes:
        parts.append("What happened recently:\n" + "\n".join(f"- {e['content'][:200]}" for e in episodes))
    parts.append("Save project facts with remember (they stay in this project). Record decisions and open questions with project_note.")
    return "\n".join(parts)


def local_only(pid: int | None) -> bool:
    p = get(pid)
    return bool(p and p["local_only"])


def budget_problem(pid: int | None) -> str:
    """Why work in this project must not spend more this month, or ''."""
    p = get(pid)
    if not p or not p["budget_cents"]:
        return ""
    spent = month_spend(pid)
    if spent >= p["budget_cents"]:
        return (f"The project {p['name']} reached its monthly budget (${p['budget_cents'] / 100:.2f}; ${spent / 100:.2f} spent). "
                "Raise it in the project's settings or use a free or local model for it.")
    return ""


def stricter(global_mode: str, pid: int | None, group: str) -> str:
    """A project can narrow a permission (on -> ask -> off), never widen it."""
    p = get(pid)
    if not p:
        return global_mode
    order = {"on": 0, "ask": 1, "off": 2}
    local = p["permissions"].get(group, "on")
    return local if order[local] > order[global_mode] else global_mode


def bin_expired() -> None:
    """Projects in the bin longer than 30 days stay there until you delete them for good; nothing is removed silently."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=BIN_DAYS)).isoformat(timespec="seconds")
    return db.all("SELECT id, name FROM projects WHERE deleted_at IS NOT NULL AND deleted_at < ?", cutoff)
