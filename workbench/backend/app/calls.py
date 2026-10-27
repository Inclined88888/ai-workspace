"""ROOKS reaching out to talk: in-app call invitations, and optional phone calls to your own verified number.

In-app invitations (always available, no account needed): a check-in set to "call" invites you to a voice
conversation in ROOKS. You accept (the voice session opens with the briefing and its project), decline, or
snooze (up to twice). An invitation you do not answer in time becomes one written briefing in the inbox; ROOKS
does not keep ringing. Invitations only happen inside your contact window and up to a daily limit.

Phone calls (optional, off by default, need a provider):
  * Only to your own number, verified with a code ROOKS reads out in a verification call you start yourself.
  * Turning calls on, or changing the number, needs your password.
  * Every call starts by saying it is ROOKS, an AI assistant. It reads the briefing and ends; you reply in the
    app. A two-way phone conversation would need the provider to reach ROOKS from the internet, which ROOKS does
    not allow, so it is not built.
  * Nothing is recorded or transcribed. Calls only inside the contact window, at most N a day and M a week, under a
    monthly cost cap; no retries for an unanswered call (it becomes a written briefing instead).
  * Providers: "test" (records what would be said, places nothing, for trying it out and for automated tests) and
    "twilio" (real calls; you pay Twilio). The number is stored encrypted and shown masked.
"""

import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import httpx
from fastapi import HTTPException

from . import audit, db, security

AI_INTRO = "Hello, this is ROOKS, your AI assistant, calling with an update you asked for."
DEFAULTS = {"window_start": "09:00", "window_end": "20:00", "weekend_start": "09:00", "weekend_end": "17:00",
            "invites_per_day": 3, "invite_minutes": 30, "phone_per_day": 1, "phone_per_week": 3, "phone_month_cents": 100}
EST_CENTS_PER_CALL = 8.0  # a short call to an Australian mobile at Twilio's listed US$0.075 a minute, rounded up
MOCK_CALLS: list[dict] = []  # what the test provider "said"; never a real call


# Settings
def policy() -> dict:
    try:
        saved = json.loads(db.get_setting("contact_policy", "") or "{}")
    except ValueError:
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def set_policy(changes: dict) -> dict:
    from .checkins import _hhmm

    cur = policy()
    for k, v in changes.items():
        if k not in DEFAULTS or v is None:
            continue
        if k.endswith(("_start", "_end")):
            _hhmm(v)
        else:
            v = max(0, min(int(v), 10_000))
        cur[k] = v
    db.set_setting("contact_policy", json.dumps(cur))
    return cur


def in_window(now: datetime | None = None) -> bool:
    from .checkins import _hhmm, _local_now

    p = policy()
    now = now or _local_now()
    weekend = now.weekday() >= 5
    start, end = _hhmm(p["weekend_start" if weekend else "window_start"]), _hhmm(p["weekend_end" if weekend else "window_end"])
    return bool(start and end and start <= now.time() < end)


def phone() -> dict:
    return {"provider": db.get_setting("phone_provider", "") or "", "enabled": db.get_setting("phone_enabled", "0") == "1",
            "number": security.decrypt(db.get_setting("phone_number_enc", "") or ""),
            "verified": db.get_setting("phone_verified", "0") == "1"}


def mask(number: str) -> str:
    return (number[:3] + "•" * max(0, len(number) - 6) + number[-3:]) if number else ""


def status() -> dict:
    p = phone()
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    return {"policy": policy(), "in_window": in_window(),
            "phone": {"provider": p["provider"], "enabled": p["enabled"], "number": mask(p["number"]), "verified": p["verified"],
                      "twilio_configured": bool(db.get_setting("twilio_sid", "")), "month_cost_estimate_cents": _phone_cost(30)},
            "invites": db.all("SELECT * FROM call_invites ORDER BY id DESC LIMIT 30"),
            "phone_calls": db.all("SELECT id, purpose, provider, to_masked, status, cost_estimate_cents, error, created_at FROM phone_calls "
                                  "WHERE created_at >= ? ORDER BY id DESC LIMIT 30", since)}


def _require_password(password: str) -> None:
    if not security.verify_password(password or "", db.get_setting("password_hash", "") or ""):
        audit.record("user", "phone settings refused", "", "wrong password")
        raise HTTPException(403, "Enter your ROOKS password to change phone calling.")


