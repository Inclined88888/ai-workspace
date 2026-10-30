"""Jira, Asana and Trello (feat/team-lead), each with the lead's own personal token.

What it does: brings open issues/tasks/cards assigned to people on your team into the Team board (matched by email or
name), keeps title, due date and status in step on each sync, and never deletes anything on either side. Writing status
back to the tracker is not done yet: the tracker stays the place to change it, and the board follows.

Tokens are kept encrypted on this computer and only sent to the tracker's own address.
Jira: site (https://yours.atlassian.net), account email, API token (id.atlassian.com > Security > API tokens).
Asana: personal access token (My settings > Apps > Developer apps) and a workspace or project id.
Trello: API key + token (trello.com/app-key) and a board id.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from fastapi import HTTPException

from ..security import decrypt, encrypt
from . import store

log = logging.getLogger("team.trackers")
KINDS = ("jira", "asana", "trello")
FIELDS = {"jira": ("site", "email", "token", "jql"), "asana": ("token", "project"), "trello": ("key", "token", "board")}
SECRET = {"token", "key"}
_transport: httpx.BaseTransport | None = None  # tests swap in a MockTransport


def _cfg(kind: str) -> dict:
    raw = store.setting(f"tracker_{kind}")
    if not raw:
        return {}
    d = json.loads(raw)
    return {k: decrypt(v) if k in SECRET and v else v for k, v in d.items()}


def configure(kind: str, values: dict) -> dict:
    if kind not in KINDS:
        raise HTTPException(404, "Unknown tracker")
    old = _cfg(kind)
    d = {k: str(values.get(k, old.get(k, "")) or "").strip() for k in FIELDS[kind]}
    if kind == "jira" and d["site"] and not re.fullmatch(r"https://[a-z0-9-]+\.atlassian\.net/?", d["site"]):
        raise HTTPException(400, "Use your Jira address, like https://yourteam.atlassian.net")
    if not any(d.values()):
        store.set_setting(f"tracker_{kind}", "")
        return status()
    store.set_setting(f"tracker_{kind}", json.dumps({k: encrypt(v) if k in SECRET and v else v for k, v in d.items()}))
    return status()


def status() -> dict:
    out = {}
    for k in KINDS:
        c = _cfg(k)
        out[k] = {"connected": all(c.get(f) for f in FIELDS[k] if f != "jql"),
                  "fields": {f: ("set" if c.get(f) else "") if f in SECRET else c.get(f, "") for f in FIELDS[k]},
                  "last_sync": store.setting(f"tracker_{k}_synced")}
    return out


def _http(**kw) -> httpx.Client:
    return httpx.Client(timeout=20, transport=_transport, **kw)


def _status(word: str) -> str:
    w = (word or "").lower()
    if w in ("done", "closed", "resolved", "complete", "completed"):
        return "done"
    if "block" in w:
        return "blocked"
    if "progress" in w or w in ("doing", "review", "in review"):
        return "doing"
    return "todo"


def fetch_jira(c: dict) -> list[dict]:
    jql = c.get("jql") or "assignee is not EMPTY AND statusCategory != Done ORDER BY duedate"
    with _http(base_url=c["site"].rstrip("/"), auth=(c["email"], c["token"])) as h:
        r = h.get("/rest/api/3/search", params={"jql": jql, "maxResults": 100, "fields": "summary,assignee,duedate,status,priority"})
        r.raise_for_status()
    out = []
    for i in r.json().get("issues", []):
        f = i.get("fields") or {}
        a = f.get("assignee") or {}
        out.append({"ref": f"jira:{i['key']}", "title": f"{i['key']} {f.get('summary', '')}".strip(), "due": f.get("duedate"),
                    "status": _status(((f.get("status") or {}).get("statusCategory") or {}).get("name") or (f.get("status") or {}).get("name", "")),
                    "who": [a.get("emailAddress", ""), a.get("displayName", "")],
                    "priority": 3 if (f.get("priority") or {}).get("name") in ("Highest", "Blocker") else 2 if (f.get("priority") or {}).get("name") == "High" else 0})
    return out


def fetch_asana(c: dict) -> list[dict]:
    with _http(base_url="https://app.asana.com/api/1.0", headers={"Authorization": f"Bearer {c['token']}"}) as h:
        r = h.get(f"/projects/{c['project']}/tasks", params={"opt_fields": "name,assignee.email,assignee.name,due_on,completed", "limit": 100})
        r.raise_for_status()
    return [{"ref": f"asana:{t['gid']}", "title": t.get("name", ""), "due": t.get("due_on"), "status": "done" if t.get("completed") else "todo",
             "who": [(t.get("assignee") or {}).get("email", ""), (t.get("assignee") or {}).get("name", "")], "priority": 0}
            for t in r.json().get("data", [])]


def fetch_trello(c: dict) -> list[dict]:
    with _http(base_url="https://api.trello.com/1") as h:
        auth = {"key": c["key"], "token": c["token"]}
        members = h.get(f"/boards/{c['board']}/members", params={**auth, "fields": "fullName,username"})
        members.raise_for_status()
        lists = h.get(f"/boards/{c['board']}/lists", params={**auth, "fields": "name"})
        lists.raise_for_status()
        cards = h.get(f"/boards/{c['board']}/cards", params={**auth, "fields": "name,due,idMembers,idList,dueComplete,closed"})
        cards.raise_for_status()
    names = {m["id"]: m.get("fullName", "") for m in members.json()}
    lnames = {x["id"]: x.get("name", "") for x in lists.json()}
    return [{"ref": f"trello:{k['id']}", "title": k.get("name", ""), "due": (k.get("due") or "")[:10] or None,
             "status": "done" if k.get("dueComplete") else _status(lnames.get(k.get("idList"), "")),
             "who": [names.get(i, "") for i in k.get("idMembers", [])[:1]], "priority": 0}
            for k in cards.json() if not k.get("closed")]


FETCH = {"jira": fetch_jira, "asana": fetch_asana, "trello": fetch_trello}


def sync(kind: str) -> dict:
    """Bring the tracker's assigned work onto the board. Nothing is sent to anyone because of a sync."""
    if kind not in KINDS:
        raise HTTPException(404, "Unknown tracker")
    c = _cfg(kind)
    if not status()[kind]["connected"]:
        raise HTTPException(400, f"Connect {kind.title()} first.")
    try:
        items = FETCH[kind](c)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        raise HTTPException(400, f"{kind.title()} said no ({code}). Check the token." if code in (401, 403) else f"{kind.title()} error {code}.") from None
    except httpx.HTTPError:
        raise HTTPException(502, f"Couldn't reach {kind.title()}.") from None
    added = updated = skipped = 0
    for it in items:
        m = next((store.find_member(w) for w in it["who"] if w and store.find_member(w)), None)
        old = store.task_by_external(it["ref"])
        if old:
            store.update_task(old["id"], {"title": it["title"], "due": it["due"], "status": it["status"],
                                          **({"assignee_id": m["id"]} if m else {})})
            updated += 1
        elif m and it["status"] != "done":
            store.add_task({"title": it["title"], "due": it["due"], "status": it["status"], "assignee_id": m["id"],
                            "priority": it["priority"], "source": kind, "external_ref": it["ref"], "mine": False})
            added += 1
        else:
            skipped += 1
    store.set_setting(f"tracker_{kind}_synced", store.now())
    return {"added": added, "updated": updated, "skipped": skipped}
