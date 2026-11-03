"""Text messages through Twilio (feat/team-lead), with the lead's own Twilio account.

Settings: Account SID, Auth Token (kept encrypted) and the Twilio number to send from. Sending is used by the outbox
(same approval rules as every other channel). Replies are read by polling Twilio's message list every check, so no
public URL or open port is needed. See docs/team-lead.md#text-messages-twilio.
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import HTTPException

from ..security import decrypt, encrypt
from . import store

log = logging.getLogger("team.sms")
API = "https://api.twilio.com/2010-04-01"
_transport: httpx.BaseTransport | None = None  # tests swap in a MockTransport


def _client() -> httpx.Client:
    sid, tok = store.setting("twilio_sid"), store.setting("twilio_token")
    return httpx.Client(base_url=f"{API}/Accounts/{sid}", auth=(sid, decrypt(tok) if tok else ""), timeout=20,
                        transport=_transport)


def configured() -> bool:
    return bool(store.setting("twilio_sid") and store.setting("twilio_token") and store.setting("twilio_from"))


def status() -> dict:
    return {"configured": configured(), "from": store.setting("twilio_from"), "sid": store.setting("twilio_sid")[:6] + "…"
            if store.setting("twilio_sid") else ""}


def configure(sid: str | None, token: str | None, from_number: str | None) -> dict:
    if sid is not None:
        if sid and not re.fullmatch(r"AC[0-9a-fA-F]{32}", sid.strip()):
            raise HTTPException(400, "A Twilio Account SID starts with AC and has 34 characters.")
        store.set_setting("twilio_sid", sid.strip())
    if token is not None:
        store.set_setting("twilio_token", encrypt(token.strip()) if token.strip() else "")
    if from_number is not None:
        num = re.sub(r"[^\d+]", "", from_number)
        if num and not re.fullmatch(r"\+\d{8,15}", num):
            raise HTTPException(400, "Use the full number with country code, like +61400000000.")
        store.set_setting("twilio_from", num)
    return status()


def normalise(phone: str) -> str:
    p = re.sub(r"[^\d+]", "", phone or "")
    if p.startswith("0") and store.setting("twilio_from", "").startswith("+61"):
        p = "+61" + p[1:]  # local Australian numbers
    return p


def send(to: str, body: str) -> str:
    if not configured():
        raise RuntimeError("Twilio isn't set up")
    with _client() as c:
        r = c.post("/Messages.json", data={"To": normalise(to), "From": store.setting("twilio_from"), "Body": body[:1500]})
        r.raise_for_status()
        return r.json().get("sid", "")


def poll(limit: int = 50) -> dict:
    """New texts to the Twilio number, handed to the team reply reader (which only acts on team members)."""
    from . import replies

    if not configured():
        return {"read": 0, "reason": "Text messages aren't set up."}
    with _client() as c:
        r = c.get("/Messages.json", params={"To": store.setting("twilio_from"), "PageSize": limit})
        r.raise_for_status()
        msgs = r.json().get("messages", [])
    first = store.setting("twilio_started") != "1"
    n = 0
    for m in msgs:
        if m.get("direction") != "inbound" or not m.get("sid"):
            continue
        if first:
            replies.ensure()
            replies.mark("sms", m["sid"])
            continue
        if replies.seen("sms", m["sid"]):
            continue
        res = replies.receive(m.get("from", ""), m.get("body", ""), "sms", m["sid"])
        n += 1 if res.get("pending") or res.get("applied") else 0
    store.set_setting("twilio_started", "1")
    return {"read": n}
