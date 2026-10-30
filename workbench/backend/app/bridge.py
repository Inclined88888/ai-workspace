"""OpenAI-shaped endpoints over the adapter layer (core of item 103): /v1/chat/completions and /v1/models.

Tools that only speak the OpenAI API (the browser agent's browser-use library) reach every Workbench connection through
here, so their model calls use the one adapter layer (R1), never hold provider keys, and land in the run log (R2,
channel "browser"). Model ids are Workbench model refs ("3/claude-…") or "default".

Auth: Bearer BROWSER_API_KEY, the secret the browser service already shares with Workbench. Refused when it is not set.
Streaming is not offered: no caller needs it yet.
"""

import hmac
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import config, llm


def require_bridge_key(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not (config.BROWSER_API_KEY and token and hmac.compare_digest(token, config.BROWSER_API_KEY)):
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
    channel = channel if channel in ("browser", "rooks-code", "rooks-browser") else "browser"
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
