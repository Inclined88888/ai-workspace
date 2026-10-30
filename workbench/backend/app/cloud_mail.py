"""Mail and calendar over the providers' own APIs, for accounts connected with "Connect Google" / "Connect Microsoft".

Google: Gmail API (read and search with gmail.readonly, drafts and send with gmail.compose) and Google Calendar API
(calendar.events). Microsoft: Microsoft Graph v1.0 (Mail.ReadWrite, Mail.Send, Calendars.ReadWrite).

mail.py and calendar_sync.py call into here when an account's kind is "google" or "microsoft", and return the same
shapes as IMAP and CalDAV, so every page and tool works the same with either. Every call refreshes the access token
when needed (connect.access_token) and retries once after a 401. API bases can be pointed elsewhere for tests with
GMAIL_API_BASE, GCAL_API_BASE, GOOGLE_USERINFO_URL and GRAPH_API_BASE.
"""

from __future__ import annotations

import base64
import email
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parseaddr
from urllib.parse import quote

import httpx
from fastapi import HTTPException

KINDS = ("google", "microsoft")


def _base(env: str, default: str) -> str:
    return os.getenv(env, default).rstrip("/")


def gmail() -> str:
    return _base("GMAIL_API_BASE", "https://gmail.googleapis.com/gmail/v1")


def gcal() -> str:
    return _base("GCAL_API_BASE", "https://www.googleapis.com/calendar/v3")


def graph() -> str:
    return _base("GRAPH_API_BASE", "https://graph.microsoft.com/v1.0")


def _short(scope: str) -> str:
    return scope.rsplit("/", 1)[-1] if scope.startswith("https://graph.microsoft.com/") else scope


def capabilities(provider: str, granted: set[str]) -> tuple[bool, bool]:
    """(mail, calendar) the user actually allowed; Google's consent screen lets people untick individual scopes."""
    g = {_short(s) for s in granted}
    if provider == "google":
        return "https://www.googleapis.com/auth/gmail.readonly" in g, "https://www.googleapis.com/auth/calendar.events" in g
    return "Mail.ReadWrite" in g or "Mail.Read" in g, "Calendars.ReadWrite" in g or "Calendars.Read" in g


def _explain(provider: str, r: httpx.Response) -> str:
    try:
        body = r.json()
        err = body.get("error", {}) if isinstance(body, dict) else {}
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
    except ValueError:
        msg = r.text[:200]
    name = "Google" if provider == "google" else "Microsoft"
    if r.status_code == 403:
        return (f"{name} said this account didn't allow that ({msg[:160]}). Disconnect and connect again, allowing mail and "
                "calendar, or ask your admin to approve the app.")
    if r.status_code == 404:
        return f"{name} couldn't find that item."
    if r.status_code == 429:
        return f"{name} is rate limiting requests right now. Try again in a minute."
    return f"{name} API error {r.status_code}: {msg[:200]}"


def whoami(provider: str, token: str) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    try:
        if provider == "google":
            r = httpx.get(_base("GOOGLE_USERINFO_URL", "https://openidconnect.googleapis.com/v1/userinfo"), headers=h, timeout=15)
            if r.status_code >= 400:
                raise HTTPException(502, _explain(provider, r))
            j = r.json()
            return {"email": j.get("email", ""), "name": j.get("name", "")}
        r = httpx.get(f"{graph()}/me", params={"$select": "displayName,mail,userPrincipalName"}, headers=h, timeout=15)
        if r.status_code >= 400:
            raise HTTPException(502, _explain(provider, r))
        j = r.json()
        return {"email": j.get("mail") or j.get("userPrincipalName", ""), "name": j.get("displayName", "")}
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Couldn't reach {provider.title()}: {e}") from e


