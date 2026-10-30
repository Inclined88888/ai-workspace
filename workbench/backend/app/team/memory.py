"""Team people and tasks on the shared knowledge graph (feat/memory-core, PR #40), per its adoption notes
(docs/data-model.md §8):

    staff  -> upsert_entity("person", name, {"email","phone","role"}, ref=("team_members", id))
    tasks  -> upsert_entity("task", title, {...}, ref=("team_tasks", id)); link(task, "assigned_to", person);
              link(task, "part_of", project)
    status -> remember(..., "episodic", subject_id=person, project_id=project, scope="team:<id>")

The team tables stay the working store (the graph keeps a ref back to each row, as memory-core asks), so the board is
fast and works on builds without memory-core. Every change is mirrored here; a failure never breaks the team feature.
"""

from __future__ import annotations

import json
import logging

from .. import db

log = logging.getLogger("team.memory")


def _kn():
    try:
        from .. import knowledge  # noqa: PLC0415 - optional until feat/memory-core is merged

        return knowledge if getattr(knowledge, "API_VERSION", 0) >= 1 else None
    except ImportError:
        return None


def available() -> bool:
    return _kn() is not None


def scope() -> str:
    try:
        s = json.loads(db.get_setting("account_summary") or "{}")
        tid = (s.get("team") or {}).get("id", "")
    except ValueError:
        tid = ""
    return f"team:{tid or 'local'}"


def _ref_entity(kn, table: str, rid) -> dict | None:
    r = db.one("SELECT id FROM kn_entities WHERE ref_table = ? AND ref_id = ? AND deleted_at IS NULL", table, str(rid))
    return kn.get_entity(r["id"]) if r else None


def person(m: dict) -> int | None:
    kn = _kn()
    if not kn:
        return None
    try:
        c = m.get("contact") or {}
        e = kn.upsert_entity("person", m["name"], {"email": c.get("email", ""), "phone": c.get("phone", ""), "role": m.get("role", ""),
                                                  "skills": ", ".join(m.get("skills") or []), "relation": "team member"},
                             ref=("team_members", m["id"]), scope=scope(), source="team")
        return e["id"]
    except Exception as e:  # noqa: BLE001
        log.warning("team person mirror failed: %s", e.__class__.__name__)
        return None


def task(t: dict) -> int | None:
    kn = _kn()
    if not kn:
        return None
    try:
        e = kn.upsert_entity("task", t["title"], {"status": t["status"], "percent": str(t["percent"]), "due": t.get("due") or "",
                                                 "priority": str(t["priority"]), "source": t.get("source", "")},
                             ref=("team_tasks", t["id"]), scope=scope(), source="team")
        db.run("DELETE FROM kn_edges WHERE src = ? AND rel IN ('assigned_to', 'part_of')", e["id"])  # re-linked below
        if t.get("assignee_id"):
            from . import store

            pid = person(store.member(t["assignee_id"]))
            if pid:
                kn.link(e["id"], "assigned_to", pid, source="team")
        proj = project(t.get("project_id"))
        if proj:
            kn.link(e["id"], "part_of", proj, source="team")
        return e["id"]
    except Exception as e:  # noqa: BLE001
        log.warning("team task mirror failed: %s", e.__class__.__name__)
        return None


def project(pid) -> int | None:
    kn = _kn()
    if not kn or not pid:
        return None
    try:
        return kn.adapters.project_entity(int(pid))
    except Exception:  # noqa: BLE001 - projects are optional
        return None


def forget(table: str, rid) -> None:
    kn = _kn()
    if not kn:
        return
    try:
        e = _ref_entity(kn, table, rid)
        if e:
            kn.forget_entity(e["id"])
    except Exception as e:  # noqa: BLE001
        log.warning("team forget failed: %s", e.__class__.__name__)


def note(t: dict, member: dict | None, text: str, source: str) -> None:
    """A status update as an episodic memory about the person, filed under the task's project."""
    kn = _kn()
    if not kn or not text:
        return
    try:
        sid = person(member) if member else None
        kn.remember(text, "episodic", subject_id=sid, project_id=project(t.get("project_id")), scope=scope(),
                    source_kind=f"team:{source}", source_ref=f"team_tasks:{t['id']}")
    except Exception as e:  # noqa: BLE001
        log.warning("team note failed: %s", e.__class__.__name__)
