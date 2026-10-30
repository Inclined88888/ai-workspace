"""OpenAI-shaped endpoints over the adapter layer (core of item 103): /v1/chat/completions and /v1/models.

Tools that only speak the OpenAI API (the browser agent's browser-use library) reach every Workbench connection through
here, so their model calls use the one adapter layer (R1), never hold provider keys, and land in the run log (R2,
channel "browser"). Model ids are Workbench model refs ("3/claude-…") or "default".

Auth: Bearer BROWSER_API_KEY, the secret the browser service already shares with Workbench (or DESK_API_KEY, Rooks
Desk's). Refused when neither is set.

/v1/desk/send_email lets Rooks Desk (computer use) email a result, with attachments, from the user's own mail account.
Rooks Desk asks the user to approve every email before it calls this; the endpoint only answers the rooks-desk channel.
Streaming is not offered: no caller needs it yet.
"""

import asyncio
import base64
import binascii
import hmac
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import config, db, llm

CHANNELS = ("browser", "rooks-code", "rooks-browser", "rooks-desk")
MAX_ATTACHMENTS_BYTES = 20 * 1024 * 1024


def require_bridge_key(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    keys = [k for k in (config.BROWSER_API_KEY, config.DESK_API_KEY) if k]
    if not (token and any(hmac.compare_digest(token, k) for k in keys)):
        raise HTTPException(401, "Invalid or missing bridge key")


router = APIRouter(prefix="/v1", dependencies=[Depends(require_bridge_key)])


class CompletionIn(BaseModel):
    model: str = Field(..., max_length=300)
    messages: list[dict] = Field(..., min_length=1)
    tools: list[dict] | None = None
    response_format: dict | None = None
    stream: bool = False
    # temperature, top_p, max_completion_tokens, seed, ... are accepted and left to each provider's defaults

    model_config = {"extra": "allow"}


def _schema(response_format: dict | None) -> dict | None:
    if not response_format or response_format.get("type") != "json_schema":
        return None
    js = response_format.get("json_schema") or {}
    if not isinstance(js.get("schema"), dict):
        raise HTTPException(400, "response_format.json_schema.schema must be an object")
    return {"name": js.get("name") or "response", "schema": js["schema"]}


@router.post("/chat/completions")
async def chat_completions(data: CompletionIn, request: Request):
    if data.stream:
        raise HTTPException(400, "Streaming is not supported on this endpoint")
    ref = None if data.model in ("default", "") else data.model
    # Rooks Code sends X-Workbench-Channel: rooks-code; the router picks a strong model for both clients either way
    channel = request.headers.get("x-workbench-channel", "browser")
    channel = channel if channel in CHANNELS else "browser"
    if ref is None and channel == "rooks-code":
        ref = llm.local_coder_ref()
    res = await llm.chat(ref, data.messages, data.tools, trace={"channel": channel}, schema=_schema(data.response_format))
    msg = res["message"]
    out = {"role": "assistant", "content": msg.get("content") or None}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    u = res["usage"]
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:24]}", "object": "chat.completion", "created": int(time.time()), "model": res["ref"],
            "choices": [{"index": 0, "message": out, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0),
                      "total_tokens": u.get("prompt_tokens", 0) + u.get("completion_tokens", 0)},
            **({"router": res["router"]} if res.get("router") else {})}


@router.get("/models")
def models():
    return {"object": "list", "data": [{"id": m["ref"], "object": "model", "owned_by": m["provider"]} for m in llm.list_models()]}


class AttachmentIn(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field("application/octet-stream", max_length=120)
    content_b64: str


class DeskEmailIn(BaseModel):
    to: str = Field(..., min_length=3, max_length=2000)
    cc: str = Field("", max_length=2000)
    subject: str = Field("", max_length=500)
    body: str = Field("", max_length=500_000)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=20)
    account_id: int | None = None


@router.post("/desk/send_email")
async def desk_send_email(data: DeskEmailIn, request: Request):
    """Send an email (with files) from the user's connected mail account, for Rooks Desk. The user approved it there."""
    if request.headers.get("x-workbench-channel") != "rooks-desk":
        raise HTTPException(403, "Only Rooks Desk sends email through the bridge")
    from . import mail

    acct = (db.one("SELECT * FROM mail_accounts WHERE id = ?", data.account_id) if data.account_id
            else db.one("SELECT * FROM mail_accounts ORDER BY id LIMIT 1"))
    if not acct:
        raise HTTPException(409, "No mail account is connected. Add one on the Mail page first.")
    files, total = [], 0
    for a in data.attachments:
        try:
            raw = base64.b64decode(a.content_b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(400, f"{a.filename}: not valid base64") from e
        total += len(raw)
        if total > MAX_ATTACHMENTS_BYTES:
            raise HTTPException(413, "Attachments are over 20 MB together")
        files.append((a.filename.replace("/", "_").replace("\\", "_"), a.content_type, raw))
    mid = await asyncio.to_thread(mail.send, acct, data.to, data.subject, data.body, "", cc=data.cc, attachments=files)
    return {"ok": True, "message_id": mid, "from": acct["email"], "attachments": [f[0] for f in files], "bytes": total}
