"""Assistant tools for automations, background jobs and live documents (feat/v02-automations).

Registered from main.py like the adapt tools. Kept out of assistant.py so the shared registry stays untouched.
"""

from __future__ import annotations

import asyncio
import email
import re
import shutil
from email import policy
from pathlib import Path

from fastapi import HTTPException

from . import db

LIVE_DOCS_PROMPT = (
    "Live documents: when a table, budget, schedule, plan, comparison or chart would help, put it in the reply as an editable "
    "block the user can change in place and export. Use fenced blocks: ```sheet with JSON {\"title\", \"columns\": [..], "
    "\"rows\": [[..]]} where cells may be numbers, text or Excel formulas like \"=B2*C2\" or \"=SUM(D2:D9)\" (row 1 is the "
    "header, so data starts at row 2); ```chart with JSON {\"title\", \"type\": \"bar\"|\"line\"|\"pie\", \"labels\": [..], "
    "\"series\": [{\"name\", \"values\": [..]}]}; ```doc with Markdown for a letter, report or contract draft. Keep normal "
    "prose outside the blocks. Use them only when they help."
)


def _home() -> Path:
    return Path.home().resolve()


def _inside_home(p: Path) -> Path:
    p = p.expanduser().resolve()
    if p != _home() and _home() not in p.parents:
        raise HTTPException(400, "I can only use folders inside your home folder.")
    return p


def _unique(dest: Path) -> Path:
    """Never overwrite: 'invoice.pdf' becomes 'invoice (2).pdf'."""
    if not dest.exists():
        return dest
    for i in range(2, 1000):
        cand = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not cand.exists():
            return cand
    raise HTTPException(409, "Too many files with that name.")


def raw_message(acct: dict, uid: str) -> bytes:
    """The whole message (with attachments) as MIME bytes, from Gmail, Outlook or IMAP."""
    from . import cloud_mail, mail

    if acct.get("kind") == "google":
        j = cloud_mail._call(acct, "GET", f"{cloud_mail.gmail()}/users/me/messages/{uid}", params={"format": "raw"}).json()
        return cloud_mail._b64url_decode(j.get("raw", ""))
    if acct.get("kind") == "microsoft":
        return cloud_mail._call(acct, "GET", f"{cloud_mail.graph()}/me/messages/{uid}/$value").content
    if not str(uid).isdigit():
        raise HTTPException(400, "Invalid message id")
    m = mail._imap(acct)
    try:
        m.select('"INBOX"', readonly=True)
        status, data = m.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            raise HTTPException(404, "Message not found")
        return data[0][1]
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass


def attachments_of(raw: bytes) -> list[tuple[str, str, bytes]]:
    msg = email.message_from_bytes(raw, policy=policy.default)
    out = []
    for part in msg.iter_attachments():
        name = part.get_filename() or "attachment"
        data = part.get_payload(decode=True) or b""
        if data:
            out.append((name, part.get_content_type(), data))
    return out


def save_email_attachments(account_id: int, uid: str, only: str = "", folder: str = "") -> dict:
    """Keep an email's attachments: always added to Files; with folder, also copied into that folder (never overwrites)."""
    from . import analysis, config

    acct = db.one("SELECT * FROM mail_accounts WHERE id = ?", account_id)
    if not acct:
        raise HTTPException(404, "Mail account not found")
    atts = attachments_of(raw_message(acct, str(uid)))
    want = [x.strip(". ").lower() for x in re.split(r"[,\s]+", only or "") if x.strip()]
    if want:
        atts = [a for a in atts if a[0].lower().rsplit(".", 1)[-1] in want or any(w in a[1] for w in want)]
    if not atts:
        return {"saved": [], "note": "That email has no matching attachments."}
    target = None
    if folder:
        base = Path(folder).expanduser()
        if not base.is_absolute():
            base = (_home() / "Documents" / folder) if config.DESKTOP else None
        if base is not None:
            target = _inside_home(base)
            target.mkdir(parents=True, exist_ok=True)
    saved = []
    for name, _ctype, data in atts[:20]:
        f = analysis.add(name, data=data, source="email")
        row = {"file_id": f["id"], "name": name, "summary": f.get("summary", "")}
        if target is not None:
            dest = _unique(target / analysis._safe_name(name))
            dest.write_bytes(data)
            row["saved_to"] = str(dest)
        saved.append(row)
    return {"saved": saved, "folder": str(target) if target else ""}


def add_file_from_path(path: str) -> dict:
    """Add a file from this computer to Files so read_file can read it (desktop app, home folder only)."""
    from . import analysis, config

    if not config.DESKTOP:
        raise HTTPException(400, "Adding by path works in the desktop app.")
    p = _inside_home(Path(path))
    if not p.is_file():
        raise HTTPException(404, f"No file at {p}")
    f = analysis.add(p.name, src=p, source="path")
    return {"file_id": f["id"], "name": f["name"], "summary": f.get("summary", "")}


def create_automation(request: str) -> dict:
    from . import automations

    draft = automations.parse(request)
    if draft["questions"]:
        return {"saved": False, "draft": draft, "note": "Ask the user: " + " ".join(draft["questions"])}
    a = automations.create(draft)
    return {"saved": True, "automation": {"id": a["id"], "name": a["name"], "sentence": a["sentence"]},
            "note": "It is on the Automations page, where the user can change or turn it off."}


def start_job(kind: str, prompt: str, title: str = "") -> dict:
    from . import jobs

    j = jobs.create(kind, prompt, title)
    return {"job_id": j["id"], "status": j["status"], "note": "It runs in the background; progress is on the Jobs board."}


def register_tools() -> None:
    from . import assistant

    if "create_automation" in assistant.TOOL_MAP:
        return
    S, I = assistant.S, assistant.I
    not_unattended = lambda ctx: ctx.channel not in ("automation", "job", "schedule", "agent")  # noqa: E731 - no runaway chains
    new = [
        assistant.Tool("save_email_attachments", "Keep an email's attachments (e.g. the PDF invoice): they are added to Files, and "
                       "with folder (a name like 'Invoices' or a path in the home folder) also copied there. Never overwrites. "
                       "only: file types to keep, e.g. 'pdf'.", {"account_id": I(), "uid": S(), "only": S(), "folder": S()},
                       save_email_attachments, ("account_id", "uid"), effect="write", untrusted=True),
        assistant.Tool("add_file_from_path", "Add a file on this computer (in the home folder) to Files so read_file can read it.",
                       {"path": S()}, add_file_from_path, ("path",), effect="write"),
        assistant.Tool("create_automation", "Set up a trigger automation from the user's words: 'when X happens, do Y'. Triggers: new "
                       "email, calendar event soon, work site change, file in a folder, a schedule, something shared from the phone.",
                       {"request": S("the user's own words")}, create_automation, ("request",), when=not_unattended, effect="persistent",
                       confirm=lambda a: "Setting up an automation always needs your OK."),
        assistant.Tool("start_background_job", "Run a long task in the background on the Jobs board so the user can keep chatting. "
                       "kind: chat, browser, computer or files.", {"kind": S(), "prompt": S("self contained instruction"), "title": S()},
                       start_job, ("prompt",), when=not_unattended, effect="write"),
    ]
    for t in new:
        assistant.TOOLS.append(t)
        assistant.TOOL_MAP[t.name] = t
    assistant.UNTRUSTED_TOOLS.add("save_email_attachments")  # its file summaries are third party content
