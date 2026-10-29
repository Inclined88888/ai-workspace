"""Model calls for the rest of Workbench: connections, model lists, and chat/complete/stream recorded in the run log.

Every call goes through the adapter layer (adapters.py, R1), which knows the backends; this module never branches on
which one is active. Model references are "<connection_id>/<model>", e.g. "1/qwen3:4b" or "3/openai/gpt-4o".
"""

import json
import time
from typing import AsyncIterator

from fastapi import HTTPException

from . import config, db
from .adapters import (  # noqa: F401 - re-exported for callers and tests
    OLLAMA_NUM_CTX, THINK_RE, Adapter, keep_model, parse_text_tool_calls, validate,
)
from .security import encrypt

# Presets offered in Settings. Each names the auth kind and protocol it uses; the base URL is editable.
PRESETS = {
    # Native Ollama app on the host (fast on Mac: uses the GPU) unless the ollama add-on container is configured
    "ollama": {"name": "Ollama (local)", "auth_kind": "local_ollama", "protocol": "ollama_native",
               "base_url": config.OLLAMA_URL or "http://host.docker.internal:11434"},
    "openai": {"name": "OpenAI", "auth_kind": "api_key", "protocol": "responses", "base_url": "https://api.openai.com/v1"},
    "anthropic": {"name": "Anthropic", "auth_kind": "api_key", "protocol": "anthropic_messages", "base_url": "https://api.anthropic.com/v1",
                  "note": "Claude API key from console.anthropic.com. Claude Pro and Max subscriptions cannot be used by "
                          "third-party apps (Anthropic's terms), so a key is the supported way."},
    "openrouter": {"name": "OpenRouter", "auth_kind": "api_key", "protocol": "chat_completions", "base_url": "https://openrouter.ai/api/v1",
                   "note": "Free account key. Models ending in :free cost nothing (20 requests/min, 50/day; 1000/day once you have bought $10 of credits)."},
    "gemini": {"name": "Google Gemini", "auth_kind": "api_key", "protocol": "chat_completions",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "note": "Free tier with a key from aistudio.google.com (Flash and Flash-Lite models). Google may use free tier prompts to improve its products."},
    # OpenCode's own free models are refused outside the OpenCode app ("Free models work in OpenCode"),
    # so only the paid Zen and the Go subscription are offered here. Free OpenCode models: the paperclip add-on.
    "opencode-go": {"name": "OpenCode Go", "auth_kind": "api_key", "protocol": "chat_completions",
                    "base_url": "https://opencode.ai/inference/go/openai/v1", "note": "OpenCode Go subscription ($10/month flat). Key from opencode.ai/console."},
    "opencode-zen": {"name": "OpenCode Zen", "auth_kind": "api_key", "protocol": "chat_completions",
                     "base_url": "https://opencode.ai/inference/openai/v1", "note": "Pay as you go. Key from opencode.ai/console."},
    "compatible": {"name": "OpenAI compatible server", "auth_kind": "openai_compatible", "protocol": "chat_completions",
                   "base_url": "http://host.docker.internal:1234/v1", "note": "LM Studio, vLLM, llama.cpp server, LiteLLM, Groq, Together. Key optional."},
}
SEED = {"openai": "openai", "anthropic": "anthropic", "openrouter": "openrouter", "gemini": "gemini"}  # .env key -> preset


def seed_connections() -> None:
    """First start only: connections for a host Ollama and for provider keys given in .env."""
    if db.one("SELECT id FROM connections LIMIT 1"):
        return
    seeds = [("ollama", "")] if config.OLLAMA_URL else []
    seeds += [(SEED[k], key) for k, key in config.SEED_KEYS.items() if key]
    for preset, key in seeds:
        p = PRESETS[preset]
        db.run("INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, residency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
               p["name"], p["auth_kind"], p["protocol"], p["base_url"], encrypt(key), "local" if p["auth_kind"] == "local_ollama" else "cloud",
               db.now())


def _connection(cid: int) -> dict:
    c = db.one("SELECT * FROM connections WHERE id = ? AND enabled = 1", cid)
    if not c:
        raise HTTPException(404, f"Connection {cid} not found or disabled")
    return c


def split_ref(ref: str) -> tuple[dict, str]:
    cid, _, model = ref.partition("/")
    if not cid.isdigit() or not model:
        raise HTTPException(400, f"Invalid model reference '{ref}'")
    return _connection(int(cid)), model


def adapter(ref: str | None) -> tuple[Adapter, dict, str, str]:
    """(adapter, connection, model, full ref) for a model reference, or the default model."""
    ref = ref or default_model_ref()
    conn, model = split_ref(ref)
    return Adapter(conn), conn, model, ref