def configure_phone(password: str, provider: str | None = None, number: str | None = None, enabled: bool | None = None,
                    twilio_sid: str | None = None, twilio_token: str | None = None, twilio_from: str | None = None) -> dict:
    """Strong authentication: every change here needs the password. A new number is unverified until confirmed."""
    _require_password(password)
    if provider is not None:
        if provider not in ("", "test", "twilio"):
            raise HTTPException(400, "Provider is test or twilio.")
        db.set_setting("phone_provider", provider)
    if twilio_sid is not None:
        if twilio_sid and not re.fullmatch(r"AC[0-9a-f]{32}", twilio_sid):
            raise HTTPException(400, "A Twilio Account SID starts with AC and has 34 characters.")
        db.set_setting("twilio_sid", twilio_sid)
    if twilio_token:
        db.set_setting("twilio_token_enc", security.encrypt(twilio_token))
    if twilio_from is not None:
        db.set_setting("twilio_from", _e164(twilio_from) if twilio_from else "")
    if number is not None:
        num = _e164(number) if number else ""
        db.set_setting("phone_number_enc", security.encrypt(num) if num else "")
        db.set_setting("phone_verified", "0")
        db.set_setting("phone_code", "")
        db.set_setting("phone_enabled", "0")
    if enabled is not None:
        p = phone()
        if enabled and not (p["provider"] and p["number"] and p["verified"]):
            raise HTTPException(409, "Choose a provider and verify your number before turning phone calls on.")
        db.set_setting("phone_enabled", "1" if enabled else "0")
    audit.record("user", "phone settings changed", "", f"provider {phone()['provider'] or 'none'}, enabled {phone()['enabled']}")
    return status()


def _e164(number: str) -> str:
    n = re.sub(r"[\s()\-]", "", number)
    if n.startswith("0") and len(n) == 10:
        n = "+61" + n[1:]  # an Australian number written locally
    if not re.fullmatch(r"\+[1-9]\d{7,14}", n):
        raise HTTPException(400, "Enter the number in international form, e.g. +61 412 345 678.")
    return n


# Providers
def _twiml(text: str) -> str:
    return f"<Response><Say>{escape(AI_INTRO)}</Say><Pause length=\"1\"/><Say>{escape(text[:1500])}</Say>" \
           "<Say>Reply in the ROOKS app when you are ready. Goodbye.</Say><Hangup/></Response>"


async def _place(to: str, text: str) -> tuple[bool, str, str]:
    """(ok, provider reference, error). The test provider never dials anything."""
    provider = phone()["provider"]
    if provider == "test":
        ref = f"test-{secrets.token_hex(4)}"
        MOCK_CALLS.append({"to": to, "twiml": _twiml(text), "ref": ref})
        return True, ref, ""
    if provider == "twilio":
        sid, token, frm = db.get_setting("twilio_sid", ""), security.decrypt(db.get_setting("twilio_token_enc", "") or ""), db.get_setting("twilio_from", "")
        if not (sid and token and frm):
            return False, "", "Twilio is not fully set up (Account SID, Auth Token and a From number)."
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json", auth=(sid, token),
                                 data={"To": to, "From": frm, "Twiml": _twiml(text), "Timeout": "30"})
        except httpx.HTTPError as e:
            return False, "", f"Twilio could not be reached ({e.__class__.__name__})."
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", "")
            except ValueError:
                msg = r.text[:200]
            return False, "", f"Twilio refused the call ({r.status_code}): {msg}"[:300]
        return True, r.json().get("sid", ""), ""
    return False, "", "No phone provider is set up."


def _phone_cost(days: int) -> float:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return db.one("SELECT COALESCE(SUM(cost_estimate_cents), 0) AS c FROM phone_calls WHERE status = 'placed' AND created_at >= ?", since)["c"]


def _phone_count(hours: int, purpose: str = "briefing") -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return db.one("SELECT COUNT(*) AS n FROM phone_calls WHERE status = 'placed' AND purpose = ? AND created_at >= ?", purpose, since)["n"]