def _call(acct: dict, method: str, url: str, *, ok_404: bool = False, **kw) -> httpx.Response:
    from . import connect

    aid = acct["linked_account_id"]
    headers = kw.pop("headers", {})
    r = None
    for attempt in (0, 1):
        token = connect.access_token(aid, force=attempt == 1)
        try:
            r = httpx.request(method, url, headers={"Authorization": f"Bearer {token}", **headers}, timeout=30, **kw)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Couldn't reach {acct['kind'].title()}: {e}") from e
        if r.status_code != 401:
            break
    if r.status_code == 401:
        connect.mark_reauth(aid, "The provider rejected the access token")
        raise HTTPException(401, f"{acct['email']} needs to be connected again (Settings > Connect your mail & calendar).")
    if r.status_code >= 400 and not (ok_404 and r.status_code == 404):
        raise HTTPException(502 if r.status_code != 403 else 403, _explain(acct["kind"], r))
    return r


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _addr(a: dict | None) -> str:
    e = (a or {}).get("emailAddress") or {}
    return f'{e.get("name", "")} <{e.get("address", "")}>'.strip() if e.get("name") else e.get("address", "")


def _recipients(to: str) -> list[dict]:
    out = [{"emailAddress": {"address": addr, **({"name": name} if name else {})}} for name, addr in getaddresses([to]) if "@" in addr]
    if not out:
        raise HTTPException(400, "Add at least one valid email address")
    return out


