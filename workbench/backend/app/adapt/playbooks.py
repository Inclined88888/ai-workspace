"""Teach once: a recording plus the person's narration becomes a reusable playbook.

A playbook is plain English steps a person can read and edit, plus parameters (the things that change each time,
like a client name or a date) and notes learned from past runs. Compile is deterministic, so it works without a
model; when a model is available it tidies the wording, names the job and spots which typed values are parameters.

Replay hands the steps to the agent for that surface (Rooks Browser for websites, Rooks Desk for desktop apps) as a
goal. Both agents act on an accessibility snapshot (role + name), so they find "the Invoices link" after a redesign
instead of clicking old coordinates, and both pause for the person on sign-in, payment, sending or submitting.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from .. import config, db
from . import jobs, knowledge, recorder, store

SENSITIVE = re.compile(r"\b(send|submit|pay|purchase|buy|delete|remove|approve|sign|post|publish|transfer|book)\b", re.I)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:32] or "value"


def _what(t: dict) -> str:
    name = t.get("label") or t.get("name") or t.get("placeholder") or t.get("text") or ""
    role = t.get("role") or t.get("tag") or ""
    noun = {"link": "link", "button": "button", "textbox": "box", "searchbox": "search box", "combobox": "list",
            "checkbox": "tick box", "radio": "option", "tab": "tab", "menuitem": "menu item"}.get(role, role or "item")
    return f"the \u201c{name}\u201d {noun}" if name else f"the {noun}"


def compile_steps(events: list[dict], narration: str = "") -> dict:
    """Deterministic compile: events -> steps and parameters."""
    events = recorder.tidy([e for e in events if e])
    steps, params, start_url, app = [], [], "", ""
    names: set[str] = set()

    def param(label: str, example: str) -> str:
        base = _slug(label)
        name, i = base, 2
        while name in names:
            name, i = f"{base}_{i}", i + 1
        names.add(name)
        params.append({"name": name, "label": label or name.replace("_", " "), "example": example})
        return name

    prev = ""
    for e in events:
        k, t = e["kind"], e.get("target") or {}
        if k == "navigate" and prev in ("click", "submit", "key"):
            prev = k
            continue  # the page a click or submit led to; replaying the click gets there again
        prev = k
        step = {"action": k, "target": {x: t[x] for x in ("role", "name", "label", "placeholder", "text") if t.get(x)}}
        if k == "navigate":
            url = e.get("url", "")
            if not start_url and not steps:
                start_url = url
                continue
            host = urlsplit(url).netloc
            step["text"] = f"Go to {e.get('title') or host or url}" + (f" ({url})" if url else "")
            step["url"] = url
        elif k == "open_app":
            app = app or e.get("app", "")
            step["text"] = f"Open {e.get('app') or 'the app'}"
        elif k == "click":
            step["text"] = f"Click {_what(t)}"
        elif k == "check":
            step["text"] = f"Tick {_what(t)}"
        elif k in ("type", "select"):
            label = t.get("label") or t.get("name") or t.get("placeholder") or "value"
            if e.get("secret"):
                step["text"] = f"Sign in or fill {_what(t)} yourself (I never type passwords)"
                step["needs_user"] = True
            else:
                p = param(label, e.get("value", ""))
                step["param"] = p
                step["text"] = (f"In {_what(t)}, type {{{p}}}" if k == "type" else f"Choose {{{p}}} in {_what(t)}")
        elif k == "submit":
            step["text"] = "Submit the form (I'll ask you first)"
            step["sensitive"] = True
        elif k == "key":
            step["text"] = f"Press {e.get('key') or 'Enter'}"
        elif k == "note":
            step["text"] = e.get("text") or e.get("value") or ""
        if step.get("text"):
            if SENSITIVE.search(step["text"]) and k in ("click", "submit"):
                step["sensitive"] = True
            steps.append(step)
    if not events and narration.strip():  # narration only: one step per line or sentence
        parts = [p.strip(" -•\t") for p in re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z])", narration) if p.strip(" -•\t")]
        steps = [{"action": "note", "text": p, **({"sensitive": True} if SENSITIVE.search(p) else {})} for p in parts[:40]]
    for i, s in enumerate(steps, 1):
        s["n"] = i
    surface = "desktop" if any(e.get("surface") == "desktop" for e in events) else ("browser" if events else "narration")
    text = narration + " " + " ".join(s["text"] for s in steps) + " " + start_url
    kind = jobs.classify(text)
    first = narration.strip().split("\n")[0][:60] if narration.strip() else ""
    name = first or (f"{jobs.label(kind).capitalize()} on {urlsplit(start_url).netloc}" if start_url else jobs.label(kind).capitalize())
    return {"name": name.rstrip("."), "summary": narration.strip()[:400] or f"Repeat what you showed me ({len(steps)} steps).",
            "job_kind": kind, "surface": surface, "start_url": start_url, "app": app, "params": params, "steps": steps,
            "notes": []}


REFINE_PROMPT = (
    "You turn a person's one-time demonstration into a reusable playbook for their assistant. You get the steps the "
    "recorder captured and what the person said while doing it. Rewrite the steps in short, plain English a "
    "non-technical person understands, keep their order, keep {param} placeholders, merge noise, and decide which typed "
    "values change each time (parameters) versus stay the same (fixed text: put the value in the step instead). "
    'Reply with only JSON: {"name": "short title", "summary": "one sentence: what this does and when", '
    '"job_kind": "weekly_report|invoice_chasing|quotes|site_checks|timesheets|inbox_triage|scheduling|bookkeeping|'
    'legal_review|research|data_analysis|study|maths|coding|creative|social_posts|other", '
    '"steps": ["..."], "fixed_params": ["names of parameters that never change"]}'
)


async def refine(pb: dict, narration: str, model: str | None = None) -> dict:
    """Ask the model to polish the compiled playbook; any problem keeps the deterministic version."""
    try:
        payload = json.dumps({"captured_steps": [s["text"] for s in pb["steps"]], "parameters": pb["params"],
                              "narration": narration[:4000], "start": pb["start_url"]}, ensure_ascii=False)
        out = await knowledge.model_json(REFINE_PROMPT, payload, model, "teach")
    except Exception:  # noqa: BLE001 - no model, offline, bad JSON: the compiled playbook is already usable
        return pb
    if not isinstance(out, dict):
        return pb
    texts = out.get("steps")
    if isinstance(texts, list) and len(texts) == len(pb["steps"]) and all(isinstance(t, str) and t.strip() for t in texts):
        for s, t in zip(pb["steps"], texts):
            s["text"] = t.strip()[:300]
    fixed = set(out.get("fixed_params") or []) if isinstance(out.get("fixed_params"), list) else set()
    for p in [p for p in pb["params"] if p["name"] in fixed]:
        for s in pb["steps"]:
            if s.get("param") == p["name"]:
                s["text"] = s["text"].replace("{" + p["name"] + "}", f"\u201c{p['example']}\u201d")
                s.pop("param", None)
        pb["params"].remove(p)
    if isinstance(out.get("name"), str) and out["name"].strip():
        pb["name"] = out["name"].strip()[:80]
    if isinstance(out.get("summary"), str) and out["summary"].strip():
        pb["summary"] = out["summary"].strip()[:400]
    if out.get("job_kind") in jobs.KINDS:
        pb["job_kind"] = out["job_kind"]
    return pb


# Storage -------------------------------------------------------------------------------------------------------------
def _out(r: dict) -> dict:
    body = store.open_json(r["body_enc"], {}) or {}
    return {"id": r["id"], "name": r["name"], "status": r["status"], "version": r["version"], "runs": r["runs"],
            "successes": r["successes"], "schedule_id": r["schedule_id"], "recording_id": r["recording_id"],
            "created_at": r["created_at"], "updated_at": r["updated_at"], **body}


def get(pid: int) -> dict:
    store.ensure()
    r = db.one("SELECT * FROM adapt_playbooks WHERE id = ?", pid)
    if not r:
        raise HTTPException(404, "Not found")
    return _out(r)


def all_playbooks() -> list[dict]:
    store.ensure()
    return [_out(r) for r in db.all("SELECT * FROM adapt_playbooks ORDER BY updated_at DESC")]


BODY_KEYS = ("summary", "job_kind", "surface", "start_url", "app", "params", "steps", "notes")


def save(pb: dict, recording_id: int | None = None, pid: int | None = None, status: str | None = None) -> dict:
    store.ensure()
    body = {k: pb.get(k) for k in BODY_KEYS}
    body["steps"] = [s for s in (body["steps"] or []) if str(s.get("text", "")).strip()]
    for i, s in enumerate(body["steps"], 1):
        s["n"] = i
    body["params"] = body["params"] or []
    body["notes"] = body["notes"] or []
    name = (pb.get("name") or "My task").strip()[:80]
    if pid:
        db.run("UPDATE adapt_playbooks SET name = ?, body_enc = ?, status = COALESCE(?, status), version = version + 1, "
               "updated_at = ? WHERE id = ?", name, store.seal(body), status, db.now(), pid)
        return get(pid)
    pid = db.run("INSERT INTO adapt_playbooks(name, body_enc, status, recording_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                 name, store.seal(body), status or "draft", recording_id, db.now(), db.now())
    return get(pid)


def delete(pid: int) -> dict:
    store.ensure()
    db.run("DELETE FROM adapt_playbooks WHERE id = ?", pid)
    db.run("DELETE FROM adapt_runs WHERE playbook_id = ?", pid)
    return {"deleted": pid}


def add_note(pid: int, note: str) -> dict:
    pb = get(pid)
    notes = [n for n in pb.get("notes", []) if n.lower() != note.lower()]
    notes.append(note[:300])
    pb["notes"] = notes[-12:]
    return save(pb, pid=pid)


# Replay ----------------------------------------------------------------------------------------------------------------
def render_goal(pb: dict, values: dict) -> str:
    missing = [p["label"] for p in pb.get("params", []) if not str(values.get(p["name"], p.get("example", ""))).strip()]
    if missing:
        raise HTTPException(400, "Tell me: " + ", ".join(missing))
    vals = {p["name"]: str(values.get(p["name"], p.get("example", ""))) for p in pb.get("params", [])}
    lines = []
    for s in pb.get("steps", []):
        text = s["text"]
        for k, v in vals.items():
            text = text.replace("{" + k + "}", f"\u201c{v}\u201d")
        t = s.get("target") or {}
        hint = ", ".join(f"{k} {v!r}" for k, v in t.items())
        lines.append(f"{s['n']}. {text}" + (f"   [what it looked like: {hint}]" if hint else ""))
    where = pb.get("start_url") or pb.get("app") or ""
    out = [f"Repeat a task the user showed you once: {pb.get('name')}. {pb.get('summary') or ''}".strip()]
    if where:
        out.append(f"Start at: {where}")
    if vals:
        out.append("Values for this run: " + "; ".join(f"{p['label']} = \u201c{vals[p['name']]}\u201d" for p in pb["params"]))
    out.append("Steps:\n" + "\n".join(lines))
    if pb.get("notes"):
        out.append("Lessons from earlier runs (follow them):\n" + "\n".join(f"- {n}" for n in pb["notes"]))
    out.append("How to do it: find each button, link and field by its label, role or visible text, never by position. "
               "If the screen looks different from when you were shown, look for the item with the same meaning (a "
               "renamed button, a moved menu) and carry on. Only if a step is truly impossible or needs the user "
               "(signing in, a missing value, a choice you cannot make) use handoff and say exactly what you need. "
               "Anything that sends, submits, pays or deletes waits for the user's OK. Finish with a short summary of "
               "what you did and what you found.")
    return "\n\n".join(out)


class BrowserRunner:
    surface = "browser"

    async def start(self, goal: str, pb: dict) -> str:
        if not config.BROWSER_URL:
            raise HTTPException(503, "Turn on Rooks Browser to replay website tasks (Settings \u2192 Browser agent).")
        job = {"goal": goal, "start_url": pb.get("start_url") or None, "headed": True,
               "max_steps": min(10 + 3 * len(pb.get("steps", [])), 50), "provider": "workbench", "model": "default"}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{config.BROWSER_URL}/api/jobs", json=job, headers={"Authorization": f"Bearer {config.BROWSER_API_KEY}"})
        if r.status_code >= 400:
            raise HTTPException(502, f"Rooks Browser: {r.text[:300]}")
        return r.json()["id"]

    async def status(self, remote_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{config.BROWSER_URL}/api/jobs/{remote_id}", headers={"Authorization": f"Bearer {config.BROWSER_API_KEY}"})
        return r.json() if r.status_code < 400 else {"status": "error", "error": r.text[:300]}


class DeskRunner(BrowserRunner):
    """Rooks Desk speaks the same job API. Without computer use set up, desktop replays report 503."""
    surface = "desktop"

    def _base(self) -> tuple[str, str]:
        base = config.DESK_URL
        if not base:
            raise HTTPException(503, "Desktop replays need computer use (Rooks Desk), which is not set up on this computer yet.")
        return base, config.DESK_API_KEY

    async def start(self, goal: str, pb: dict) -> str:
        base, key = self._base()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{base}/api/jobs", json={"goal": goal, "max_steps": min(10 + 3 * len(pb.get("steps", [])), 50), "source": "learn"},
                             headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 400:
            raise HTTPException(502, f"Rooks Desk: {r.text[:300]}")
        return r.json()["id"]

    async def status(self, remote_id: str) -> dict:
        base, key = self._base()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{base}/api/jobs/{remote_id}", headers={"Authorization": f"Bearer {key}"})
        return r.json() if r.status_code < 400 else {"status": "error", "error": r.text[:300]}


RUNNERS = {"browser": BrowserRunner, "desktop": DeskRunner}


def _run_out(r: dict) -> dict:
    return {"id": r["id"], "playbook_id": r["playbook_id"], "surface": r["surface"], "remote_id": r["remote_id"],
            "status": r["status"], "values": store.open_json(r["values_enc"], {}), "goal": store.open_text(r["goal_enc"]),
            "result": store.open_text(r["result_enc"]), "trigger": r["trigger"], "rating": r["rating"],
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


def last_values(pid: int) -> dict:
    r = db.one("SELECT values_enc FROM adapt_runs WHERE playbook_id = ? ORDER BY id DESC LIMIT 1", pid)
    return store.open_json(r["values_enc"], {}) if r else {}


async def run(pid: int, values: dict | None = None, trigger: str = "user") -> dict:
    pb = get(pid)
    values = {**last_values(pid), **{k: v for k, v in (values or {}).items()}}  # unchanged values carry over
    goal = render_goal(pb, values)
    surface = pb.get("surface") if pb.get("surface") in RUNNERS else "browser"
    remote = await RUNNERS[surface]().start(goal, pb)
    rid = db.run("INSERT INTO adapt_runs(playbook_id, surface, remote_id, status, values_enc, goal_enc, trigger, created_at, updated_at) "
                 "VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?)", pid, surface, remote, store.seal(values), store.seal(goal), trigger,
                 db.now(), db.now())
    db.run("UPDATE adapt_playbooks SET runs = runs + 1, status = 'ready', updated_at = ? WHERE id = ?", db.now(), pid)
    jobs.observe(pb.get("job_kind"), "playbook", f"Ran {pb['name']}", f"playbook:{pid}")
    return run_get(rid)


def run_get(rid: int) -> dict:
    store.ensure()
    r = db.one("SELECT * FROM adapt_runs WHERE id = ?", rid)
    if not r:
        raise HTTPException(404, "Not found")
    return _run_out(r)


def runs_for(pid: int) -> list[dict]:
    store.ensure()
    return [_run_out(r) for r in db.all("SELECT * FROM adapt_runs WHERE playbook_id = ? ORDER BY id DESC LIMIT 20", pid)]


async def sync(rid: int) -> dict:
    """Bring a run's status up to date from its agent; on finish, remember how it went."""
    run_row = run_get(rid)
    if run_row["status"] in ("done", "failed"):
        return run_row
    j = await RUNNERS.get(run_row["surface"], BrowserRunner)().status(run_row["remote_id"])
    st = j.get("status")
    pending = j.get("pending_approvals") or []
    if st in ("queued", "running"):
        status = "needs_you" if pending else "running"
        result = (pending[0].get("description") or "Waiting for you") if pending else ""
    elif st == "done":
        res = j.get("result") or {}
        ok = res.get("success") is not False
        status, result = ("done" if ok else "failed"), str(res.get("final") or "")[:4000]
        if ok:
            db.run("UPDATE adapt_playbooks SET successes = successes + 1 WHERE id = ?", run_row["playbook_id"])
        else:
            add_note(run_row["playbook_id"], f"Last time this got stuck: {result[:200]}" if result else "Last time this got stuck.")
    else:
        status, result = "failed", str(j.get("error") or st or "")[:2000]
    db.run("UPDATE adapt_runs SET status = ?, result_enc = ?, updated_at = ? WHERE id = ?", status, store.seal(result), db.now(), rid)
    return run_get(rid)
