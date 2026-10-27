"""Telegram gateway: chat with the same assistant (same memory and skills) from Telegram.

Long polling, so no public URL or open port is needed. Only allowlisted Telegram user ids get answers; everyone
else is told the bot is private. Scheduled tasks with deliver=telegram go to the home chat (your private chat).
"""

import asyncio
import logging

import httpx

from . import assistant, db
from .security import decrypt, encrypt

log = logging.getLogger("telegram")
API = "https://api.telegram.org"
_offset = 0
_status = {"running": False, "bot": "", "error": ""}


def token() -> str:
    return decrypt(db.get_setting("secret:TELEGRAM_BOT_TOKEN", "") or "")


def allowed() -> set[int]:
    raw = db.get_setting("telegram_allowed_users", "") or ""
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


def configure(bot_token: str | None, allowed_users: str | None) -> None:
    if bot_token is not None:
        db.set_setting("secret:TELEGRAM_BOT_TOKEN", encrypt(bot_token.strip()) if bot_token.strip() else "")
    if allowed_users is not None:
        ids = ",".join(str(i) for i in sorted({int(x) for x in allowed_users.replace(" ", "").split(",") if x.strip().isdigit()}))
        db.set_setting("telegram_allowed_users", ids)
        if ids:
            db.set_setting("telegram_home_chat", ids.split(",")[0])


def status() -> dict:
    return {**_status, "configured": bool(token()), "allowed_users": db.get_setting("telegram_allowed_users", "") or ""}


async def _api(client: httpx.AsyncClient, method: str, **params):
    r = await client.post(f"{API}/bot{token()}/{method}", json=params)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", f"Telegram {method} failed"))
    return data["result"]


async def send(client: httpx.AsyncClient, chat_id: int, text: str) -> None:
    text = text or "(empty reply)"
    for i in range(0, len(text), 4000):  # Telegram caps messages at 4096 characters
        await _api(client, "sendMessage", chat_id=chat_id, text=text[i : i + 4000])


def notify(text: str) -> None:
    """Fire and forget delivery to the home chat (used by schedules and the company)."""
    chat = db.get_setting("telegram_home_chat", "")
    if not (token() and chat):
        return

    async def go():
        async with httpx.AsyncClient(timeout=30) as c:
            await send(c, int(chat), text)

    try:
        asyncio.get_running_loop().create_task(go())
    except RuntimeError:
        pass


def _conversation(chat_id: int, fresh: bool = False) -> int:
    if not fresh:
        c = db.one("SELECT id FROM conversations WHERE channel = 'telegram' AND external_id = ? ORDER BY id DESC LIMIT 1", str(chat_id))
        if c:
            return c["id"]
    return assistant.new_conversation("telegram", "Telegram chat", str(chat_id))


async def _handle(client: httpx.AsyncClient, msg: dict) -> None:
    chat_id, user_id = msg["chat"]["id"], (msg.get("from") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if user_id not in allowed():
        await send(client, chat_id, f"This assistant is private. Your Telegram user id is {user_id}.")
        return
    if not text:
        await send(client, chat_id, "I can read text messages for now.")
        return
    if text in ("/start", "/help"):
        await send(client, chat_id, "Talk to me normally.\n/new starts a fresh conversation (memory and skills carry over)\n"
                                    "/model shows or switches the model, e.g. /model gemini-3.8-flash or /model auto\n/status shows which model is answering")
        return
    if text == "/status" or text.split()[0] == "/model":
        await send(client, chat_id, await asyncio.to_thread(model_command, chat_id, text))
        return
    if text == "/new":
        _conversation(chat_id, fresh=True)
        await send(client, chat_id, "New conversation started.")
        return
    await _api(client, "sendChatAction", chat_id=chat_id, action="typing")
    ctx = assistant.Ctx(channel="telegram", conversation_id=_conversation(chat_id))
    reply, _ = await assistant.ask(ctx, text)
    await send(client, chat_id, reply)


def model_command(chat_id: int, text: str) -> str:
    """/status and /model [name|auto] for this Telegram chat (Hermes and OpenClaw offer the same in chat)."""
    from . import llm

    cid = _conversation(chat_id)
    conv = db.one("SELECT model FROM conversations WHERE id = ?", cid) or {}
    try:
        default, source = llm.effective_default()
    except Exception as e:  # noqa: BLE001 - report configuration problems in chat
        default, source = "", str(getattr(e, "detail", e))
    arg = text.partition(" ")[2].strip()
    if text == "/status" or not arg:
        current = conv.get("model") or (f"{default} (automatic)" if source == "auto" else default) or f"none: {source}"
        backups = ", ".join(llm.fallback_refs(default)) if default and not conv.get("model") else "none (a model picked for this chat is used strictly)"
        return f"Model: {current}\nBackups: {backups or 'none'}\nSwitch with /model <name>, back to the default with /model auto."
    if arg == "auto":
        db.run("UPDATE conversations SET model = '' WHERE id = ?", cid)
        return f"Back to the default model ({default or 'none connected'}), with automatic backups."
    models = llm.list_models()
    exact = [m for m in models if arg in (m["ref"], m["model"])]
    loose = exact or [m for m in models if arg.lower() in m["model"].lower()]
    if len(loose) != 1:
        hint = ", ".join(m["model"] for m in loose[:8])
        return f"{len(loose)} models match '{arg}'." + (f" For example: {hint}" if hint else " Check the name in ROOKS Settings, AI models.")
    db.run("UPDATE conversations SET model = ? WHERE id = ?", loose[0]["ref"], cid)
    return f"This chat now uses {loose[0]['model']} ({loose[0]['provider']}). /model auto switches back."


async def loop() -> None:
    global _offset
    while True:
        if not token():
            _status.update(running=False, bot="", error="")
            await asyncio.sleep(10)
            continue
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
                me = await _api(client, "getMe")
                _status.update(running=True, bot=me.get("username", ""), error="")
                while token():
                    updates = await _api(client, "getUpdates", offset=_offset, timeout=30, allowed_updates=["message"])
                    for u in updates:
                        _offset = u["update_id"] + 1
                        if u.get("message"):
                            try:
                                await _handle(client, u["message"])
                            except Exception as e:  # noqa: BLE001 - one bad message must not stop the bot
                                log.warning("telegram message failed: %s", e)
        except Exception as e:  # noqa: BLE001
            _status.update(running=False, error=str(e)[:300])
            await asyncio.sleep(15)
