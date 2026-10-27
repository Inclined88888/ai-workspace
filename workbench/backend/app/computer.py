"""Computer Use: ROOKS acting on your own computer through the optional ROOKS Companion.

How it fits together
  * The companion runs on your computer and connects OUT to ROOKS with a device token from pairing. It opens no
    port, so there is no desktop-control API to expose. It asks for one action at a time, runs it, and reports back.
  * Every action passes the rules here before it is queued (enabled, mode, per-app rules, hard blocks, stop), and the
    companion checks its own local rules again before running it (blocked apps, allowed folders, read-only).
  * Actions that act on the world beyond your screen (send, buy, delete, install, publish...) always need your
    approval, whatever the mode. Approval can be one-off or a short, app-scoped grant.
  * Stop cancels everything queued, tells the companion to abort the running action, and refuses new actions until
    you allow them again. "Take control" pauses the same way but resumes with one click.
  * After a restart, an action that was sent but never confirmed is marked uncertain. Repeating it is refused until
    the screen has been observed again, so a recovered task checks before it acts twice.
"""

import asyncio
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from . import audit, db, sensitive

PAIR_MINUTES = 10
ONLINE_SECONDS = 40
DEFAULT_TIMEOUT = 30

READ_KINDS = {"observe", "screenshot", "wait_for", "browser_snapshot", "files_list", "files_read", "capture", "files_history",
              "sheet_read", "doc_read"}
WRITE_KINDS = {"open_app", "focus", "click", "type_text", "key", "scroll", "set_value", "menu", "click_visual", "browser_open",
               "browser_click", "browser_fill", "browser_select", "browser_back", "files_move", "files_mkdir", "files_copy", "files_write",
               "files_undo", "sheet_update", "sheet_dedupe", "doc_write"}
KINDS = READ_KINDS | WRITE_KINDS

# Applications ROOKS never drives, whatever the settings: shells (no back door to arbitrary commands), password and
# key stores, and system security settings.
HARD_BLOCKED_APPS = {"terminal", "iterm", "iterm2", "warp", "alacritty", "kitty", "wezterm", "hyper", "powershell",
                     "command prompt", "cmd", "windows terminal", "xterm", "gnome-terminal", "konsole",
                     "keychain access", "passwords", "1password", "bitwarden", "lastpass", "dashlane", "keepassxc",
                     "system settings", "system preferences", "script editor", "automator", "shortcuts",
                     "activity monitor", "disk utility", "registry editor", "regedit"}
MESSAGING_APPS = {"mail", "messages", "slack", "whatsapp", "telegram", "signal", "microsoft teams", "teams", "discord",
                  "outlook", "messenger", "wechat", "zoom"}
# Labels of things that act beyond the screen: these always need approval
CONSEQUENTIAL_RE = re.compile(
    r"\b(send|submit|post|publish|share|tweet|reply all|buy|purchase|order|checkout|check out|pay|payment|transfer|donate|"
    r"subscribe|delete|remove|erase|empty trash|discard|overwrite|replace all|uninstall|install|format|reset|"
    r"sign out|log out|deactivate|close account|confirm|authorize|approve|grant|don't save|do not save|move to trash|replace)\b", re.I)
CONSEQUENTIAL_KEYS = {"cmd+delete", "cmd+shift+delete", "ctrl+delete", "shift+delete", "cmd+alt+esc", "ctrl+alt+delete"}
KEY_RE = re.compile(r"^((cmd|ctrl|alt|option|shift|fn|super|meta)\+){0,3}([a-z0-9]|f[0-9]{1,2}|enter|return|tab|esc|escape|space|"
                    r"backspace|delete|up|down|left|right|home|end|pageup|pagedown|[\[\]\-=;',./`\\])$")

