"""The few places teammates plug into shared files (feat/grok-style). Every hook swallows its own errors."""

from __future__ import annotations

import logging
import re

log = logging.getLogger("teammates")


def context(ctx, user_text: str) -> list[str]:
    """assistant.system_prompt: who is answering, its persona and its own memory."""
    try:
        from . import computer, memory, store

        t, gid = store.teammate_for(getattr(ctx, "conversation_id", None))
        if not t:
            return []
        ctx.teammate = t
        ctx.group_id = gid
        lines = [f"In this conversation you are {t['name']}" + (f", {t['role']}" if t["role"] else "") + ". "
                 "The user has several AI teammates; answer as this one."]
        if t["persona"]:
            lines.append(f"How you work (the user's words): {t['persona']}")
        lines.append(f"Your own working folder on this computer: {computer.space(t['id'])['work']}. Save files you make there.")
        notes = memory.recall(t["id"], t["memory_scope"], user_text or "")
        if notes:
            lines.append("What you remember from earlier work:\n" + "\n".join(f"- {n}" for n in notes))
        lines.append("To send an email or a Slack or Telegram message, call draft_message: the user sees an editable card and "
                     "sends it themselves. Never ask for a password or card number in chat: call request_secure_form.")
        if gid:
            lines.append("This is a group chat: you have no tools that send or change things outside the app here.")
        return ["\n".join(lines)]
    except Exception as e:  # noqa: BLE001
        log.warning("teammate context skipped: %s", e)
        return []


def filter_tools(ctx, tools: list) -> list:
    """assistant.available_tools: a teammate only gets its tool groups; a group chat gets nothing that acts outside."""
    try:
        from . import store

        t, gid = store.teammate_for(getattr(ctx, "conversation_id", None))
        if not t:
            return tools
        out = tools
        if t["tools"]:
            pats = [re.compile(store.TOOL_GROUPS[g]["match"]) for g in t["tools"] if g in store.TOOL_GROUPS]
            every = [re.compile(v["match"]) for v in store.TOOL_GROUPS.values()]
            out = [x for x in out if not any(p.search(x.name) for p in every) or any(p.search(x.name) for p in pats)]
        out = [x for x in out if x.name not in SEND_TOOLS]  # drafts are the only way out
        if gid:
            out = [x for x in out if x.effect not in ("external", "persistent") and not x.confirm and not x.always_ask
                   and x.name != "request_secure_form"]
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("teammate tool filter skipped: %s", e)
        return tools


def _draft_message(channel: str, to: str, body: str, subject: str = "", ctx=None) -> dict:
    from . import outbox, store

    t, gid = store.teammate_for(getattr(ctx, "conversation_id", None))
    d = outbox.draft(channel, to, body, subject, t["id"] if t else None, getattr(ctx, "conversation_id", None))
    return {"draft_id": d["id"], "status": "draft",
            "note": "Shown to the user as an editable card. It is NOT sent; the user presses Send in the app."}


def _request_secure_form(kind: str, label: str, reason: str = "", ctx=None) -> dict:
    from . import vault

    f = vault.open_form(kind, label, reason, getattr(ctx, "conversation_id", None))
    return {"form_id": f["id"], "status": "waiting for the user",
            "note": "A secure form is shown in the chat. The values go straight to the Vault; you will only ever see the item id."}


def _vault_list(ctx=None) -> list:
    from . import store, vault

    t, _ = store.teammate_for(getattr(ctx, "conversation_id", None))
    return vault.for_model(t["id"] if t else None)


def _remember_for_me(content: str, ctx=None) -> dict:
    from . import store

    t, _ = store.teammate_for(getattr(ctx, "conversation_id", None))
    if not t:
        return {"error": "Not in a teammate conversation; use remember instead."}
    return store.add_note(t["id"], content)


def _save_page(title: str, body: str, ctx=None) -> dict:
    from . import pages, store

    t, _ = store.teammate_for(getattr(ctx, "conversation_id", None))
    p = pages.create(title, body, t["id"] if t else None, getattr(ctx, "conversation_id", None))
    return {"page_id": p["id"], "title": p["title"], "open": f"/pages/{p['id']}"}


def register_tools() -> None:
    from .. import assistant

    S = assistant.S
    T = assistant.Tool
    new = [
        T("draft_message", "Draft an email, Slack or Telegram message for the user to review and send themselves. It is never sent by you.",
          {"channel": S("email | slack | telegram"), "to": S("email addresses, Slack channel/member id, or Telegram chat id"),
           "body": S("the message"), "subject": S("email subject")}, _draft_message, ("channel", "to", "body"), effect="write"),
        T("request_secure_form", "Ask the user for a login, card, address or secure note through a secure form card. "
          "Values go to the Vault and are never shown to you.",
          {"kind": S("login | card | address | note"), "label": S("what it is, e.g. 'Bunnings trade login'"), "reason": S("why you need it")},
          _request_secure_form, ("kind", "label"), effect="write"),
        T("vault_list", "List what is in the user's Vault (labels, sites and masked hints only).", {}, _vault_list),
        T("remember_for_me", "Save something to this teammate's own memory (not shared with the user's other teammates).",
          {"content": S("what to remember")}, _remember_for_me, ("content",), effect="write"),
        T("save_page", "Save a report, plan or dashboard as a Page the user can keep, edit, export and share.",
          {"title": S("page title"), "body": S("Markdown content")}, _save_page, ("title", "body"), effect="write"),
    ]
    have = {t.name for t in assistant.TOOLS}
    for t in new:
        if t.name not in have:
            assistant.TOOLS.append(t)
            assistant.TOOL_MAP[t.name] = t


# Draft-before-send is the only way a teammate sends anything. Every tool that delivers a message to someone else is
# listed here with how its arguments map onto a draft; a call is turned into a draft the user sends from the app.
SEND_TOOLS = {
    "send_email": ("email", lambda a: (a.get("to", ""), a.get("subject", ""), a.get("body", ""))),
    "send_telegram": ("telegram", lambda a: (str(a.get("chat_id", "")), "", a.get("text", "") or a.get("body", ""))),
    "send_slack": ("slack", lambda a: (a.get("channel", ""), "", a.get("text", "") or a.get("body", ""))),
}


def as_draft(name: str, raw_args: str, ctx) -> str:
    """assistant.run_tool: in a teammate's conversation (chat, group, routine, Slack, Telegram) a send becomes a draft."""
    if name not in SEND_TOOLS:
        return ""
    import json

    from . import outbox, store

    t, _ = store.teammate_for(getattr(ctx, "conversation_id", None))
    if not t:
        return ""
    try:
        args = json.loads(raw_args or "{}")
    except ValueError:
        args = {}
    channel, fn = SEND_TOOLS[name]
    to, subject, body = fn(args if isinstance(args, dict) else {})
    d = outbox.draft(channel, to, body, subject, t["id"], getattr(ctx, "conversation_id", None))
    return json.dumps({"draft_id": d["id"], "status": "draft", "sent": False,
                       "note": "Teammates don't send. This is now a draft card the user reviews and sends from the app. "
                               "Tell the user it's ready for them to send; do not say it was sent."})
