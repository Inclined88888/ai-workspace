"""Action history with undo (feat/v02-core).

One timeline of everything the assistant and its helpers did: chats answered, notes and files written, tasks,
calendar events, email drafted or sent, memories, skills, automations, work-site checks and changes, computer-use
runs and settings changes. Each change the assistant makes gets a history entry with what it needs to be undone:

* reversible in the app (a note, task, draft, file it made, memory, skill, schedule, project item, setting): one tap;
* reversible outside the app but visible to others (an event on Google / Outlook / CalDAV): one tap after a plain
  warning, because removing it there is itself a change outside the app the user approves;
* not reversible (sent email, a form submitted on a website, steps on the computer): "can't undo" plus how to fix it.

Snapshots (the note body before an append, the task before an edit, the memory before forget...) are encrypted at rest.
The run log (`actions`) stays the source of truth for what ran; this adds the undo side. Read-only lookups are listed on
request. Export (CSV / PDF) is free on every plan: exports are never gated (integration/v0.2).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from . import config, db
from .security import decrypt, encrypt, require_user

log = logging.getLogger("history")

SCHEMA = """
CREATE TABLE IF NOT EXISTS history_entries (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'assistant',     -- assistant | automation | agent | settings | you
  action_id INTEGER UNIQUE,                     -- the tool_call row in `actions`
  tool TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL,                       -- note | task | calendar | email | file | memory | skill | automation | site | computer | project | setting | other
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  conversation_id INTEGER,
  undo_kind TEXT NOT NULL DEFAULT '',           -- '' = nothing to undo; 'cannot' = not reversible (see fix)
  undo_enc TEXT NOT NULL DEFAULT '',            -- encrypted JSON the undo needs (snapshots, ids)
  confirm TEXT NOT NULL DEFAULT '',             -- warning shown before an undo that reaches outside the app
  fix TEXT NOT NULL DEFAULT '',                 -- what to do when it can't be undone
  fix_prompt TEXT NOT NULL DEFAULT '',          -- a chat request that helps fix it (e.g. draft a correction)
  status TEXT NOT NULL DEFAULT 'done',          -- done | undone | undo_failed
  undone_at TEXT,
  undo_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS history_at ON history_entries(at);
"""
_ready: set[str] = set()
_lock = threading.Lock()
_local = threading.local()


def ensure() -> None:
    key = str(config.DB_PATH)
    if key in _ready:
        return
    with _lock:
        if key not in _ready:
            db.conn().executescript(SCHEMA)
            _ready.add(key)


def _seal(v) -> str:
    return encrypt(json.dumps(v, default=str, ensure_ascii=False)) if v else ""


def _unseal(v: str) -> dict:
    try:
        return json.loads(decrypt(v)) if v else {}
    except ValueError:
        return {}


def record(category: str, title: str, *, detail: str = "", source: str = "assistant", tool: str = "", action_id: int | None = None,
           conversation_id: int | None = None, undo_kind: str = "", undo: dict | None = None, confirm: str = "", fix: str = "",
           fix_prompt: str = "") -> int:
    """Add one entry. Other features can call this for work they do on the user's behalf."""
    ensure()
    from .redact import redact

    return db.run("INSERT INTO history_entries(at, source, action_id, tool, category, title, detail, conversation_id, undo_kind, "
                  "undo_enc, confirm, fix, fix_prompt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  db.now(), source, action_id, tool, category, redact(title)[:300], redact(detail)[:2000], conversation_id,
                  undo_kind, _seal(undo or {}), confirm, fix[:1000], fix_prompt[:2000])


# Capturing tool calls ------------------------------------------------------------------------------------------------
def _max_id(table: str) -> int:
    try:
        return (db.one(f"SELECT MAX(id) AS m FROM {table}") or {}).get("m") or 0
    except Exception:  # noqa: BLE001 - optional tables
        return 0


def before_tool(name: str, args: dict) -> dict:
    """Snapshot what a change is about to overwrite. Called just before a tool runs; never raises."""
    try:
        a = {k: v for k, v in args.items() if k != "ctx"}
        if name == "append_to_note":
            d = db.one("SELECT id, body FROM docs WHERE id = ?", a.get("doc_id"))
            return {"doc": d} if d else {}
        if name == "update_task":
            t = db.one("SELECT * FROM tasks WHERE id = ?", a.get("task_id"))
            return {"task": t} if t else {}
        if name == "forget":
            from .adapt import knowledge

            f = knowledge.get(int(a.get("memory_id")))
            return {"fact": {"content": f["content"], "category": f["category"], "pinned": f.get("pinned")}}
        if name == "save_skill":
            s = db.one("SELECT * FROM skills WHERE name = ?", str(a.get("name", "")).strip().lower().replace(" ", "-"))
            return {"skill": s}
        if name in ("create_event", "site_schedule"):
            return {"max_event": _max_id("events")}
        if name in ("use_computer", "run_python", "make_document", "edit_spreadsheet"):
            return {"max_file": _max_id("work_files")}
    except Exception as e:  # noqa: BLE001
        log.debug("snapshot for %s skipped: %s", name, e)
    return {}


def _source(ctx) -> str:
    ch = getattr(ctx, "channel", "") or ""
    return "automation" if ch == "schedule" else "agent" if ch == "agent" else "assistant"


def after_tool(name: str, args: dict, result, ctx, snap: dict | None, action_id: int | None) -> None:
    """Record a change that ran (verified or ok). Read-only tools are not recorded here; the run log has them."""
    try:
        from . import assistant

        t = assistant.TOOL_MAP.get(name)
        if not t or t.effect == "read" and name not in ("site_schedule",):
            return
        if name == "site_schedule" and not args.get("add_to_calendar"):
            return
        entry = describe(name, {k: v for k, v in args.items() if k != "ctx"}, result, snap or {})
        if not entry:
            return
        record(entry.pop("category"), entry.pop("title"), source=_source(ctx), tool=name, action_id=action_id,
               conversation_id=getattr(ctx, "conversation_id", None), **entry)
    except Exception as e:  # noqa: BLE001 - history must never break a tool call
        log.warning("history for %s not recorded: %s", name, e)


def _r(result) -> dict:
    return result if isinstance(result, dict) else {}


def describe(name: str, a: dict, result, snap: dict) -> dict | None:
    """Category, title and undo recipe for one change."""
    r = _r(result)
    q = lambda s, n=80: (str(s or "")[:n] + ("…" if len(str(s or "")) > n else ""))  # noqa: E731
    if name == "create_note":
        return {"category": "note", "title": f"Created {a.get('kind') or 'note'} “{q(a.get('title'))}”",
                "undo_kind": "delete_doc", "undo": {"doc_id": r.get("id")}}
    if name == "append_to_note":
        d = snap.get("doc") or {}
        return {"category": "note", "title": f"Added to note #{a.get('doc_id')}", "detail": q(a.get("text"), 300),
                "undo_kind": "restore_doc" if d else "cannot", "undo": {"doc_id": d.get("id"), "body": d.get("body"), "added": a.get("text")},
                "fix": "" if d else "Open the note and remove the added text."}
    if name == "create_task":
        return {"category": "task", "title": f"Added task “{q(a.get('title'))}”" + (f", due {a['due']}" if a.get("due") else ""),
                "undo_kind": "delete_task", "undo": {"task_id": r.get("id")}}
    if name == "update_task":
        t = snap.get("task") or {}
        what = f"marked {a['status']}" if a.get("status") else "edited"
        return {"category": "task", "title": f"Task “{q(t.get('title') or a.get('task_id'))}” {what}",
                "undo_kind": "restore_task" if t else "cannot", "undo": {"task": t}, "fix": "" if t else "Edit the task on the Tasks page."}
    if name in ("create_event", "site_schedule"):
        new = db.all("SELECT e.id, e.title, e.start, e.uid, c.kind, c.name AS cal FROM events e JOIN calendars c ON c.id = e.calendar_id "
                     "WHERE e.id > ? ORDER BY e.id", snap.get("max_event", 0)) if snap.get("max_event") is not None else []
        if name == "create_event" and r.get("id"):
            new = [e for e in new if e["id"] == r["id"]] or new
        if not new:
            return {"category": "calendar", "title": "Calendar: nothing was added", "undo_kind": ""}
        remote = [e for e in new if e["kind"] != "local"]
        invited = bool(name == "create_event" and a.get("attendees"))
        title = (f"Added “{q(new[0]['title'])}” to {new[0]['cal']}" if len(new) == 1 else f"Added {len(new)} dated items to your calendar")
        confirm = ""
        if remote:
            confirm = f"This also removes it from {remote[0]['cal']}."
            if invited:
                confirm += " The people you invited will get a cancellation email."
        return {"category": "calendar", "title": title, "detail": "; ".join(f"{e['title']}, {_when(e['start'])}" for e in new[:5]),
                "undo_kind": "delete_events", "undo": {"events": [{"id": e["id"], "uid": e["uid"], "kind": e["kind"]} for e in new]},
                "confirm": confirm}
    if name == "save_email_draft":
        return {"category": "email", "title": f"Drafted email to {q(a.get('to'), 60)}: “{q(a.get('subject'), 60)}”",
                "undo_kind": "discard_draft", "undo": {"draft_id": r.get("draft_id")}}
    if name == "send_email":
        return {"category": "email", "title": f"Sent email to {q(a.get('to'), 60)}: “{q(a.get('subject'), 60)}”",
                "undo_kind": "cannot",
                "fix": "Sent email can't be called back. If something was wrong, send a short correction to the same people.",
                "fix_prompt": f"Draft a short, polite correction to my email to {a.get('to')} with the subject \"{a.get('subject')}\". "
                              "Ask me what to correct first."}
    if name == "remember":
        if r.get("saved") is None:
            return None
        return {"category": "memory", "title": f"Remembered “{q(a.get('content'))}”", "undo_kind": "delete_fact",
                "undo": {"fact_id": r.get("saved")}}
    if name == "forget":
        f = snap.get("fact") or {}
        return {"category": "memory", "title": f"Forgot “{q(f.get('content') or a.get('memory_id'))}”",
                "undo_kind": "restore_fact" if f else "cannot", "undo": {"fact": f}}
    if name == "save_skill":
        prev = snap.get("skill")
        return {"category": "skill", "title": f"{'Updated' if prev else 'Saved'} skill {a.get('name')}",
                "undo_kind": "restore_skill" if prev else "delete_skill",
                "undo": {"skill": prev} if prev else {"name": str(a.get("name", "")).strip().lower().replace(" ", "-")}}
    if name == "schedule_task":
        return {"category": "automation", "title": f"Set up automation “{q(a.get('name'))}” ({a.get('cron')})",
                "undo_kind": "delete_schedule", "undo": {"schedule_id": r.get("id")}}
    if name == "watch_site":
        return {"category": "automation", "title": f"Set up a site check: {q(a.get('request'))}",
                "undo_kind": "delete_watch", "undo": {"watch_id": r.get("id")}}
    if name in ("make_document", "edit_spreadsheet"):
        return {"category": "file", "title": f"Made file {r.get('name') or a.get('title')}", "undo_kind": "delete_files",
                "undo": {"file_ids": [r.get("id")] if r.get("id") else []}}
    if name == "run_python":
        outs = [o["id"] for o in r.get("outputs") or []]
        return {"category": "file", "title": "Ran a calculation" + (f" and saved {len(outs)} file(s)" if outs else ""),
                "detail": q(r.get("stdout"), 300), "undo_kind": "delete_files" if outs else "", "undo": {"file_ids": outs}}
    if name == "create_project":
        return {"category": "project", "title": f"Started project {r.get('name')}", "undo_kind": "delete_project",
                "undo": {"project_id": r.get("id")}}
    if name == "add_to_project":
        return {"category": "project", "title": f"Filed a {a.get('kind')} in {r.get('project')}: {q(r.get('title'))}",
                "undo_kind": "remove_project_item", "undo": {"item_id": r.get("id")}}
    if name == "site_action":
        return {"category": "site", "title": f"Changed something on a work site (form {q(a.get('form'), 40)})",
                "detail": q(a.get("values"), 300), "undo_kind": "cannot",
                "fix": "Websites don't offer an undo. Open the site and change it back, or ask me to fill the form again with the old values."}
    if name == "use_computer":
        files = r.get("files") or []
        cmds = [c for c in (r.get("commands") or []) if c]
        detail = (f"Files: {', '.join(map(str, files[:8]))}. " if files else "") + (f"Commands: {'; '.join(cmds[:5])}" if cmds else "")
        return {"category": "computer", "title": f"Used your computer: {q(a.get('goal'))}", "detail": detail,
                "undo_kind": "cannot",
                "fix": "Steps in other apps can't be undone from here. Every step and screenshot is on the Computer page"
                       + (". Files it changed are listed above: restore them from Time Machine (Mac) or File History (Windows), "
                          "or use the app's own Undo." if files else ".")}
    if name == "browse_web":
        return {"category": "site", "title": f"Used the browser: {q(a.get('goal'))}", "undo_kind": "cannot",
                "fix": "Anything it changed on a website has to be changed back on that website. The steps are on the Browser page."}
    if name == "run_playbook":
        return {"category": "automation", "title": f"Replayed a task you taught (#{a.get('playbook_id')})", "undo_kind": "cannot",
                "fix": "See each step on the Show me how page; change anything back on the site or app it used."}
    if name == "delegate_task":
        return {"category": "automation", "title": f"Asked {a.get('agent_name')} to {q(a.get('title'))}", "undo_kind": "cannot",
                "fix": "Cancel or edit the task on the Company page."}
    if name == "run_skill_script":
        return {"category": "skill", "title": f"Ran {a.get('script')} from skill {a.get('skill')}", "undo_kind": "cannot",
                "fix": "Skill scripts run in a sandbox with only the permissions you granted. Check the skill's output."}
    return {"category": "other", "title": f"Ran {name}", "undo_kind": "cannot",
            "fix": "This can't be undone automatically. Ask me in chat to change it back."}


# Settings changes ---------------------------------------------------------------------------------------------------
SETTING_LABELS = {
    "workspace_name": "Workspace name", "default_model": "Default model", "assistant_name": "Assistant name",
    "timezone": "Time zone", "learning_enabled": "Learn memories and skills", "appearance": "Look (theme, font, size)",
    "adapt_memory": "Learn about me", "adapt_memory_context": "Use what I know in answers", "adapt_jobs": "Notice my routines",
    "adapt_suggest": "Offer to take things over", "router_mode": "Smart model routing", "checked_default": "Check answers by default",
}
# integration/v0.2: the function we wrap is whatever db.set_setting is when install() runs, so this coexists with any
# other wrapper (installed before us it is called through; installed after us it calls through us).
_original_set_setting = db.set_setting


def _tracked_set_setting(key: str, value: str) -> None:
    if key not in SETTING_LABELS or getattr(_local, "quiet", False):
        return _original_set_setting(key, value)
    old = db.get_setting(key)
    _original_set_setting(key, value)
    if old == value:
        return
    try:
        ensure()
        recent = db.one("SELECT id, undo_enc FROM history_entries WHERE category = 'setting' AND tool = ? AND status = 'done' "
                        "AND at > ? ORDER BY id DESC LIMIT 1", key,
                        (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat(timespec="seconds"))
        if recent:  # typing in a field or flicking a switch twice is one change
            u = _unseal(recent["undo_enc"])
            if u.get("old") == value:
                db.run("DELETE FROM history_entries WHERE id = ?", recent["id"])
            else:
                db.run("UPDATE history_entries SET at = ?, title = ? WHERE id = ?", db.now(), _setting_title(key, value), recent["id"])
            return
        record("setting", _setting_title(key, value), source="settings", tool=key, undo_kind="restore_setting",
               undo={"key": key, "old": old})
    except Exception as e:  # noqa: BLE001
        log.debug("setting change not recorded: %s", e)


def _setting_title(key: str, value: str) -> str:
    label = SETTING_LABELS[key]
    if key == "appearance":
        try:
            v = json.loads(value)
            return f"{label} changed to {v.get('theme', '?')}"
        except ValueError:
            return f"{label} changed"
    if value in ("0", "1"):
        return f"{label} turned {'on' if value == '1' else 'off'}"
    return f"{label} changed to {value[:60]}"


def install() -> None:
    """Track user-facing settings (one line in main.py). Idempotent."""
    global _original_set_setting, _installed
    if _installed or db.set_setting is _tracked_set_setting:
        return  # once only: a wrapper added after us calls through us, and we never wrap ourselves twice
    _original_set_setting = db.set_setting
    db.set_setting = _tracked_set_setting
    _installed = True


_installed = False


# Undo ---------------------------------------------------------------------------------------------------------------
def _delete_remote_event(e: dict) -> None:
    ev = db.one("SELECT e.*, c.kind AS ckind, c.id AS cal_id, c.linked_account_id, c.url, c.username, c.password_enc "
                "FROM events e JOIN calendars c ON c.id = e.calendar_id WHERE e.id = ?", e["id"])
    if not ev:
        return
    remote_uid = ev["uid"].split("|")[0]
    if ev["ckind"] in ("google", "microsoft"):
        from . import cloud_mail

        cal = db.one("SELECT * FROM calendars WHERE id = ?", ev["cal_id"])
        acct = cloud_mail._account_for_calendar(cal)  # noqa: SLF001 - same module family
        if ev["ckind"] == "google":
            cloud_mail._call(acct, "DELETE", f"{cloud_mail.gcal()}/calendars/primary/events/{remote_uid}", ok_404=True,  # noqa: SLF001
                             params={"sendUpdates": "all"})
        else:
            cloud_mail._call(acct, "DELETE", f"{cloud_mail.graph()}/me/events/{remote_uid}", ok_404=True)  # noqa: SLF001
    elif ev["ckind"] == "caldav":
        from . import calendar_sync

        cal = db.one("SELECT * FROM calendars WHERE id = ?", ev["cal_id"])
        try:
            calendar_sync._dav_calendar(cal).event_by_uid(remote_uid).delete()  # noqa: SLF001
        except Exception as ex:  # noqa: BLE001
            if "not found" not in str(ex).lower() and "404" not in str(ex):
                raise HTTPException(502, f"The CalDAV server would not delete it: {ex}") from ex
    db.run("DELETE FROM events WHERE id = ?", e["id"])


def _undo(kind: str, u: dict) -> str:
    """Do the undo; returns a plain-English note. Raises HTTPException when it can't."""
    if kind == "delete_doc":
        if not db.one("SELECT 1 FROM docs WHERE id = ?", u.get("doc_id")):
            return "It was already deleted."
        db.run("DELETE FROM docs WHERE id = ?", u["doc_id"])
        return "Note deleted."
    if kind == "restore_doc":
        d = db.one("SELECT body FROM docs WHERE id = ?", u.get("doc_id"))
        if not d:
            raise HTTPException(409, "That note no longer exists.")
        added = (u.get("added") or "").strip()
        if added and added not in d["body"]:
            raise HTTPException(409, "The added text isn't in the note any more, so there's nothing to take out.")
        db.run("UPDATE docs SET body = ?, updated_at = ? WHERE id = ?", u.get("body") or "", db.now(), u["doc_id"])
        return "The note is back to how it was."
    if kind == "delete_task":
        db.run("DELETE FROM tasks WHERE id = ?", u.get("task_id"))
        return "Task removed."
    if kind == "restore_task":
        t = u.get("task") or {}
        if not db.one("SELECT 1 FROM tasks WHERE id = ?", t.get("id")):
            raise HTTPException(409, "That task was deleted since.")
        db.run("UPDATE tasks SET title = ?, notes = ?, status = ?, priority = ?, due = ?, project = ?, completed_at = ?, updated_at = ? "
               "WHERE id = ?", t["title"], t["notes"], t["status"], t["priority"], t["due"], t["project"], t["completed_at"], db.now(), t["id"])
        return "Task put back the way it was."
    if kind == "delete_events":
        gone = 0
        for e in u.get("events") or []:
            if e.get("kind") == "local":
                db.run("DELETE FROM events WHERE id = ?", e["id"])
            else:
                _delete_remote_event(e)
            gone += 1
        return f"Removed {gone} event{'s' if gone != 1 else ''} from your calendar."
    if kind == "discard_draft":
        d = db.one("SELECT m.kind FROM mail_drafts d LEFT JOIN mail_accounts m ON m.id = d.account_id WHERE d.id = ?", u.get("draft_id"))
        db.run("DELETE FROM mail_drafts WHERE id = ?", u.get("draft_id"))
        if d and d.get("kind") in ("google", "microsoft"):
            return f"Draft discarded here. Also delete the copy in {'Gmail' if d['kind'] == 'google' else 'Outlook'} Drafts."
        return "Draft discarded."
    if kind == "delete_fact":
        from .adapt import knowledge

        try:
            knowledge.delete(int(u["fact_id"]))
        except HTTPException:
            return "It was already forgotten."
        return "Forgotten."
    if kind == "restore_fact":
        from .adapt import knowledge

        f = u.get("fact") or {}
        knowledge.add(f.get("content", ""), f.get("category") or "fact", "user", confidence=0.9, pinned=bool(f.get("pinned")))
        return "Remembered again."
    if kind == "delete_skill":
        db.run("DELETE FROM skills WHERE name = ? AND source != 'github'", u.get("name"))
        return "Skill removed."
    if kind == "restore_skill":
        s = u.get("skill") or {}
        db.run("UPDATE skills SET description = ?, steps = ?, updated_at = ? WHERE id = ?", s["description"], s["steps"], db.now(), s["id"])
        return "Skill put back to the earlier version."
    if kind == "delete_schedule":
        db.run("DELETE FROM schedules WHERE id = ?", u.get("schedule_id"))
        return "Automation removed. It won't run again."
    if kind == "delete_watch":
        db.run("DELETE FROM site_watches WHERE id = ?", u.get("watch_id"))
        return "Site check removed."
    if kind == "delete_files":
        from . import analysis

        n = 0
        for fid in u.get("file_ids") or []:
            try:
                analysis.remove(int(fid))
                n += 1
            except HTTPException:
                pass
        return f"Deleted {n} file{'s' if n != 1 else ''} it made."
    if kind == "delete_project":
        from . import projects

        try:
            projects.delete(int(u["project_id"]))
        except HTTPException:
            return "That project was already deleted."
        return "Project removed. Its files and chats are still in the app."
    if kind == "remove_project_item":
        from . import projects

        try:
            projects.remove_item(int(u["item_id"]))
        except HTTPException:
            return "It was already taken out."
        return "Taken out of the project."
    if kind == "restore_setting":
        _local.quiet = True
        try:
            if u.get("old") is None:
                db.run("DELETE FROM settings WHERE key = ?", u["key"])
            else:
                db.set_setting(u["key"], u["old"])
        finally:
            _local.quiet = False
        return f"{SETTING_LABELS.get(u['key'], u['key'])} is back to how it was."
    raise HTTPException(400, "This can't be undone automatically.")


def undo(entry_id: int, confirmed: bool = False) -> dict:
    ensure()
    e = db.one("SELECT * FROM history_entries WHERE id = ?", entry_id)
    if not e:
        raise HTTPException(404, "Not in your history")
    if e["status"] == "undone":
        raise HTTPException(409, "Already undone")
    if e["undo_kind"] in ("", "cannot"):
        raise HTTPException(400, e["fix"] or "There's nothing to undo here.")
    if e["confirm"] and not confirmed:
        raise HTTPException(409, {"error": "confirm", "message": e["confirm"]})
    try:
        note = _undo(e["undo_kind"], _unseal(e["undo_enc"]))
    except HTTPException as ex:
        db.run("UPDATE history_entries SET status = 'undo_failed', undo_note = ? WHERE id = ?", str(ex.detail)[:500], entry_id)
        raise
    db.run("UPDATE history_entries SET status = 'undone', undone_at = ?, undo_note = ? WHERE id = ?", db.now(), note, entry_id)
    return entry(entry_id)


# Timeline -----------------------------------------------------------------------------------------------------------
WHO = {"assistant": "Assistant", "automation": "Automation", "agent": "Team member", "settings": "Settings", "you": "You"}
CATEGORIES = {"chat": "Chats", "note": "Notes", "task": "Tasks", "calendar": "Calendar", "email": "Email", "file": "Files",
              "memory": "Memory", "skill": "Skills", "automation": "Automations", "site": "Websites", "computer": "Computer",
              "project": "Projects", "setting": "Settings", "approval": "Waiting for you", "lookup": "Look-ups", "other": "Other"}


def _entry_out(e: dict) -> dict:
    can = e["undo_kind"] not in ("", "cannot") and e["status"] != "undone"
    return {"id": f"h{e['id']}", "at": e["at"], "category": e["category"], "who": WHO.get(e["source"], e["source"]),
            "title": e["title"], "detail": e["detail"], "tool": e["tool"], "conversation_id": e["conversation_id"],
            "status": e["status"], "undo": {"available": can, "confirm": e["confirm"] if can else "",
                                             "reversible": e["undo_kind"] not in ("cannot",), "note": e["undo_note"]},
            "fix": e["fix"] if e["undo_kind"] == "cannot" else "", "fix_prompt": e["fix_prompt"], "undone_at": e["undone_at"]}


def entry(entry_id: int) -> dict:
    e = db.one("SELECT * FROM history_entries WHERE id = ?", entry_id)
    if not e:
        raise HTTPException(404, "Not in your history")
    return _entry_out(e)


def _plain(e: dict, **kw) -> dict:
    base = {"detail": "", "tool": "", "conversation_id": None, "status": "done", "fix": "", "fix_prompt": "", "undone_at": None,
            "undo": {"available": False, "confirm": "", "reversible": True, "note": ""}}
    return base | e | kw


def timeline(limit: int = 100, before: str | None = None, category: str = "", q: str = "", include_lookups: bool = False,
             project_id: int | None = None) -> list[dict]:
    """Newest first, everything merged. `before` is an ISO time for paging."""
    ensure()
    before = before or "9999"
    lim = max(1, min(int(limit), 500))
    out: list[dict] = []
    conv_filter: set[int] | None = None
    if project_id:
        from . import projects

        projects.ensure()
        conv_filter = {int(r["ref"]) for r in db.all("SELECT ref FROM project_items WHERE project_id = ? AND kind = 'chat'", project_id)}
    for e in db.all("SELECT * FROM history_entries WHERE at < ? ORDER BY at DESC, id DESC LIMIT ?", before, lim * 2):
        out.append(_entry_out(e))
    convs = {c["id"]: c for c in db.all("SELECT id, title, channel FROM conversations")}
    for a in db.all("SELECT id, ts, channel, conversation_id, outcome, failure_reason FROM actions WHERE kind = 'turn' AND ts < ? "
                    "AND parent_action_id IS NULL ORDER BY id DESC LIMIT ?", before, lim):
        c = convs.get(a["conversation_id"]) or {}
        auto = a["channel"] in ("schedule", "agent")
        title = (f"Automation ran: {c.get('title', '')}" if a["channel"] == "schedule" else
                 f"Team member worked on: {c.get('title', '')}" if a["channel"] == "agent" else f"Answered in chat “{c.get('title', 'Chat')}”")
        failed = a["outcome"] in ("model_error", "interrupted", "error", "step_limit", "unbacked")
        out.append(_plain({"id": f"t{a['id']}", "at": a["ts"], "category": "automation" if auto else "chat",
                           "who": "Automation" if auto else "Assistant", "title": title, "conversation_id": a["conversation_id"],
                           "detail": ("Didn't finish: " + (a["failure_reason"] or a["outcome"])) if failed else "",
                           "undo": {"available": False, "confirm": "", "reversible": True,
                                    "note": "Nothing to undo: answering doesn't change anything. Changes it made are listed separately."}}))
    for a in db.all("SELECT a.id, a.ts, a.tool, a.outcome, a.detail, a.conversation_id FROM actions a WHERE a.kind = 'tool_call' "
                    "AND a.outcome IN ('held', 'denied') AND a.ts < ? ORDER BY a.id DESC LIMIT ?", before, lim):
        d = db.one("SELECT outcome FROM actions WHERE kind = 'decision' AND parent_action_id = ?", a["id"])
        state = ("You said no to " if d and d["outcome"] == "denied" else "You approved " if d else
                 "Refused (needed your OK, nobody was there): " if a["outcome"] == "denied" else "Waiting for your OK: ")
        out.append(_plain({"id": f"a{a['id']}", "at": a["ts"], "category": "approval", "who": "Assistant",
                           "title": state + a["tool"].replace("_", " "), "detail": a["detail"], "tool": a["tool"],
                           "conversation_id": a["conversation_id"], "status": "waiting" if not d and a["outcome"] == "held" else "done"}))
    if include_lookups:
        for a in db.all("SELECT id, ts, tool, conversation_id, args FROM actions WHERE kind = 'tool_call' AND effect = 'read' "
                        "AND outcome IN ('ok', 'verified') AND ts < ? ORDER BY id DESC LIMIT ?", before, lim):
            out.append(_plain({"id": f"l{a['id']}", "at": a["ts"], "category": "lookup", "who": "Assistant",
                               "title": "Looked up: " + a["tool"].replace("_", " "), "detail": (a["args"] or "")[:200], "tool": a["tool"],
                               "conversation_id": a["conversation_id"]}))
    try:
        from . import sites

        sites.ensure()
        for c in db.all("SELECT k.id, k.at, k.status, k.digest, s.name FROM site_checks k JOIN sites s ON s.id = k.site_id "
                        "WHERE k.at < ? ORDER BY k.id DESC LIMIT ?", before, lim):
            out.append(_plain({"id": f"s{c['id']}", "at": c["at"], "category": "site", "who": "Site check",
                               "title": f"Checked {c['name']}" + ("" if c["status"] == "ok" else f" ({c['status']})"),
                               "detail": (c["digest"] or "")[:300],
                               "undo": {"available": False, "confirm": "", "reversible": True, "note": "Read only: nothing changed on the site."}}))
        for x in db.all("SELECT a.id, a.decided_at, a.status, a.summary, a.result, s.name, s.url FROM site_actions a JOIN sites s ON s.id = a.site_id "
                        "WHERE a.status IN ('done', 'failed') AND a.decided_at < ? ORDER BY a.id DESC LIMIT ?", before, lim):
            out.append(_plain({"id": f"x{x['id']}", "at": x["decided_at"], "category": "site", "who": "You approved",
                               "title": f"{'Changed' if x['status'] == 'done' else 'Tried to change'} {x['name']}: {x['summary'][:120]}",
                               "detail": (x["result"] or "")[:300],
                               "undo": {"available": False, "confirm": "", "reversible": False, "note": ""},
                               "fix": f"Websites don't offer an undo. Open {x['url']} and change it back."}))
    except Exception as e:  # noqa: BLE001
        log.debug("site history skipped: %s", e)
    try:
        for r in db.all("SELECT r.id, r.created_at, r.status, r.trigger, p.name FROM adapt_runs r JOIN adapt_playbooks p ON p.id = r.playbook_id "
                        "WHERE r.created_at < ? ORDER BY r.id DESC LIMIT ?", before, lim):
            out.append(_plain({"id": f"p{r['id']}", "at": r["created_at"], "category": "automation",
                               "who": "Automation" if r["trigger"] == "routine" else "Assistant",
                               "title": f"Replayed “{r['name']}” ({r['status']})",
                               "undo": {"available": False, "confirm": "", "reversible": False, "note": ""},
                               "fix": "Each step is on the Show me how page. Change anything back in the site or app it used."}))
    except Exception:  # noqa: BLE001 - adapt tables appear on first use
        pass
    if category:
        out = [e for e in out if e["category"] == category]
    if q.strip():
        words = [w for w in re.split(r"\s+", q.lower()) if w]
        out = [e for e in out if all(w in (e["title"] + " " + e["detail"]).lower() for w in words)]
    if conv_filter is not None:
        out = [e for e in out if e.get("conversation_id") in conv_filter or e["category"] == "project" and e["tool"]]
    seen, uniq = set(), []
    for e in sorted(out, key=lambda e: (e["at"] or "", e["id"]), reverse=True):
        if e["id"] not in seen:
            seen.add(e["id"])
            uniq.append(e)
    return uniq[:lim]


# Export -------------------------------------------------------------------------------------------------------------
def _when(start: str) -> str:
    """An event start the way people say it, in the user's time zone: "Tue 3 Nov, 9:00 am" (all-day: "Tue 3 Nov")."""
    try:
        if len(start or "") == 10:
            d = datetime.fromisoformat(start)
            return f"{d:%a} {d.day} {d:%b}"
        from .assistant import local_now

        d = datetime.fromisoformat(start.replace("Z", "+00:00"))
        d = d.astimezone(local_now().tzinfo) if d.tzinfo else d
        return f"{d:%a} {d.day} {d:%b}, {d.hour % 12 or 12}:{d:%M} {'am' if d.hour < 12 else 'pm'}"
    except Exception:  # noqa: BLE001
        return start or ""


def _local_time(iso: str) -> str:
    try:
        from .assistant import local_now

        tz = local_now().tzinfo
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return iso


def _status_text(e: dict) -> str:
    if e["status"] == "undone":
        return "Undone"
    if e["undo"]["available"]:
        return "Can undo"
    if e["fix"]:
        return "Can't undo"
    return ""


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["When", "Who", "Kind", "What happened", "Details", "Undo", "How to fix"])
    for e in rows:
        w.writerow([_local_time(e["at"]), e["who"], CATEGORIES.get(e["category"], e["category"]), e["title"], e["detail"],
                    _status_text(e), e["fix"]])
    return buf.getvalue()


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def to_pdf(rows: list[dict], title: str = "Action history") -> bytes:
    """A plain, dependency-free PDF (Helvetica, A4), one line per entry with details wrapped underneath."""
    import textwrap

    lines: list[tuple[int, str]] = [(14, title), (9, f"Exported {_local_time(db.now())}  ·  {len(rows)} entries"), (9, "")]
    for e in rows:
        status = _status_text(e)
        lines.append((10, f"{_local_time(e['at'])}   {e['who']} · {CATEGORIES.get(e['category'], e['category'])}"
                          + (f"   [{status}]" if status else "")))
        for chunk in textwrap.wrap(e["title"], 95) or [""]:
            lines.append((10, "    " + chunk))
        for chunk in textwrap.wrap(e["detail"], 105)[:3]:
            lines.append((8, "      " + chunk))
        if e["fix"]:
            for chunk in textwrap.wrap("How to fix: " + e["fix"], 105)[:3]:
                lines.append((8, "      " + chunk))
        lines.append((6, ""))
    pages, page, y = [], [], 800
    for size, text in lines:
        step = size + 4
        if y - step < 40:
            pages.append(page)
            page, y = [], 800
        y -= step
        page.append((size, y, text.encode("latin-1", "replace").decode("latin-1")))
    pages.append(page)
    objs: list[bytes] = []
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_id = 3 + len(pages) * 2
    for i, pg in enumerate(pages):
        stream = "BT\n" + "\n".join(f"/F1 {s} Tf 1 0 0 1 40 {yy} Tm ({_pdf_escape(t)}) Tj" for s, yy, t in pg) + "\nET"
        data = stream.encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font_id} 0 R >> >> "
                    f"/Contents {4 + i * 2} 0 R >>".encode())
        objs.append(b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


# API ----------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/history", dependencies=[Depends(require_user), Depends(ensure)])


class UndoIn(BaseModel):
    confirm: bool = False


@router.get("")
def api_timeline(limit: int = Query(100, ge=1, le=500), before: str | None = None, category: str = "", q: str = "",
                 lookups: bool = False, project_id: int | None = None):
    rows = timeline(limit, before, category, q, lookups, project_id)
    return {"entries": rows, "categories": CATEGORIES, "export_allowed": True,  # integration/v0.2: exports are never gated
           
            "next_before": rows[-1]["at"] if len(rows) >= limit else None}


@router.post("/{entry_id}/undo")
def api_undo(entry_id: str, data: UndoIn):
    if not entry_id.startswith("h") or not entry_id[1:].isdigit():
        raise HTTPException(400, "This one has nothing to undo. Changes it led to are listed as their own entries.")
    return undo(int(entry_id[1:]), data.confirm)


@router.get("/export.csv")
def api_csv(category: str = "", q: str = "", lookups: bool = False, project_id: int | None = None):
    # integration/v0.2: exports are never gated (data portability), on any plan
    body = to_csv(timeline(500, None, category, q, lookups, project_id))
    return Response("\ufeff" + body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="action-history.csv"'})


@router.get("/export.pdf")
def api_pdf(category: str = "", q: str = "", lookups: bool = False, project_id: int | None = None):
    # integration/v0.2: exports are never gated (data portability), on any plan
    data = to_pdf(timeline(500, None, category, q, lookups, project_id))
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="action-history.pdf"'})