# action id -> (the event loop of the waiting request, its event). Results arrive on other threads (sync routes), and
# asyncio events are not thread safe, so they are woken through their own loop.
_events: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Event]] = {}
_shots: dict[int, tuple[float, str]] = {}  # device id -> (time, PNG data URL); memory only, never stored or sent to a model
SHOT_TTL = 600
# Full results for active waiters are held briefly in memory, not SQLite.
MAX_TRANSIENT_RESULT = 16 * 1024 * 1024
_transient_results: dict[int, object] = {}
# Pending payloads are intentionally not written to SQLite. An action interrupted
# by a backend restart must be verified/reissued, never replayed from disk.
_pending_args: dict[int, dict] = {}



# Settings
def settings() -> dict:
    try:
        rules = json.loads(db.get_setting("computer_app_rules", "") or "{}")
    except ValueError:
        rules = {}
    return {"enabled": db.get_setting("computer_enabled", "0") == "1",
            "mode": db.get_setting("computer_mode", "ask") or "ask",          # read_only | ask | allowed
            "app_rules": rules if isinstance(rules, dict) else {},              # app -> blocked | read | write
            "stopped": db.get_setting("computer_stop", "0") == "1",
            "paused": db.get_setting("computer_paused", "0") == "1",
            "screenshots": db.get_setting("computer_screenshots", "1") == "1",   # previews for you, never for the model
            "coordinates": db.get_setting("computer_coordinates", "0") == "1"}   # clicks at x,y when no element exists


def update_settings(**changes) -> dict:
    cur = settings()
    if changes.get("mode") is not None and changes["mode"] not in ("read_only", "ask", "allowed"):
        raise HTTPException(400, "mode must be read_only, ask or allowed")
    for k, v in changes.items():
        if v is None:
            continue
        if k == "app_rules":
            clean = {}
            for app, rule in (v or {}).items():
                if rule not in ("blocked", "read", "write"):
                    raise HTTPException(400, f"Rule for {app} must be blocked, read or write")
                if app.strip():
                    clean[app.strip()[:80]] = rule
            db.set_setting("computer_app_rules", json.dumps(clean))
        elif k in ("enabled", "screenshots", "coordinates"):
            db.set_setting(f"computer_{k}", "1" if v else "0")
        elif k == "mode":
            db.set_setting("computer_mode", v)
        if cur.get(k) != v:
            audit.record("user", "computer setting", k, json.dumps(v)[:200])
    return settings()


# Devices and pairing
def _hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def start_pairing() -> dict:
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))  # e.g. 4F2A-91BC-0D3E
    expires = (datetime.now(timezone.utc) + timedelta(minutes=PAIR_MINUTES)).isoformat(timespec="seconds")
    db.run("INSERT INTO computer_pairing(code_hash, expires_at, used) VALUES (?, ?, 0)", _hash(code), expires)
    audit.record("user", "computer pairing started")
    return {"code": code, "expires_at": expires, "minutes": PAIR_MINUTES}


def pair(code: str, name: str, info: dict) -> dict:
    row = db.one("SELECT * FROM computer_pairing WHERE code_hash = ? AND used = 0", _hash(code.strip().upper()))
    if not row or row["expires_at"] < db.now():
        audit.record("companion", "pairing refused", name[:80], "wrong or expired code", ok=False)
        raise HTTPException(403, "That pairing code is wrong or expired. Create a new one in ROOKS, under Computer.")
    if not db.changed("UPDATE computer_pairing SET used = 1 WHERE code_hash = ? AND used = 0", row["code_hash"]):
        raise HTTPException(403, "That pairing code was already used.")
    token = secrets.token_urlsafe(32)
    # Not "online" until the companion itself says hello with what it can do now
    did = db.run("INSERT INTO computer_devices(name, os, token_hash, info, paired_at, last_seen) VALUES (?, ?, ?, ?, ?, NULL)",
                 name.strip()[:80] or "Computer", str(info.get("os", ""))[:40], _hash(token), json.dumps(info)[:8000], db.now())
    audit.record("companion", "computer paired", name[:80], str(info.get("os", "")))
    return {"device_id": did, "token": token}


def device_for(token: str) -> dict:
    d = db.one("SELECT * FROM computer_devices WHERE token_hash = ? AND revoked = 0", _hash(token or ""))
    if not d:
        raise HTTPException(401, "This companion is not paired (or was removed). Pair it again from ROOKS, under Computer.")
    return d