def _log_call(delivery_id: int | None, purpose: str, to: str, ok: bool, ref: str, err: str, refused: bool = False) -> None:
    db.run("INSERT INTO phone_calls(delivery_id, purpose, provider, to_masked, provider_ref, status, cost_estimate_cents, error, created_at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", delivery_id, purpose, phone()["provider"] or "none", mask(to), ref,
           "refused" if refused else "placed" if ok else "failed", EST_CENTS_PER_CALL if ok and phone()["provider"] == "twilio" else 0, err, db.now())


async def start_verification(password: str, confirm_cost: bool) -> dict:
    """You start this yourself: one call to your number that reads a 6 digit code."""
    _require_password(password)
    p = phone()
    if not (p["provider"] and p["number"]):
        raise HTTPException(409, "Choose a provider and enter your number first.")
    if p["provider"] == "twilio" and not confirm_cost:
        raise HTTPException(409, f"This places one real call through Twilio (about {EST_CENTS_PER_CALL:.0f} US cents). Confirm to continue.")
    if _phone_count(24, "verification") >= 3:
        raise HTTPException(429, "Three verification calls in a day is the limit.")
    code = f"{secrets.randbelow(10**6):06d}"
    db.set_setting("phone_code", security.encrypt(code))
    db.set_setting("phone_code_at", db.now())
    spoken = "Your ROOKS verification code is " + ", ".join(code) + ". Again: " + ", ".join(code) + "."
    ok, ref, err = await _place(p["number"], spoken)
    _log_call(None, "verification", p["number"], ok, ref, err)
    if not ok:
        raise HTTPException(502, err)
    return {"placed": True, "to": mask(p["number"])}


