"""IMAP read and SMTP send. Works with any standard provider (Fastmail, iCloud, Yahoo, Gmail app passwords, self hosted).

Accounts connected with "Connect Google" or "Connect Microsoft" (kind google | microsoft) go through cloud_mail.py
instead, which speaks the Gmail API and Microsoft Graph and returns the same shapes.
"""

import email
import imaplib
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr, parsedate_to_datetime

from fastapi import HTTPException

from . import egress
from .security import decrypt

CTX = ssl.create_default_context()


def _h(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError):
        return value


def _imap(acct: dict) -> imaplib.IMAP4_SSL:
    try:
        with egress.track("LOGIN", f"imaps://{acct['imap_host']}:{acct['imap_port']}"):
            m = imaplib.IMAP4_SSL(acct["imap_host"], acct["imap_port"], ssl_context=CTX, timeout=30)
            m.login(acct["username"], decrypt(acct["password_enc"]))
        return m
    except (imaplib.IMAP4.error, OSError) as e:
        raise HTTPException(502, f"IMAP connection failed: {e}") from e


def _body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        html = ""
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if ctype == "text/plain":
                return text
            if ctype == "text/html" and not html:
                html = text
        if html:
            import trafilatura

            return trafilatura.extract(html) or ""
        return ""
    payload = msg.get_payload(decode=True) or b""
    text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    if msg.get_content_type() == "text/html":
        import trafilatura

        return trafilatura.extract(text) or ""
    return text


def _cloud(acct: dict):
    if acct.get("kind") in ("google", "microsoft"):
        from . import cloud_mail

        return cloud_mail
    return None


def list_messages(acct: dict, folder: str = "INBOX", limit: int = 40, query: str = "") -> list[dict]:
    if cloud := _cloud(acct):
        return cloud.list_messages(acct, folder, limit, query)
    m = _imap(acct)
    try:
        status, _ = m.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise HTTPException(404, f"Folder {folder} not found")
        if query:
            safe = query.replace("\\", "").replace('"', "")
            status, data = m.uid("SEARCH", "CHARSET", "UTF-8", "TEXT", f'"{safe}"')
        else:
            status, data = m.uid("SEARCH", None, "ALL")
        uids = data[0].split()[-limit:][::-1] if status == "OK" and data and data[0] else []
        if not uids:
            return []
        status, data = m.uid("FETCH", b",".join(uids), "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
        out = []
        for item in data:
            if not isinstance(item, tuple):
                continue
            meta, raw = item
            hdr = email.message_from_bytes(raw)
            meta_s = meta.decode(errors="replace")
            uid = meta_s.split("UID ")[1].split()[0].rstrip(")") if "UID " in meta_s else ""
            try:
                date = parsedate_to_datetime(hdr["Date"]).isoformat() if hdr["Date"] else ""
            except (TypeError, ValueError):
                date = ""
            out.append({
                "uid": uid,
                "from": _h(hdr["From"]),
                "subject": _h(hdr["Subject"]) or "(no subject)",
                "date": date,
                "unread": "\\Seen" not in meta_s,
            })
        out.sort(key=lambda x: x["date"], reverse=True)
        return out
    finally:
        try:
            m.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def get_message(acct: dict, uid: str, folder: str = "INBOX") -> dict:
    if cloud := _cloud(acct):
        return cloud.get_message(acct, uid)
    if not uid.isdigit():
        raise HTTPException(400, "Invalid message id")
    m = _imap(acct)
    try:
        m.select(f'"{folder}"', readonly=True)
        status, data = m.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            raise HTTPException(404, "Message not found")
        msg = email.message_from_bytes(data[0][1])
        return {
            "uid": uid,
            "from": _h(msg["From"]),
            "reply_to": parseaddr(_h(msg["Reply-To"] or msg["From"]))[1],
            "to": _h(msg["To"]),
            "cc": _h(msg["Cc"]),
            "subject": _h(msg["Subject"]),
            "date": msg["Date"] or "",
            "message_id": msg["Message-ID"] or "",
            "body": _body_text(msg)[:100_000],
        }
    finally:
        try:
            m.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def send(acct: dict, to: str, subject: str, body: str, in_reply_to: str = "") -> str:
    if cloud := _cloud(acct):
        return cloud.send(acct, to, subject, body, in_reply_to)
    msg = EmailMessage()
    msg["From"] = f'{acct["name"]} <{acct["email"]}>'
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=acct["email"].split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    password = decrypt(acct["password_enc"])
    try:
        with egress.track("SEND", f"smtp://{acct['smtp_host']}:{acct['smtp_port']}"):
            if acct["smtp_security"] == "starttls":
                with smtplib.SMTP(acct["smtp_host"], acct["smtp_port"], timeout=30) as s:
                    s.starttls(context=CTX)
                    s.login(acct["username"], password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP_SSL(acct["smtp_host"], acct["smtp_port"], context=CTX, timeout=30) as s:
                    s.login(acct["username"], password)
                    s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise HTTPException(502, f"SMTP send failed: {e}") from e
    return msg["Message-ID"]


def test(acct: dict) -> None:
    if cloud := _cloud(acct):
        return cloud.test(acct)
    m = _imap(acct)
    m.logout()