def revoke(did: int) -> None:
    db.run("UPDATE computer_devices SET revoked = 1 WHERE id = ?", did)
    for a in db.all("SELECT id FROM computer_actions WHERE device_id = ? AND status IN ('queued', 'dispatched')", did):
        _finish(a["id"], "cancelled", error="The computer was removed.")
    audit.record("user", "computer removed", f"#{did}")


def devices() -> list[dict]:
    out = []
    for d in db.all("SELECT id, name, os, info, paired_at, last_seen FROM computer_devices WHERE revoked = 0 ORDER BY id"):
        info = json.loads(d.pop("info") or "{}")
        online = bool(d["last_seen"]) and (datetime.now(timezone.utc) - datetime.fromisoformat(d["last_seen"])).total_seconds() < ONLINE_SECONDS
        out.append({**d, "online": online, "permissions": info.get("permissions", {}), "capabilities": info.get("capabilities", []),
                    "version": info.get("version", ""), "folders": info.get("folders", []), "blocked_apps": info.get("blocked_apps", []),
                    "read_only": info.get("read_only", False), "platform_note": info.get("platform_note", "")})
    return out


def online_device() -> dict | None:
    return next((d for d in devices() if d["online"]), None)


def hello(device: dict, info: dict) -> dict:
    pending = info.pop("unacknowledged", []) if isinstance(info, dict) else []
    db.run("UPDATE computer_devices SET info = ?, os = ?, last_seen = ? WHERE id = ?", json.dumps(info)[:8000],
           str(info.get("os", ""))[:40], db.now(), device["id"])
    for r in pending[:50]:  # results the companion finished while ROOKS was down
        if isinstance(r, dict) and isinstance(r.get("id"), int):
            report(device, r)
    return state(device)


def state(device: dict) -> dict:
    s = settings()
    cancel = [r["id"] for r in db.all("SELECT id FROM computer_actions WHERE device_id = ? AND status = 'dispatched' AND cancel = 1", device["id"])]
    return {"stop": s["stopped"] or not s["enabled"], "paused": s["paused"], "cancel": cancel, "screenshots": s["screenshots"]}


# Policy
def _app_of(kind: str, args: dict) -> str:
    return str(args.get("app") or "").strip()


def classify(kind: str, args: dict) -> tuple[str, str]:
    """(risk, reason). risk is read, write or consequential."""
    if kind in READ_KINDS:
        return "read", ""
    label = " ".join(str(args.get(k) or "") for k in ("label", "target_label", "text_hint"))
    if kind == "menu" and isinstance(args.get("path"), list) and args["path"]:
        label = str(args["path"][-1])
    app = _app_of(kind, args).lower()
    if kind in ("click", "browser_click", "menu", "click_visual") and CONSEQUENTIAL_RE.search(label):
        return "consequential", f'"{label.strip()[:60]}" looks like it acts beyond your screen (sends, buys, deletes or similar)'
    if kind == "key":
        combo = str(args.get("keys", "")).lower()
        if combo in CONSEQUENTIAL_KEYS:
            return "consequential", f"{combo} deletes or forces something"
        if combo in ("enter", "return", "cmd+enter", "ctrl+enter") and app in MESSAGING_APPS:
            return "consequential", f"pressing {combo} in {args.get('app')} may send a message"
    if kind == "type_text" and app in MESSAGING_APPS and str(args.get("text", "")).endswith("\n"):
        return "consequential", f"typing a line ending in Enter in {args.get('app')} may send a message"
    if kind in ("files_write", "files_copy", "files_move") and args.get("overwrite"):
        return "consequential", "this would overwrite an existing file"
    return "write", ""