def _mime(acct: dict, to: str, subject: str, body: str, in_reply_to: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f'{acct["name"]} <{acct["email"]}>' if acct.get("name") and acct["name"] != acct["email"] else acct["email"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=acct["email"].split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return msg


# Mail ---------------------------------------------------------------------------------------------------------------
def list_messages(acct: dict, folder: str = "INBOX", limit: int = 40, query: str = "") -> list[dict]:
    if acct["kind"] == "google":
        q = query.strip() or "in:inbox"
        ids = [m["id"] for m in (_call(acct, "GET", f"{gmail()}/users/me/messages", params={"maxResults": limit, "q": q}).json()
                                 .get("messages") or [])]

        def meta(mid: str) -> dict:
            params = [("format", "metadata")] + [("metadataHeaders", h) for h in ("From", "Subject", "Date")]
            j = _call(acct, "GET", f"{gmail()}/users/me/messages/{mid}", params=params).json()
            hdr = {h["name"].lower(): h["value"] for h in (j.get("payload") or {}).get("headers") or []}
            ts = int(j.get("internalDate") or 0) / 1000
            return {"uid": mid, "from": hdr.get("from", ""), "subject": hdr.get("subject") or "(no subject)",
                    "date": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else "",
                    "unread": "UNREAD" in (j.get("labelIds") or []), "snippet": j.get("snippet", ""), "thread_id": j.get("threadId", "")}

        with ThreadPoolExecutor(max_workers=8) as pool:
            out = list(pool.map(meta, ids))
        return sorted(out, key=lambda x: x["date"], reverse=True)
    select = "id,subject,from,receivedDateTime,isRead,bodyPreview,conversationId"
    if query.strip():
        safe = query.replace('"', "").replace("\\", "")
        r = _call(acct, "GET", f"{graph()}/me/messages", params={"$search": f'"{safe}"', "$top": limit, "$select": select})
    else:
        r = _call(acct, "GET", f"{graph()}/me/mailFolders/inbox/messages",
                  params={"$top": limit, "$select": select, "$orderby": "receivedDateTime desc"})
    out = [{"uid": m["id"], "from": _addr(m.get("from")), "subject": m.get("subject") or "(no subject)",
            "date": m.get("receivedDateTime", ""), "unread": not m.get("isRead", True), "snippet": m.get("bodyPreview", ""),
            "thread_id": m.get("conversationId", "")} for m in r.json().get("value") or []]
    return sorted(out, key=lambda x: x["date"], reverse=True)


def get_message(acct: dict, uid: str) -> dict:
    from .mail import _body_text, _h

    if not uid or len(uid) > 400:
        raise HTTPException(400, "Invalid message id")
    if acct["kind"] == "google":
        j = _call(acct, "GET", f"{gmail()}/users/me/messages/{quote(uid, safe='')}", params={"format": "raw"}).json()
        msg = email.message_from_bytes(_b64url_decode(j.get("raw", "")))
        return {"uid": uid, "from": _h(msg["From"]), "reply_to": parseaddr(_h(msg["Reply-To"] or msg["From"]))[1],
                "to": _h(msg["To"]), "cc": _h(msg["Cc"]), "subject": _h(msg["Subject"]), "date": msg["Date"] or "",
                "message_id": msg["Message-ID"] or "", "thread_id": j.get("threadId", ""), "body": _body_text(msg)[:100_000]}
    m = _call(acct, "GET", f"{graph()}/me/messages/{quote(uid, safe='')}",
              params={"$select": "subject,from,toRecipients,ccRecipients,replyTo,receivedDateTime,internetMessageId,body,conversationId"},
              headers={"Prefer": 'outlook.body-content-type="text"'}).json()
    reply_to = (m.get("replyTo") or [None])[0] or m.get("from")
    return {"uid": uid, "from": _addr(m.get("from")), "reply_to": ((reply_to or {}).get("emailAddress") or {}).get("address", ""),
            "to": ", ".join(_addr(a) for a in m.get("toRecipients") or []), "cc": ", ".join(_addr(a) for a in m.get("ccRecipients") or []),
            "subject": m.get("subject", ""), "date": m.get("receivedDateTime", ""), "message_id": m.get("internetMessageId", ""),
            "thread_id": m.get("conversationId", ""), "body": ((m.get("body") or {}).get("content") or "")[:100_000]}


def _gmail_thread(acct: dict, in_reply_to: str) -> str:
    if not in_reply_to:
        return ""
    r = _call(acct, "GET", f"{gmail()}/users/me/messages", params={"q": f"rfc822msgid:{in_reply_to.strip('<>')}", "maxResults": 1})
    found = r.json().get("messages") or []
    return found[0].get("threadId", "") if found else ""


def _graph_original(acct: dict, in_reply_to: str) -> str:
    if not in_reply_to:
        return ""
    safe = in_reply_to.replace("'", "''")
    r = _call(acct, "GET", f"{graph()}/me/messages", params={"$filter": f"internetMessageId eq '{safe}'", "$select": "id", "$top": 1})
    found = r.json().get("value") or []
    return found[0]["id"] if found else ""


def send(acct: dict, to: str, subject: str, body: str, in_reply_to: str = "") -> str:
    if acct["kind"] == "google":
        msg = _mime(acct, to, subject, body, in_reply_to)
        payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        thread = _gmail_thread(acct, in_reply_to)
        if thread:
            payload["threadId"] = thread
        sent = _call(acct, "POST", f"{gmail()}/users/me/messages/send", json=payload).json()
        return msg["Message-ID"] if sent.get("id") else ""
    recipients = _recipients(to)
    original = _graph_original(acct, in_reply_to)
    if original:  # a real reply keeps Outlook's conversation threading
        _call(acct, "POST", f"{graph()}/me/messages/{quote(original, safe='')}/reply",
              json={"message": {"toRecipients": recipients}, "comment": body})
    else:
        _call(acct, "POST", f"{graph()}/me/sendMail", json={"message": {
            "subject": subject, "body": {"contentType": "Text", "content": body}, "toRecipients": recipients}, "saveToSentItems": True})
    return "sent"


def create_draft(acct: dict, to: str, subject: str, body: str, in_reply_to: str = "") -> str:
    """Also save the draft in the provider's own Drafts folder, so it can be finished on the phone or the web."""
    if acct["kind"] == "google":
        msg = _mime(acct, to, subject, body, in_reply_to)
        payload = {"message": {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}}
        thread = _gmail_thread(acct, in_reply_to)
        if thread:
            payload["message"]["threadId"] = thread
        return _call(acct, "POST", f"{gmail()}/users/me/drafts", json=payload).json().get("id", "")
    original = _graph_original(acct, in_reply_to)
    if original:
        d = _call(acct, "POST", f"{graph()}/me/messages/{quote(original, safe='')}/createReply", json={"comment": body}).json()
        return d.get("id", "")
    d = _call(acct, "POST", f"{graph()}/me/messages", json={
        "subject": subject, "body": {"contentType": "Text", "content": body}, "toRecipients": _recipients(to)}).json()
    return d.get("id", "")


def test(acct: dict) -> None:
    list_messages(acct, limit=1)


# Calendar -----------------------------------------------------------------------------------------------------------
def _utc(value: str) -> str:
    v = value.strip()
    if "." in v:  # Graph: 2026-10-30T09:00:00.0000000
        v = v.split(".")[0]
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def list_events(cal: dict, start: datetime, end: datetime) -> list[dict]:
    acct = _account_for_calendar(cal)
    rows = []
    if acct["kind"] == "google":
        params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "orderBy": "startTime",
                  "maxResults": 2500}
        for _ in range(10):
            j = _call(acct, "GET", f"{gcal()}/calendars/primary/events", params=params).json()
            for ev in j.get("items") or []:
                if ev.get("status") == "cancelled":
                    continue
                s, e = ev.get("start") or {}, ev.get("end") or {}
                all_day = "date" in s
                st = s["date"] if all_day else _utc(s.get("dateTime", ""))
                en = e.get("date") if all_day else _utc(e.get("dateTime") or s.get("dateTime", ""))
                rows.append({"uid": f'{ev["id"]}|{st}', "title": ev.get("summary", "(busy)"), "start": st,
                             "end": en or st, "all_day": int(all_day), "location": ev.get("location", ""),
                             "description": (ev.get("description") or "")[:5000]})
            if not j.get("nextPageToken"):
                break
            params["pageToken"] = j["nextPageToken"]
        return rows
    url = f"{graph()}/me/calendarView"
    params = {"startDateTime": start.isoformat(), "endDateTime": end.isoformat(), "$top": 200,
              "$select": "id,subject,start,end,isAllDay,location,bodyPreview,isCancelled"}
    for _ in range(20):
        j = _call(acct, "GET", url, params=params, headers={"Prefer": 'outlook.timezone="UTC"'}).json()
        for ev in j.get("value") or []:
            if ev.get("isCancelled"):
                continue
            all_day = bool(ev.get("isAllDay"))
            st_raw, en_raw = (ev.get("start") or {}).get("dateTime", ""), (ev.get("end") or {}).get("dateTime", "")
            st = st_raw[:10] if all_day else _utc(st_raw)
            en = en_raw[:10] if all_day else _utc(en_raw)
            rows.append({"uid": f'{ev["id"]}|{st}', "title": ev.get("subject", "(busy)"), "start": st, "end": en,
                         "all_day": int(all_day), "location": ((ev.get("location") or {}).get("displayName") or ""),
                         "description": (ev.get("bodyPreview") or "")[:5000]})
        nxt = j.get("@odata.nextLink")
        if not nxt:
            break
        url, params = nxt, None
    return rows


def create_event(cal: dict, title: str, start: str, end: str, all_day: bool, location: str, description: str,
                 attendees: list[str] | None = None) -> str:
    """Create the event in the provider's calendar. With attendees, the provider emails them an invitation."""
    acct = _account_for_calendar(cal)
    people = [a.strip() for a in attendees or [] if "@" in a]
    if acct["kind"] == "google":
        when = (lambda v: {"date": v[:10]}) if all_day else (lambda v: {"dateTime": v})
        body = {"summary": title, "start": when(start), "end": when(end)}
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        if people:
            body["attendees"] = [{"email": a} for a in people]
        ev = _call(acct, "POST", f"{gcal()}/calendars/primary/events", params={"sendUpdates": "all" if people else "none"}, json=body).json()
        return ev.get("id", "")
    if all_day:
        from .assistant import timezone_name

        tz = timezone_name()
        s, e = {"dateTime": f"{start[:10]}T00:00:00", "timeZone": tz}, {"dateTime": f"{end[:10]}T00:00:00", "timeZone": tz}
    else:
        fmt = lambda v: datetime.fromisoformat(v).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731
        s, e = {"dateTime": fmt(start), "timeZone": "UTC"}, {"dateTime": fmt(end), "timeZone": "UTC"}
    body = {"subject": title, "start": s, "end": e, "isAllDay": all_day,
            "body": {"contentType": "text", "content": description or ""}}
    if location:
        body["location"] = {"displayName": location}
    if people:
        body["attendees"] = [{"emailAddress": {"address": a}, "type": "required"} for a in people]
    return _call(acct, "POST", f"{graph()}/me/events", json=body).json().get("id", "")


def _account_for_calendar(cal: dict) -> dict:
    from . import db

    acct = db.one("SELECT * FROM linked_accounts WHERE id = ?", cal.get("linked_account_id"))
    if not acct:
        raise HTTPException(404, "The account behind this calendar was disconnected")
    return {"linked_account_id": acct["id"], "kind": acct["provider"], "email": acct["email"], "name": acct["name"]}


def default_window() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=60), now + timedelta(days=365)

