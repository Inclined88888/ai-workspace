"""HTTP API for teammates, groups, vault, outbox, pages, channels and the phone link (feat/grok-style)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .. import db
from ..mobile import require_device
from ..security import require_user
from . import channels, computer, groups, outbox, pages, phonelink, store, vault

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
device = APIRouter(prefix="/api/mobile")
public = APIRouter()


class TeammateIn(BaseModel):
    name: str | None = Field(None, max_length=40)
    template: str | None = None
    role: str | None = Field(None, max_length=120)
    persona: str | None = Field(None, max_length=4000)
    avatar: str | None = Field(None, max_length=8)
    color: str | None = None
    memory_scope: str | None = None
    tools: list[str] | None = None
    model: str | None = None
    section: str | None = None
    section_id: int | None = None


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=50_000)
    conversation_id: int | None = None


class TextIn(BaseModel):
    content: str = Field(..., max_length=20000)
    reply_to: int | None = None


class GroupIn(BaseModel):
    name: str = ""
    members: list[int] | None = None


class RoutineIn(BaseModel):
    text: str = Field(..., max_length=2000)


# Teammates --------------------------------------------------------------------------------------------------------
@router.get("/teammates")
def list_teammates():
    return {**store.listing(), "groups": groups.listing()}


@router.post("/teammates")
def create_teammate(body: TeammateIn):
    return store.create(body.model_dump(exclude_none=True))


@router.get("/teammates/{tid}")
def get_teammate(tid: int):
    return {**store.get(tid), "conversations": store.conversations(tid), "notes": store.notes(tid), "computer": computer.overview(tid),
            "routines": _routines(tid), "pages": pages.listing(tid)}


@router.patch("/teammates/{tid}")
def patch_teammate(tid: int, body: TeammateIn):
    return store.update(tid, body.model_dump(exclude_unset=True))


@router.delete("/teammates/{tid}")
def delete_teammate(tid: int):
    return store.delete(tid)


@router.post("/teammates/{tid}/chat")
def teammate_chat(tid: int, body: ChatIn, idempotency_key: str | None = Header(None)):
    """One turn with this teammate, streamed like /api/assistant/chat."""
    from .. import assistant
    from ..api import sse

    t = store.get(tid)
    cid = body.conversation_id
    if cid:
        owner, _ = store.teammate_for(cid)
        if not owner or owner["id"] != tid:
            raise HTTPException(404, "Conversation not found for this teammate")
    else:
        cid = assistant.new_conversation("web")
        store.link(cid, tid)

    async def events():
        yield {"type": "start", "conversation_id": cid, "teammate_id": tid}
        ctx = assistant.Ctx(channel="web", conversation_id=cid, request_key=idempotency_key or "")
        before = (db.one("SELECT MAX(id) AS m FROM messages WHERE conversation_id = ?", cid) or {}).get("m") or 0
        async for e in assistant.run_turn(ctx, body.message, t["model"] or None):
            yield e
        last = store.history(tid, cid)["messages"]
        if last and last[-1]["role"] == "assistant" and last[-1].get("choices") and last[-1]["id"] > before:
            yield {"type": "choices", **last[-1]["choices"]}
        for f in vault.forms(cid):
            yield {"type": "secure_form", **f}
        for d in outbox.listing("draft", cid):
            yield {"type": "draft", **d}

    return sse(events())


@router.get("/teammates/{tid}/conversations/{cid}")
def teammate_history(tid: int, cid: int):
    h = store.history(tid, cid)
    h["drafts"] = outbox.listing("draft", cid)
    h["forms"] = vault.forms(cid)
    return h


@router.post("/teammates/{tid}/notes")
def add_note(tid: int, body: TextIn):
    return store.add_note(tid, body.content)


@router.delete("/teammates/{tid}/notes/{nid}")
def delete_note(tid: int, nid: int):
    return store.delete_note(tid, nid)


@router.get("/teammates/{tid}/template")
def export_template(tid: int, notes: bool = False):
    return store.export_template(tid, notes)


@router.post("/teammates/import")
def import_template(data: dict[str, Any]):
    return store.import_template(data)


class SignInIn(BaseModel):
    url: str = Field(..., max_length=2000)


@router.post("/teammates/{tid}/computer/sign-in")
async def computer_sign_in(tid: int, body: SignInIn):
    store.get(tid)
    return await computer.sign_in(tid, body.url)


def _routines(tid: int) -> list[dict]:
    from .. import automations

    out = []
    for r in db.all("SELECT automation_id FROM tm_routines WHERE teammate_id = ?", tid):
        try:
            out.append(automations.get(r["automation_id"]))
        except HTTPException:
            db.run("DELETE FROM tm_routines WHERE automation_id = ?", r["automation_id"])
    return out


@router.post("/teammates/{tid}/routines")
def add_routine(tid: int, body: RoutineIn):
    """A routine owned by this teammate: an automation (feat/v02-automations) parsed from plain words."""
    from .. import automations

    store.get(tid)
    parsed = automations.parse(body.text)
    if not parsed.get("ok", True) or not parsed.get("trigger_kind"):
        raise HTTPException(400, parsed.get("error") or parsed.get("message") or "I couldn't work out when that should run. Try 'Every weekday at 8am, ...'")
    a = automations.create({k: parsed[k] for k in ("name", "trigger_kind", "trigger", "action_kind", "action") if k in parsed}
                           | {"request": body.text, "ask_first": True})
    db.run("INSERT OR REPLACE INTO tm_routines(automation_id, teammate_id) VALUES (?, ?)", a["id"], tid)
    return a


# Sections ---------------------------------------------------------------------------------------------------------
class SectionIn(BaseModel):
    name: str = Field(..., max_length=40)


@router.post("/teammates/sections")
def add_section(body: SectionIn):
    return {"id": store.section_for(body.name), "sections": store.sections()}


@router.patch("/teammates/sections/{sid}")
def rename_section(sid: int, body: SectionIn):
    return store.rename_section(sid, body.name)


@router.post("/teammates/sections/{sid}/move")
def move_section(sid: int, delta: int = 1):
    return store.move_section(sid, delta)


@router.delete("/teammates/sections/{sid}")
def delete_section(sid: int):
    return store.delete_section(sid)


# Groups -----------------------------------------------------------------------------------------------------------
@router.post("/groups")
def create_group(body: GroupIn):
    return groups.create(body.name, body.members or [])


@router.get("/groups/{gid}")
def group_messages(gid: int, after: int = 0):
    return groups.messages(gid, after)


@router.patch("/groups/{gid}")
def patch_group(gid: int, body: GroupIn):
    return groups.update(gid, body.name, body.members)


@router.delete("/groups/{gid}")
def delete_group(gid: int):
    return groups.delete(gid)


@router.post("/groups/{gid}/messages")
async def post_group(gid: int, body: TextIn):
    return groups.post(gid, body.content, body.reply_to)


class ReactIn(BaseModel):
    scope: str
    ref: int
    emoji: str
    on: bool = True


@router.post("/reactions")
def react(body: ReactIn):
    return groups.react(body.scope, body.ref, body.emoji, body.on)


# Vault ------------------------------------------------------------------------------------------------------------
class VaultIn(BaseModel):
    kind: str = "login"
    label: str = ""
    values: dict[str, str] | None = None
    teammates: list[int] | None = None


@router.get("/vault")
def vault_list():
    return vault.listing()


@router.post("/vault")
def vault_add(body: VaultIn):
    return vault.create(body.kind, body.label, body.values or {}, body.teammates)


@router.patch("/vault/{vid}")
def vault_edit(vid: int, body: VaultIn):
    return vault.update(vid, body.label or None, body.values, body.teammates)


@router.delete("/vault/{vid}")
def vault_delete(vid: int):
    return vault.delete(vid)


@router.post("/vault/{vid}/reveal")
def vault_reveal(vid: int):
    return vault.reveal(vid)


@router.get("/vault/forms")
def vault_forms(conversation_id: int | None = None):
    return vault.forms(conversation_id)


@router.post("/vault/forms/{fid}")
def vault_fill(fid: int, body: VaultIn):
    r = vault.fill_form(fid, body.values or {}, body.teammates)
    return {"form": r["form"], "item": {k: r["item"][k] for k in ("id", "kind", "label", "secret_hints")}}


@router.post("/vault/forms/{fid}/cancel")
def vault_cancel(fid: int):
    return vault.cancel_form(fid)


# Outbox -----------------------------------------------------------------------------------------------------------
class DraftIn(BaseModel):
    channel: str = "email"
    recipients: str | None = None
    subject: str | None = None
    body: str | None = None


@router.get("/outbox")
def outbox_list(status: str = "draft", conversation_id: int | None = None):
    return outbox.listing(status, conversation_id)


@router.post("/outbox")
def outbox_add(body: DraftIn):
    return outbox.draft(body.channel, body.recipients or "", body.body or "", body.subject or "", created_by="you")


@router.patch("/outbox/{did}")
def outbox_edit(did: int, body: DraftIn):
    return outbox.edit(did, body.recipients, body.subject, body.body)


@router.post("/outbox/{did}/send")
async def outbox_send(did: int):
    return await outbox.send(did, "web")


@router.post("/outbox/{did}/discard")
def outbox_discard(did: int):
    return outbox.discard(did)


# Pages ------------------------------------------------------------------------------------------------------------
class PageIn(BaseModel):
    title: str | None = None
    body: str | None = None
    message_id: int | None = None
    teammate_id: int | None = None


@router.get("/pages")
def pages_list(teammate_id: int | None = None):
    return pages.listing(teammate_id)


@router.post("/pages")
def pages_add(body: PageIn):
    if body.message_id:
        return pages.from_message(body.message_id, body.title or "")
    return pages.create(body.title or "", body.body or "", body.teammate_id)


@router.get("/pages/{pid}")
def pages_get(pid: int):
    return pages.get(pid)


@router.patch("/pages/{pid}")
def pages_edit(pid: int, body: PageIn):
    return pages.update(pid, body.title, body.body)


@router.delete("/pages/{pid}")
def pages_delete(pid: int):
    return pages.delete(pid)


@router.post("/pages/{pid}/share")
def pages_share(pid: int, on: bool = True):
    return pages.share(pid, on)


@router.get("/pages/{pid}/export.html")
def pages_export(pid: int, theme: str = "sunshine"):
    p = pages.get(pid)
    safe = "".join(ch for ch in p["title"] if ch.isalnum() or ch in " -_")[:60].strip() or "page"
    return Response(pages.export_html(pid, theme), media_type="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.html"'})


@public.get("/p/{token}", include_in_schema=False)
def shared_page(token: str, theme: str = "sunshine"):
    p = pages.by_token(token)
    return HTMLResponse(pages.export_html(p["id"], theme), headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})


# Channels ---------------------------------------------------------------------------------------------------------
class SlackIn(BaseModel):
    bot_token: str | None = None
    app_token: str | None = None
    allowed_users: str | None = None
    teammate_id: int | None = None


class TelegramIn(BaseModel):
    bot_token: str | None = None
    allowed_users: str | None = None
    teammate_id: int | None = None


@router.get("/channels")
def channels_status():
    return channels.status()


@router.put("/channels/slack")
def channels_slack(body: SlackIn):
    return channels.configure_slack(body.bot_token, body.app_token, body.allowed_users, body.teammate_id)


@router.post("/channels/slack/test")
async def channels_slack_test():
    return await channels.test_slack()


@router.put("/channels/telegram")
def channels_telegram(body: TelegramIn):
    return channels.configure_telegram(body.bot_token, body.allowed_users, body.teammate_id)


# Phone link -------------------------------------------------------------------------------------------------------
class LinkSettingsIn(BaseModel):
    relay_url: str | None = Field(None, max_length=300)
    shared_folders: list[str] | None = None


@router.get("/link")
def link_status():
    return phonelink.status()


@router.put("/link")
def link_settings(body: LinkSettingsIn):
    if body.relay_url is not None:
        u = body.relay_url.strip().rstrip("/")
        if u and not u.startswith("https://"):
            raise HTTPException(400, "The relay address must start with https://")
        db.set_setting("link_relay_url", u)
    if body.shared_folders is not None:
        phonelink.set_shared_folders(body.shared_folders)
    return phonelink.status()


class HandoffIn(BaseModel):
    conversation_id: int | None = None
    teammate_id: int | None = None
    draft: str = ""
    path: str = ""


@router.post("/link/handoff")
def handoff_to_phone(body: HandoffIn):
    return phonelink.handoff("to_phone", body.conversation_id, body.teammate_id, body.draft, body.path)


@router.get("/link/handoffs")
def handoffs_for_computer():
    return phonelink.pending_handoffs("to_computer")


@router.post("/link/handoffs/{hid}/pick-up")
def pick_up_on_computer(hid: int):
    return phonelink.pick_up(hid)


class KeysIn(BaseModel):
    phone_pub: str = Field(..., max_length=100)


@device.post("/link/keys")
def link_keys(body: KeysIn, d: dict = Depends(require_device)):
    return phonelink.register(d, body.phone_pub)


@device.post("/link/sealed")
async def link_sealed(env: dict[str, Any]):
    """No device token outside the envelope: the sealed request carries it, so a relay never sees it."""
    return await phonelink.dispatch(env)


@device.get("/link/computer")
def link_computer(_: dict = Depends(require_device)):
    return {"computer_id": phonelink.computer_id(), "name": db.get_setting("workspace_name", "") or "My computer",
            "lan": phonelink.lan_urls(), "relay_url": phonelink.relay_url()}


@device.post("/link/handoff")
def handoff_to_computer(body: HandoffIn, _: dict = Depends(require_device)):
    return phonelink.handoff("to_computer", body.conversation_id, body.teammate_id, body.draft, body.path)


@device.get("/link/handoffs")
def handoffs_for_phone(_: dict = Depends(require_device)):
    return phonelink.pending_handoffs("to_phone")


@device.get("/files/browse")
def phone_browse(path: str = "", _: dict = Depends(require_device)):
    return phonelink.browse(path)


@device.get("/files/download")
def phone_download(path: str, _: dict = Depends(require_device)):
    p, data = phonelink.read_file(path)
    return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{p.name}"'})


@device.get("/teammates")
def phone_teammates(_: dict = Depends(require_device)):
    return {"teammates": store.listing()["teammates"], "groups": groups.listing(), "drafts": outbox.listing("draft")}


@device.post("/outbox/{did}/send")
async def phone_send(did: int, _: dict = Depends(require_device)):
    """Sending from the phone: the app asks for Face ID / fingerprint first (mobile), then calls this."""
    return await outbox.send(did, "mobile")


def install(app) -> None:
    from . import hooks

    app.include_router(router)
    app.include_router(device)
    app.include_router(public)
    hooks.register_tools()


def background():
    return [("Slack", channels.slack_loop), ("Phone relay", phonelink.relay_loop)]