def check(kind: str, args: dict, *, approved: bool = False, unattended: bool = False) -> None:
    """Rules that hold whatever the model asks. Raises with a plain reason when the action may not run."""
    s = settings()
    if not s["enabled"]:
        raise HTTPException(403, "Computer Use is turned off. Turn it on in ROOKS, under Computer.")
    if s["stopped"]:
        raise HTTPException(403, "Computer Use was stopped by you. Nothing runs until you press Allow again, under Computer.")
    if s["paused"]:
        raise HTTPException(409, "You have taken control of the computer. ROOKS waits until you hand it back.")
    if unattended:
        raise HTTPException(403, "Computer Use is not available to unattended runs (schedules, agents, Telegram).")
    if kind not in KINDS:
        raise HTTPException(400, f"Unknown computer action {kind}")
    app = _app_of(kind, args).lower()
    if app and (app in HARD_BLOCKED_APPS or any(app.startswith(b + " ") for b in HARD_BLOCKED_APPS)):
        raise HTTPException(403, f"ROOKS never controls {args.get('app')}: shells, password stores and system security settings are off limits.")
    rule = next((r for a, r in s["app_rules"].items() if a.lower() == app), None) if app else None
    if rule == "blocked":
        raise HTTPException(403, f"You blocked {args.get('app')} for Computer Use.")
    risk, why = classify(kind, args)
    if risk == "read":
        return
    if s["mode"] == "read_only" or rule == "read":
        raise HTTPException(403, f"Computer Use is read only{' for ' + args.get('app') if rule == 'read' else ''}: ROOKS can look but not change anything.")
    if risk == "consequential" and not approved:
        raise HTTPException(403, f"Needs your approval: {why}.")


def approval_reason(kind: str, args: dict) -> str:
    """Why this action must wait for you ('' if it can run). Used by the assistant before queuing."""
    risk, why = classify(kind, args)
    if risk == "read":
        return ""
    if risk == "consequential":
        return f"This acts beyond your screen: {why}. It always needs your approval."
    s = settings()
    app = _app_of(kind, args)
    rule = next((r for a, r in s["app_rules"].items() if a.lower() == app.lower()), None) if app else None
    if s["mode"] == "allowed" or rule == "write" or has_grant(app, kind):
        return ""
    return f"Computer Use is set to ask before changes{' in ' + app if app else ''}."


# Temporary grants: "let ROOKS click and type in Numbers for 30 minutes"
def grant(app: str, minutes: int) -> dict:
    minutes = max(1, min(int(minutes), 8 * 60))
    expires = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    gid = db.run("INSERT INTO computer_grants(app, expires_at, created_at) VALUES (?, ?, ?)", app.strip()[:80], expires, db.now())
    audit.record("user", "computer grant", app[:80], f"{minutes} minutes")
    return {"id": gid, "app": app, "expires_at": expires}


def has_grant(app: str, kind: str) -> bool:
    if not app:
        return False
    return bool(db.one("SELECT 1 FROM computer_grants WHERE lower(app) = lower(?) AND expires_at > ? AND revoked = 0", app, db.now()))


def grants() -> list[dict]:
    return db.all("SELECT id, app, expires_at FROM computer_grants WHERE revoked = 0 AND expires_at > ? ORDER BY expires_at", db.now())


def revoke_grant(gid: int) -> None:
    db.run("UPDATE computer_grants SET revoked = 1 WHERE id = ?", gid)


# Stop and take control
def stop() -> dict:
    db.set_setting("computer_stop", "1")
    n = _cancel_all("Stopped by you.")
    _pause_missions("Computer Use was stopped by you.")
    audit.record("user", "computer stop", "", f"{n} action(s) cancelled")
    return {"stopped": True, "cancelled": n}


def allow_again() -> dict:
    db.set_setting("computer_stop", "0")
    audit.record("user", "computer allowed again")
    return settings()


def take_control() -> dict:
    db.set_setting("computer_paused", "1")
    n = _cancel_all("You took control.")
    _pause_missions("You took control of the computer.")
    audit.record("user", "computer take control")
    return {"paused": True, "cancelled": n}


def hand_back() -> dict:
    db.set_setting("computer_paused", "0")
    audit.record("user", "computer handed back")
    return settings()