def confirm_verification(code: str) -> dict:
    stored = security.decrypt(db.get_setting("phone_code", "") or "")
    at = db.get_setting("phone_code_at", "") or ""
    fresh = at and at >= (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")
    if not (stored and fresh and hmac.compare_digest(stored, re.sub(r"\D", "", code or ""))):
        raise HTTPException(400, "That code is wrong or more than 15 minutes old. Start a new verification call.")
    db.set_setting("phone_verified", "1")
    db.set_setting("phone_code", "")
    audit.record("user", "phone number verified", "", mask(phone()["number"]))
    return status()


# Delivery from check-ins (checkins.send_due)
async def deliver(d: dict) -> tuple[bool, str]:
    if d["channel"] == "call":
        return _invite(d)
    return await _phone(d)


def _invite(d: dict) -> tuple[bool, str]:
    from .scheduler import deliver as inbox

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    sent = db.one("SELECT COUNT(*) AS n FROM call_invites WHERE created_at >= ?", since)["n"]
    if not in_window() or sent >= policy()["invites_per_day"]:
        why = "outside the hours ROOKS may ask to talk" if not in_window() else "today's limit for call invitations was reached"
        inbox(d["title"], f"{d['body']}\n\n(Written instead of a call: {why}.)", "inbox", f"checkin:{d['topic_id']}")
        return True, f"written instead: {why}"
    expires = (datetime.now(timezone.utc) + timedelta(minutes=policy()["invite_minutes"])).isoformat(timespec="seconds")
    db.run("INSERT INTO call_invites(topic_id, delivery_id, title, briefing, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
           d["topic_id"], d["id"], d["title"], d["body"], expires, db.now())
    return True, ""


async def _phone(d: dict) -> tuple[bool, str]:
    from .scheduler import deliver as inbox

    p, pol = phone(), policy()
    refusal = ""
    if not (p["enabled"] and p["verified"] and p["number"] and p["provider"]):
        refusal = "phone calls are not turned on with a verified number"
    elif not in_window():
        refusal = "outside the hours ROOKS may call"
    elif _phone_count(24) >= pol["phone_per_day"] or _phone_count(24 * 7) >= pol["phone_per_week"]:
        refusal = "the daily or weekly call limit was reached"
    elif p["provider"] == "twilio" and _phone_cost(30) + EST_CENTS_PER_CALL > pol["phone_month_cents"]:
        refusal = "the monthly phone budget would be exceeded"
    if refusal:
        _log_call(d["id"], "briefing", p["number"], False, "", refusal, refused=True)
        inbox(d["title"], f"{d['body']}\n\n(Written instead of a phone call: {refusal}.)", "inbox", f"checkin:{d['topic_id']}")
        return True, f"written instead: {refusal}"
    ok, ref, err = await _place(p["number"], f"{d['title']}. {d['body']}")
    _log_call(d["id"], "briefing", p["number"], ok, ref, err)
    # On record in ROOKS either way; no retry for a call (a missed call is not repeated)
    inbox(d["title"], d["body"] + ("" if ok else f"\n\n(The phone call could not be placed: {err})"), "inbox", f"checkin:{d['topic_id']}")
    return ok, err


# Invitations: your answer
def _invite_row(iid: int) -> dict:
    i = db.one("SELECT * FROM call_invites WHERE id = ?", iid)
    if not i:
        raise HTTPException(404, "Invitation not found")
    return i


def accept(iid: int) -> dict:
    """Start the conversation: a conversation in the topic's project, opening with the briefing."""
    from .assistant import _save, new_conversation

    i = _invite_row(iid)
    if i["status"] not in ("pending", "snoozed"):
        raise HTTPException(409, f"This invitation is {i['status']}.")
    t = db.one("SELECT project_id, name FROM topics WHERE id = ?", i["topic_id"]) if i["topic_id"] else None
    cid = new_conversation("web", f"Call: {i['title']}"[:120], project_id=(t or {}).get("project_id"))
    _save(cid, "assistant", f"{i['title']}: {i['briefing']}\n\nWhat would you like to do about it?")
    db.run("UPDATE call_invites SET status = 'accepted', decided_at = ?, conversation_id = ? WHERE id = ?", db.now(), cid, iid)
    return {**_invite_row(iid), "speak": f"{i['title']}. {i['briefing']} What would you like to do about it?"}


def decline(iid: int) -> dict:
    i = _invite_row(iid)
    if i["status"] not in ("pending", "snoozed"):
        raise HTTPException(409, f"This invitation is {i['status']}.")
    db.run("UPDATE call_invites SET status = 'declined', decided_at = ? WHERE id = ?", db.now(), iid)
    _fallback(i, "You declined the call")
    return _invite_row(iid)


def snooze(iid: int, minutes: int = 30) -> dict:
    i = _invite_row(iid)
    if i["status"] not in ("pending", "snoozed"):
        raise HTTPException(409, f"This invitation is {i['status']}.")
    if i["snoozes"] >= 2:
        raise HTTPException(409, "Snoozed twice already. Accept or decline; it will become a written briefing otherwise.")
    until = (datetime.now(timezone.utc) + timedelta(minutes=max(5, min(minutes, 240)))).isoformat(timespec="seconds")
    db.run("UPDATE call_invites SET status = 'snoozed', snoozes = snoozes + 1, snooze_until = ? WHERE id = ?", until, iid)
    return _invite_row(iid)


def _fallback(i: dict, why: str) -> None:
    """The written briefing, exactly once."""
    from .scheduler import deliver as inbox

    if db.changed("UPDATE call_invites SET fallback_sent = 1 WHERE id = ? AND fallback_sent = 0", i["id"]):
        inbox(i["title"], f"{i['briefing']}\n\n({why}; here it is in writing.)", "inbox", f"call:{i['id']}")


def pending() -> list[dict]:
    """Invitations to show now."""
    return db.all("SELECT * FROM call_invites WHERE status = 'pending' AND expires_at > ? ORDER BY id", db.now())


async def tick() -> None:
    """A snooze that has ended brings the invitation back; one not answered in time is missed: one written briefing,
    no further interruption."""
    now = db.now()
    for i in db.all("SELECT id FROM call_invites WHERE status = 'snoozed' AND snooze_until <= ?", now):
        expires = (datetime.now(timezone.utc) + timedelta(minutes=policy()["invite_minutes"])).isoformat(timespec="seconds")
        db.run("UPDATE call_invites SET status = 'pending', expires_at = ? WHERE id = ?", expires, i["id"])
    for i in db.all("SELECT * FROM call_invites WHERE status = 'pending' AND expires_at <= ?", now):
        db.run("UPDATE call_invites SET status = 'missed', decided_at = ? WHERE id = ?", now, i["id"])
        _fallback(i, "You missed the call")