def default_model_ref() -> str:
    saved = db.get_setting("default_model")
    if saved:
        return saved
    local = db.one("SELECT id FROM connections WHERE auth_kind = 'local_ollama' AND enabled = 1 ORDER BY id LIMIT 1")
    if local:
        return f"{local['id']}/{config.DEFAULT_MODEL}"
    conns = db.all("SELECT id, base_url FROM connections WHERE enabled = 1 ORDER BY id")
    if not conns:
        raise HTTPException(400, "No model provider configured. Add an API key in Settings.")
    # Sensible current defaults when the provider lists them; otherwise the first model it lists
    preferred = {"api.anthropic.com": "claude-sonnet-5-5", "openrouter.ai": "anthropic/claude-sonnet-5.5",
                 "generativelanguage.googleapis.com": "gemini-3.8-flash"}
    for c in conns:
        want = next((m for host, m in preferred.items() if host in c["base_url"]), None)
        if want and any(m["model"] == want for m in list_models_for(c["id"])):
            return f"{c['id']}/{want}"
    for c in conns:
        models = list_models_for(c["id"])
        if models:
            return models[0]["ref"]
    raise HTTPException(400, "No models available. Pick a default model in Settings.")


_model_cache: dict[int, tuple[float, list[dict]]] = {}


def list_models_for(cid: int, fresh: bool = False) -> list[dict]:
    conn = _connection(cid)
    cached = _model_cache.get(cid)
    if cached and not fresh and cached[0] > time.time() - 120:
        return cached[1]
    a = Adapter(conn)
    out = [{"ref": f"{cid}/{n}", "model": n, "provider": conn["name"], "provider_id": cid, "free": a.is_free(n)}
           for n in a.list_models()]
    _model_cache[cid] = (time.time(), out)
    return out


def list_models(fresh: bool = False) -> list[dict]:
    """Every usable model. A connection that cannot be used (refused, needs sign-in) is skipped, never fatal (R4)."""
    out: list[dict] = []
    for c in db.all("SELECT id FROM connections WHERE enabled = 1 ORDER BY id"):
        try:
            out.extend(list_models_for(c["id"], fresh))
        except HTTPException as e:
            db.run("UPDATE connections SET last_error = ? WHERE id = ?", str(e.detail)[:300], c["id"])
    return out


def is_free(conn: dict, model: str) -> bool:
    return Adapter(conn).is_free(model)


def context_chars(ref: str | None) -> int:
    """Characters of prompt this model can take, keeping a quarter of the window for the reply."""
    try:
        return adapter(ref)[0].context_chars()
    except HTTPException:
        return 300_000


def _reported_cost(usage: dict) -> float | None:
    return usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None


def _record(conn: dict, ref: str, trace: dict | None, started: float, usage: dict, outcome: str, error: str = "") -> tuple[int, float | None]:
    """One model_call row per call, success or failure (R2). Returns (action id, USD cost or None if unpriced)."""
    from . import actions
    from .usage import price_usd

    pt, ct = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    reported = _reported_cost(usage)
    usd = reported if reported is not None else price_usd(ref, pt, ct)
    aid = actions.add("model_call", outcome, trace=trace, provider=conn["name"], model=ref, auth_kind=conn["auth_kind"],
                      protocol=conn["protocol"], latency_ms=int((time.monotonic() - started) * 1000), tokens_in=pt, tokens_out=ct,
                      usd_cost=usd, failure_reason=error)
    return aid, usd


async def chat(ref: str | None, messages: list[dict], tools: list[dict] | None = None, trace: dict | None = None,
               schema: dict | None = None) -> dict:
    """One non-streaming turn with optional tool calling, recorded as one model_call action.

    Returns {"message": {...}, "usage": {...}, "ref": str, "model": str, "usd": float | None, "action_id": int}.
    """
    a, conn, model, ref = adapter(ref)
    started = time.monotonic()
    try:
        res = await a.chat(model, messages, tools, schema)
    except HTTPException as e:
        _record(conn, ref, trace, started, {}, "error", str(e.detail))
        raise
    res.update(ref=ref, model=model)
    res["action_id"], res["usd"] = _record(conn, ref, trace, started, res["usage"], "ok")
    return res


async def complete(ref: str | None, messages: list[dict], trace: dict | None = None) -> str:
    return (await chat(ref, messages, None, trace))["message"]["content"]


async def stream(ref: str | None, messages: list[dict], trace: dict | None = None) -> AsyncIterator[dict]:
    """Yields {"reasoning"} and {"delta"} chunks, then {"done": True, "usage", "ms", "first_token_ms"} or {"error"}."""
    a, conn, model, ref = adapter(ref)
    started = time.monotonic()
    first_token_ms = None
    recorded = False
    try:
        async for e in a.stream(model, messages):
            if "delta" in e and first_token_ms is None:
                first_token_ms = int((time.monotonic() - started) * 1000)
            if "done" in e or "error" in e:
                recorded = True
                _record(conn, ref, trace, started, e.get("usage") or {}, "ok" if "done" in e else "error", e.get("error", ""))
                if "done" in e:
                    e = {**e, "ms": int((time.monotonic() - started) * 1000), "first_token_ms": first_token_ms}
            yield e
    finally:
        if not recorded:  # the reader went away (closed tab) or the transport failed mid stream
            _record(conn, ref, trace, started, {}, "error", "stream ended before the model finished")


def parse_json(text: str):
    """Extract the first JSON object or array from model output."""
    text = THINK_RE.sub("", text)
    pairs = sorted((("[", "]"), ("{", "}")), key=lambda p: text.find(p[0]) if p[0] in text else len(text))
    for opener, closer in pairs:
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                continue
    return None
