"""Optional engine for Anthropic connections: Mozilla's any-llm (any-llm-sdk, Apache-2.0) calls Anthropic's native
Messages API instead of its OpenAI compatibility endpoint, and hands back OpenAI-shaped results.

Why only here: every other provider ROOKS supports already speaks the OpenAI chat format natively, so any-llm would
add a layer (and about 90 MB of SDKs) without improving anything. Anthropic's compatibility endpoint is the one place
ROOKS talks to a provider through a translation layer; the native API is what Anthropic documents in full.

It is off unless you install the package (pip install any-llm-sdk==1.40.2, not in requirements.txt) and choose it
for an Anthropic connection. Existing connections keep the compatibility endpoint until you switch them. Requests go
through llm._chat_once and llm.stream exactly as before, so AI modes, spending limits and output caps apply.
"""

import importlib.metadata
import json

VERSION_TESTED = "1.40.2"


def available() -> str:
    """The installed any-llm version, or '' when it is not installed."""
    try:
        import any_llm  # noqa: F401
    except ImportError:
        return ""
    try:
        return importlib.metadata.version("any-llm-sdk")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _base(p: dict) -> str:
    """The Anthropic SDK adds /v1/messages itself."""
    b = p["base_url"].rstrip("/")
    return b[:-3] if b.endswith("/v1") else b


def _kwargs(p: dict, body: dict) -> dict:
    from .llm import _key

    kw = {"provider": "anthropic", "model": body["model"], "messages": body["messages"], "api_key": _key(p), "api_base": _base(p),
          "max_tokens": body.get("max_tokens") or 8192}
    if body.get("tools"):
        kw["tools"] = body["tools"]
    return kw


def _message(m) -> dict:
    out = {"role": "assistant", "content": m.content or ""}
    if getattr(m, "tool_calls", None):
        out["tool_calls"] = [{"id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments or "{}"}}
                             for t in m.tool_calls]
    return out


def _usage(u) -> dict:
    return {"prompt_tokens": u.prompt_tokens or 0, "completion_tokens": u.completion_tokens or 0} if u else {}


async def call(p: dict, body: dict) -> dict:
    """One request, returned in the OpenAI response shape llm._call returns."""
    from any_llm import acompletion

    resp = await acompletion(**_kwargs(p, body))
    return {"choices": [{"message": _message(resp.choices[0].message)}], "usage": _usage(resp.usage), "model": resp.model}


async def stream(p: dict, body: dict):
    """Yields OpenAI-shaped stream chunks as dicts ({"choices": [{"delta": {"content": ...}}]}, then usage)."""
    from any_llm import acompletion

    async for c in await acompletion(**_kwargs(p, body), stream=True):
        if c.choices and c.choices[0].delta and c.choices[0].delta.content:
            yield {"choices": [{"delta": {"content": c.choices[0].delta.content}}]}
        if getattr(c, "usage", None):
            yield {"choices": [], "usage": _usage(c.usage)}


def error_text(e: Exception) -> tuple[int, str]:
    """(HTTP status, detail) from an any-llm or Anthropic SDK error, for llm.explain."""
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None) or 0
    body = getattr(e, "body", None)
    detail = json.dumps(body)[:400] if isinstance(body, (dict, list)) else str(e)[:400]
    return int(status or 0), detail
