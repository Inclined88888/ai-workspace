"""Mail through the accounts already signed in on this Mac (System Settings > Internet Accounts).

Apple's own sign-in covers Outlook, Hotmail, Microsoft 365 / Exchange, Gmail, iCloud and Yahoo. Many people are
already signed in there for Apple Mail and Calendar, and anyone can add an account there with the provider's normal
sign-in page. AI Workspace then reads and writes through Apple Mail and Calendar (AppleScript), so it never sees a
password or token and needs no developer registration with Microsoft or Google. This is the route for Outlook, where
Microsoft switched off app passwords and its own sign-in needs an app registration this install may not have.

macOS asks once: "AI Workspace wants to control Mail". Only the desktop app on a Mac offers this.

Calendars are not read this way: Calendar.app's scripting took 50 s for 10 events on a real Mac (measured), too slow to
use. Outlook calendars keep the publish-a-calendar (ICS) route; Google and iCloud calendars have their own routes.

Scripts take the user's values as arguments (osascript `run(argv)`), never pasted into the script text.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from fastapi import HTTPException

TIMEOUT = 90  # Mail and Calendar can be slow to answer the first time (launching, syncing)


def available() -> bool:
    return sys.platform == "darwin" and os.getenv("MAC_ACCOUNTS", "1") not in ("0", "false", "no")


def _osa(script: str, args: list[str], timeout: int = TIMEOUT, lang: str = "JavaScript") -> str:
    if not available():
        raise HTTPException(400, "Mail and calendars on this Mac are only available in the AI Workspace app on a Mac.")
    try:
        r = subprocess.run(["osascript", "-l", lang, "-e", script, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(504, "Apple Mail or Calendar did not answer in time. Open it once, then try again.") from e
    except OSError as e:
        raise HTTPException(502, f"Could not reach Apple Mail or Calendar: {e}") from e
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip()
        if "-1743" in err or "Not authorized" in err or "not allowed" in err.lower():
            raise HTTPException(403, "macOS blocked access. Open System Settings > Privacy & Security > Automation and "
                                     "turn on Mail and Calendar for AI Workspace, then try again.")
        raise HTTPException(502, f"Apple Mail or Calendar said: {err[:300]}")
    return r.stdout.strip()


def _json(script: str, args: list[str], timeout: int = TIMEOUT):
    out = _osa(script, args, timeout)
    try:
        return json.loads(out or "null")
    except ValueError as e:
        raise HTTPException(502, f"Unexpected answer from Apple Mail or Calendar: {out[:200]}") from e


# Mail ----------------------------------------------------------------------------------------------------------------
ACCOUNTS_JS = """
function run(argv) {
  const M = Application("Mail");
  return JSON.stringify(M.accounts().map(a => ({name: a.name(), emails: a.emailAddresses(), enabled: a.enabled(),
                                                 type: String(a.accountType())})));
}"""

_INBOX_JS = """
function inbox(M, name) {
  const a = M.accounts.byName(name);
  const boxes = a.mailboxes();
  for (const b of boxes) { if (/^inbox$/i.test(b.name())) return b; }
  throw new Error("No inbox in the account " + name + ". Is Mail turned on for it in Internet Accounts?");
}
function iso(d) { try { return d ? d.toISOString() : ""; } catch (e) { return ""; } }
"""

LIST_JS = _INBOX_JS + """
function run(argv) {
  const M = Application("Mail");
  const box = inbox(M, argv[0]), limit = parseInt(argv[1], 10) || 40, q = argv[2] || "";
  let msgs = box.messages;
  if (q) msgs = msgs.whose({_or: [{subject: {_contains: q}}, {sender: {_contains: q}}]});
  const n = Math.min(msgs.length, limit), out = [];
  for (let i = 0; i < n; i++) {
    const m = msgs[i];
    out.push({uid: String(m.id()), from: m.sender(), subject: m.subject() || "(no subject)", date: iso(m.dateReceived()),
              unread: !m.readStatus()});
  }
  return JSON.stringify(out);
}"""

GET_JS = _INBOX_JS + """
function run(argv) {
  const M = Application("Mail");
  const found = inbox(M, argv[0]).messages.whose({id: parseInt(argv[1], 10)});
  if (found.length === 0) return "null";
  const m = found[0];
  const addr = list => list.map(r => r.address()).join(", ");
  return JSON.stringify({uid: argv[1], from: m.sender(), reply_to: m.replyTo() || m.sender(), to: addr(m.toRecipients()),
                         cc: addr(m.ccRecipients()), subject: m.subject(), date: iso(m.dateReceived()),
                         message_id: m.messageId() ? "<" + m.messageId() + ">" : "", body: (m.content() || "").slice(0, 100000)});
}"""

# argv: sender, to (comma separated), cc, subject, body, "send" | "draft", attachment paths...
COMPOSE_JS = """
function run(argv) {
  const M = Application("Mail");
  const [sender, to, cc, subject, body, mode] = argv;
  const msg = M.OutgoingMessage({subject: subject, content: body, visible: false, sender: sender});
  M.outgoingMessages.push(msg);
  for (const a of to.split(",").map(s => s.trim()).filter(Boolean)) msg.toRecipients.push(M.Recipient({address: a}));
  for (const a of cc.split(",").map(s => s.trim()).filter(Boolean)) msg.ccRecipients.push(M.CcRecipient({address: a}));
  for (const p of argv.slice(6)) msg.content.attachments.push(M.Attachment({fileName: Path(p)}));
  if (mode === "send") { msg.send(); return "sent"; }
  msg.close({saving: "yes"});
  return "draft";
}"""


def accounts() -> list[dict]:
    """Mail accounts on this Mac: [{name, emails, enabled, type}]. enabled is false when Mail is switched off for it."""
    rows = _json(ACCOUNTS_JS, [], timeout=60) or []
    return [r for r in rows if isinstance(r, dict) and r.get("name")]


def _name(acct: dict) -> str:
    return acct.get("username") or acct.get("name") or ""


def list_messages(acct: dict, folder: str = "INBOX", limit: int = 40, query: str = "") -> list[dict]:
    rows = _json(LIST_JS, [_name(acct), str(int(limit)), query.strip()[:200]]) or []
    return sorted(rows, key=lambda r: r.get("date") or "", reverse=True)


def get_message(acct: dict, uid: str) -> dict:
    if not uid.isdigit():
        raise HTTPException(400, "Invalid message id")
    m = _json(GET_JS, [_name(acct), uid])
    if not m:
        raise HTTPException(404, "Message not found")
    return m


def _compose(acct: dict, to: str, subject: str, body: str, cc: str, mode: str,
             attachments: list[tuple[str, str, bytes]] | None) -> str:
    folder = tempfile.mkdtemp(prefix="aiw-mail-")
    paths = []
    try:
        for filename, _ctype, data in attachments or []:
            p = os.path.join(folder, os.path.basename(filename) or "attachment")
            with open(p, "wb") as f:
                f.write(data)
            paths.append(p)
        return _osa(COMPOSE_JS, [acct["email"], to, cc, subject, body, mode, *paths])
    finally:
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(folder)
        except OSError:
            pass


def send(acct: dict, to: str, subject: str, body: str, in_reply_to: str = "", *, cc: str = "",
         attachments: list[tuple[str, str, bytes]] | None = None) -> str:
    """Sent by Apple Mail from the account's own address (it lands in that account's Sent folder)."""
    _compose(acct, to, subject, body, cc, "send", attachments)
    return in_reply_to or ""


def create_draft(acct: dict, to: str, subject: str, body: str, in_reply_to: str = "") -> str:
    return _compose(acct, to, subject, body, "", "draft", None)


def test(acct: dict) -> None:
    for a in accounts():
        if a["name"] == _name(acct):
            if not a.get("enabled"):
                raise HTTPException(400, f"Mail is switched off for {a['name']} on this Mac. Turn it on in System Settings > "
                                         "Internet Accounts.")
            return
    raise HTTPException(404, f"{_name(acct)} is no longer signed in on this Mac (System Settings > Internet Accounts).")
