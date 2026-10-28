"""Capability tests for one model, each run separately and recorded separately.

A provider that accepts the key (and even answers a ping) proves only that much. Chat, tool calling, a full tool round
trip, streaming, strict JSON, images and embeddings are each a different request here, with a pass or fail, the time
it took, and what the model actually did. Nothing is inferred from another capability or from the provider's model
list.

Every test is sent through the same guard as all model calls (llm._chat_once or llm.stream): Free Cloud AI and Local
AI refuse models they do not allow, and the monthly limit applies. Output is capped as in connection checks. A test
that could be charged runs only when you confirm it (allow_paid); nothing runs in the background.
"""

import json
import time

import httpx
from fastapi import HTTPException

from . import db, llm, usage

CAPS = {
    "chat": "Answers a plain message",
    "tools": "Calls a tool with the right arguments",
    "tool_roundtrip": "Uses a tool's result in its answer",
    "streaming": "Streams its answer",
    "json": "Replies in exact JSON when asked",
    "vision": "Reads a simple test image",
    "embeddings": "Returns embeddings",
}
TOOL = {"type": "function", "function": {"name": "lookup_code", "description": "Look up the access code for a room.",
                                         "parameters": {"type": "object", "properties": {"room": {"type": "string"}}, "required": ["room"]}}}


def results(ref: str) -> dict:
    try:
        return json.loads(db.get_setting(f"cap_check:{ref}", "") or "{}")
    except ValueError:
        return {}


def _save(ref: str, cap: str, out: dict) -> None:
    cur = results(ref)
    cur[cap] = out
    db.set_setting(f"cap_check:{ref}", json.dumps(cur))


def may_charge(ref: str) -> bool:
    """True when a test request to this model could be billed (not local, not verified as free)."""
    try:
        p, model = llm.split_ref(ref)
    except HTTPException:
        return False
    return not (llm.is_local(ref) or llm.is_free(p, model) or llm.confirmed_free(p))


async def _chat(ref: str, cap: str) -> tuple[bool, str]:
    if cap == "chat":
        res = await llm._chat_once(ref, [{"role": "user", "content": "Reply with only the word READY."}], None, ping=True)
        usage.record(res, channel="check")
        text = (res["message"].get("content") or "").strip()
        return "ready" in text.lower(), f"replied {text[:60]!r}"
    if cap in ("tools", "tool_roundtrip"):
        msgs = [{"role": "system", "content": "Use the lookup_code tool to answer."}, {"role": "user", "content": "What is the access code for room B12?"}]
        res = await llm._chat_once(ref, msgs, [TOOL], ping=True)
        usage.record(res, channel="check")
        calls = res["message"].get("tool_calls") or []
        call = next((c for c in calls if c.get("function", {}).get("name") == "lookup_code"), None)
        if not call:
            return False, f"did not call the tool; replied {str(res['message'].get('content'))[:60]!r}"
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except ValueError:
            return False, "called the tool with arguments that are not JSON"
        if "b12" not in str(args.get("room", "")).lower():
            return False, f"called the tool with the wrong arguments {json.dumps(args)[:80]}"
        if cap == "tools":
            return True, "called lookup_code with room B12"
        msgs += [{"role": "assistant", "content": res["message"].get("content") or "", "tool_calls": [call]},
                 {"role": "tool", "tool_call_id": call.get("id", "c1"), "content": json.dumps({"room": "B12", "code": "4417"})}]
        res = await llm._chat_once(ref, msgs, [TOOL], ping=True)
        usage.record(res, channel="check")
        text = res["message"].get("content") or ""
        return "4417" in text, f"after the tool result it replied {text[:60]!r}"
    raise ValueError(cap)


async def _stream(ref: str) -> tuple[bool, str]:
    chunks, text, err = 0, "", ""
    async for c in llm.stream(ref, [{"role": "user", "content": "Count from 1 to 5, separated by spaces."}], cap=True):
        if c.get("error"):
            err = c["error"]
        elif c.get("delta"):
            chunks += 1
            text += c["delta"]
        elif c.get("done"):
            usage.record({"model": llm.split_ref(ref)[1], "usage": c.get("usage") or {}, "cost_usd": (c.get("usage") or {}).get("cost")}, channel="check")
    if err:
        raise HTTPException(502, err)
    # One chunk means the answer arrived whole: the endpoint accepted stream=true but did not stream
    return chunks >= 2 and "3" in text, f"{chunks} chunk(s): {text[:40]!r}"


def _embed(ref: str) -> tuple[bool, str]:
    from .memory import embed

    llm.spend_guard()
    try:
        vecs = embed(["a test sentence"], ref)
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"The embeddings endpoint answered {e.response.status_code}.") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, str(e)[:200] or "The embeddings endpoint could not be reached.") from e
    n = len(vecs[0]) if vecs and isinstance(vecs[0], list) else 0
    return n > 0, f"{n} dimensions"


async def run(ref: str, caps: list[str], allow_paid: bool = False) -> dict:
    llm.split_ref(ref)
    unknown = [c for c in caps if c not in CAPS]
    if unknown:
        raise HTTPException(400, f"Unknown capability {unknown[0]}. Known: {', '.join(CAPS)}")
    if (why := llm.mode_allows(ref)):
        raise HTTPException(400, f"Not tested: {why}")
    if may_charge(ref) and not allow_paid:
        raise HTTPException(402, f"Testing {ref} may be charged by its provider (a few tokens per test, output capped). Confirm to run it.")
    out = {}
    for cap in caps:
        started = time.monotonic()
        rec = {"ok": False, "at": db.now(), "ms": 0, "detail": ""}
        try:
            if cap == "streaming":
                ok, detail = await _stream(ref)
            elif cap == "json":
                from .roles import json_check

                j = await json_check(ref)
                ok, detail = j["ok"], j.get("error") or "returned the exact JSON asked for"
            elif cap == "vision":
                from .vision import check

                v = await check(ref)
                ok, detail = v["ok"], v.get("error") or "named the colour in the test image"
            elif cap == "embeddings":
                ok, detail = _embed(ref)
            else:
                ok, detail = await _chat(ref, cap)
            rec.update(ok=bool(ok), detail=detail)
        except HTTPException as e:
            rec["detail"] = str(e.detail)[:300]
        rec["ms"] = int((time.monotonic() - started) * 1000)
        _save(ref, cap, rec)
        out[cap] = rec
    return {"ref": ref, "results": out, "all": results(ref)}