def _cancel_all(why: str) -> int:
    n = 0
    for a in db.all("SELECT id, status FROM computer_actions WHERE status IN ('queued', 'dispatched')"):
        if a["status"] == "queued":
            _finish(a["id"], "cancelled", error=why)
        else:
            db.run("UPDATE computer_actions SET cancel = 1 WHERE id = ?", a["id"])  # the companion aborts it mid-way
        n += 1
    from .assistant import request_stop

    for c in db.all("SELECT DISTINCT conversation_id FROM computer_actions WHERE conversation_id IS NOT NULL AND created_at > ?",
                    (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")):
        request_stop(c["conversation_id"])
    return n


def _pause_missions(why: str) -> None:
    from . import missions

    for m in db.all("SELECT DISTINCT mission_id FROM computer_actions WHERE mission_id IS NOT NULL"):
        mm = db.one("SELECT status FROM missions WHERE id = ?", m["mission_id"])
        if mm and mm["status"] in ("running", "queued"):
            try:
                missions.pause(m["mission_id"])
                missions.event(m["mission_id"], "pause", why)
            except HTTPException:
                pass


# Queue
def _canon(kind: str, args: dict) -> str:
    # Idempotency markers must not reveal text or document contents.
    body = kind + json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


async def request(kind: str, args: dict, *, conversation_id: int | None = None, mission_id: int | None = None,
                  approved: bool = False, unattended: bool = False, timeout: float | None = None) -> dict:
    """Queue one action for the paired computer and wait for its result."""
    check(kind, args, approved=approved, unattended=unattended)
    dev = online_device()
    if not dev:
        raise HTTPException(503, "No computer is connected. Start ROOKS Companion on your computer (see ROOKS, under Computer).")
    risk, _ = classify(kind, args)
    key = _canon(kind, args)
    if risk != "read":
        # After a restart, an identical action whose outcome was unknown (or only confirmed late) is not repeated until
        # the screen has been looked at again: recovery checks the observed state instead of acting twice.
        last_look = db.one("SELECT COALESCE(MAX(id), 0) AS id FROM computer_actions WHERE device_id = ? AND status = 'done' "
                           "AND kind IN ('observe', 'browser_snapshot', 'files_list', 'screenshot')", dev["id"])["id"]
        # Device wide, not per conversation: retrying the same thing from a new chat is covered too
        prior = db.one("SELECT id, status FROM computer_actions WHERE device_id = ? AND idem_key = ? AND id > ? AND "
                       "(status = 'uncertain' OR (status = 'done' AND recovered = 1)) ORDER BY id DESC LIMIT 1", dev["id"], key, last_look)
        if prior:
            what = "its outcome is unknown" if prior["status"] == "uncertain" else "it did finish, so repeating it would do it twice"
            raise HTTPException(409, f"This exact action (#{prior['id']}) was sent before ROOKS restarted and {what}. "
                                     "Observe the screen first to check the current state, then decide.")
    # Only non-content metadata goes on disk. In particular no typed strings,
    # document contents, or form values are persisted as action arguments.
    # Labels, URLs and paths can contain private document names or credentials.
    # Keep only minimal operational metadata; history remains useful by action type.
    safe_args = {k: args[k] for k in ("app", "direction", "keys")
                 if k in args and isinstance(args[k], (str, int, float, bool))}
    if "text" in args:
        safe_args["text_length"] = len(str(args["text"]))
    aid = db.run("INSERT INTO computer_actions(device_id, kind, args, risk, status, approved, conversation_id, mission_id, idem_key, timeout, created_at) "
                 "VALUES (?, ?, ?, ?, 'preparing', ?, ?, ?, ?, ?, ?)", dev["id"], kind, json.dumps(safe_args), risk, int(approved), conversation_id, mission_id, key,
                 int(timeout or args.get("timeout") or DEFAULT_TIMEOUT), db.now())
    _pending_args[aid] = args
    ev = asyncio.Event()
    _events[aid] = (asyncio.get_running_loop(), ev)
    # Publish the action only after its ephemeral payload and waiter exist.
    db.run("UPDATE computer_actions SET status = 'queued' WHERE id = ? AND status = 'preparing'", aid)
    if risk != "read":
        audit.record("assistant", "computer", kind, _summary(kind, args))
    limit = (timeout or args.get("timeout") or DEFAULT_TIMEOUT) + 15
    try:
        await asyncio.wait_for(ev.wait(), limit)
    except asyncio.TimeoutError:
        a = db.one("SELECT status FROM computer_actions WHERE id = ?", aid)
        if a["status"] == "queued":
            _finish(aid, "cancelled", error="The computer did not pick this up in time.")
        elif a["status"] == "dispatched":
            db.run("UPDATE computer_actions SET status = 'uncertain', error = 'No answer from the computer in time' WHERE id = ?", aid)
    finally:
        _events.pop(aid, None)
        _pending_args.pop(aid, None)
    a = db.one("SELECT * FROM computer_actions WHERE id = ?", aid)
    out = {"id": aid, "status": a["status"], "ok": a["status"] == "done",
           "result": _transient_results.pop(aid, None), "error": a["error"]}
    if out["ok"] and out["result"] is None:
        out["ok"] = False
        out["error"] = "Result unavailable after restart; re-observe before retrying."
    if a["status"] == "uncertain":
        out["error"] = "No confirmation from the computer. Observe the screen before trying again."
    return out


def _summary(kind: str, args: dict) -> str:
    """For the activity log: what was done, never what was typed."""
    parts = [f"{k}={args[k]}" for k in ("app", "target", "label", "keys", "direction", "url", "src", "dst", "path") if args.get(k)]
    if kind in ("type_text", "browser_fill"):
        parts.append(f"text of {len(str(args.get('text', '')))} characters")
    return sensitive.redact(", ".join(parts))[:300]


def next_action(device: dict) -> dict | None:
    db.run("UPDATE computer_devices SET last_seen = ? WHERE id = ?", db.now(), device["id"])
    s = settings()
    if s["stopped"] or s["paused"] or not s["enabled"]:
        return None
    # Fail closed after a backend restart: queued actions no longer have
    # their ephemeral payload, so they cannot be dispatched or replayed.
    for stale in db.all("SELECT id FROM computer_actions WHERE device_id = ? AND status = 'queued' ORDER BY id", device["id"]):
        if stale["id"] not in _pending_args:
            _finish(stale["id"], "failed", error="Action interrupted by restart; review before retrying.")
    a = db.one("SELECT * FROM computer_actions WHERE device_id = ? AND status = 'queued' ORDER BY id LIMIT 1", device["id"])
    if not a:
        return None
    payload = _pending_args.get(a["id"])
    if payload is None:
        _finish(a["id"], "failed", error="Action payload expired; review before retrying.")
        return None
    if not db.changed("UPDATE computer_actions SET status = 'dispatched', dispatched_at = ? WHERE id = ? AND status = 'queued'", db.now(), a["id"]):
        return None
    return {"id": a["id"], "kind": a["kind"], "args": payload, "timeout": a["timeout"], "risk": a["risk"], "approved": bool(a["approved"])}


def report(device: dict, r: dict) -> None:
    a = db.one("SELECT * FROM computer_actions WHERE id = ? AND device_id = ?", r["id"], device["id"])
    if not a or a["status"] in ("done", "failed", "cancelled", "refused"):
        return
    status = "done" if r.get("ok") else ("cancelled" if r.get("cancelled") else "refused" if r.get("refused") else "failed")
    result = r.get("result")
    shot = r.get("screenshot")
    if isinstance(shot, str) and shot.startswith("data:image/png;base64,") and len(shot) < 3_000_000 and settings()["screenshots"]:
        _shots[device["id"]] = (time.time(), shot)
    if a["status"] == "uncertain":
        db.run("UPDATE computer_actions SET recovered = 1 WHERE id = ?", a["id"])  # the answer arrived after a restart
    _finish(a["id"], status, result=result, error=sensitive.redact(str(r.get("error") or ""))[:300])


def _finish(aid: int, status: str, result=None, error: str = "") -> None:
    # Never persist screenshots or large action outputs. Live callers receive
    # an intact bounded result; an interrupted waiter must re-observe.
    waiting = _events.get(aid)
    if result is not None and waiting:
        try:
            encoded_size = len(json.dumps(result))
        except (TypeError, ValueError):
            encoded_size = MAX_TRANSIENT_RESULT + 1
        if encoded_size > MAX_TRANSIENT_RESULT:
            status, error = "failed", "Computer result exceeds the 16 MiB safety limit."
        else:
            _transient_results[aid] = result
    db.run("UPDATE computer_actions SET status = ?, result = '', error = ?, finished_at = ? WHERE id = ?",
           status, error, db.now(), aid)
    waiting = _events.get(aid)
    if waiting:
        loop, ev = waiting
        try:
            loop.call_soon_threadsafe(ev.set)
        except RuntimeError:  # that loop has closed: nobody is waiting any more
            pass


def screenshot(did: int) -> str:
    t, shot = _shots.get(did, (0, ""))
    if time.time() - t > SHOT_TTL:
        _shots.pop(did, None)
        return ""
    return shot


def history(limit: int = 100) -> list[dict]:
    rows = db.all("SELECT id, kind, args, risk, status, error, conversation_id, mission_id, created_at, finished_at FROM computer_actions "
                  "ORDER BY id DESC LIMIT ?", limit)
    for r in rows:
        args = json.loads(r.pop("args") or "{}")
        r["summary"] = _summary(r["kind"], args)
    return rows


def recover() -> None:
    """After a restart: queued actions are cancelled (whoever asked is gone); sent but unconfirmed ones are uncertain."""
    db.run("UPDATE computer_actions SET status = 'cancelled', error = 'ROOKS restarted before this was sent' WHERE status IN ('queued', 'preparing')")
    db.run("UPDATE computer_actions SET status = 'uncertain', error = 'ROOKS restarted while this was running' WHERE status = 'dispatched'")


def describe_observation(result: dict, limit: int = 120) -> str:
    """Text for the model: apps, windows and elements, with secrets removed. Screen text is untrusted data."""
    if not isinstance(result, dict):
        return json.dumps(result)[:4000]
    lines = []
    if result.get("active_app"):
        lines.append(f"Active app: {result['active_app']}")
    for w in (result.get("windows") or [])[:20]:
        lines.append(f"Window: {w.get('app', '')} | {sensitive.redact(str(w.get('title', '')))[:120]}" + (" (focused)" if w.get("focused") else ""))
    els = result.get("elements") or []
    for e in els[:limit]:
        value = "" if e.get("secure") else sensitive.redact(str(e.get("value") or ""))[:80]
        label = sensitive.redact(str(e.get("label") or ""))[:80]
        lines.append(f"[{e.get('id')}] {e.get('role', '')} \"{label}\"" + (f" = \"{value}\"" if value else "") + (" (password field: never type here)" if e.get("secure") else ""))
    if len(els) > limit:
        lines.append(f"... {len(els) - limit} more elements")
    if result.get("text"):
        lines.append("Visible text: " + sensitive.redact(str(result["text"]))[:3000])
    if result.get("columns"):
        lines.append("Columns: " + ", ".join(map(str, result["columns"])) + f" ({result.get('row_count', 0)} rows)")
        for i, r in enumerate((result.get("rows") or [])[:60], 2):
            lines.append(f"row {i}: " + " | ".join(sensitive.redact(str(v))[:40] for v in r))
        if result.get("duplicate_rows"):
            lines.append("Duplicate rows: " + ", ".join(f"{d['row']} (same as {d['same_as_row']})" for d in result["duplicate_rows"][:50]))
    for k in ("entries", "content", "url", "title", "note"):
        if result.get(k):
            lines.append(f"{k}: {sensitive.redact(json.dumps(result[k]) if not isinstance(result[k], str) else result[k])[:3000]}")
    return "\n".join(lines)[:12000] or json.dumps(result)[:4000]
