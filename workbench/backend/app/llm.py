"""Model calls for the rest of Workbench: connections, model lists, and chat/complete/stream recorded in the run log.

Every call goes through the adapter layer (adapters.py, R1), which knows the backends; this module never branches on
which one is active. Model references are "<connection_id>/<model>", e.g. "1/qwen3:4b" or "3/openai/gpt-4o".
"""

import json
import re
import time
from typing import AsyncIterator

from fastapi import HTTPException

from . import config, db
from .adapters import (  # noqa: F401 - re-exported for callers and tests
    OLLAMA_NUM_CTX, THINK_RE, Adapter, keep_model, parse_text_tool_calls, validate,
)
from .security import encrypt, store_connection_secret  # noqa: F401 - encrypt kept for callers

# Presets offered in Settings. Each names the auth kind and protocol it uses; the base URL is editable.
PRESETS = {
    # Native Ollama app on the host (fast on Mac: uses the GPU) unless the ollama add-on container is configured
    "ollama": {"name": "Ollama (local)", "auth_kind": "local_ollama", "protocol": "ollama_native",
               "base_url": config.OLLAMA_URL or ("http://127.0.0.1:11434" if config.DESKTOP else "http://host.docker.internal:11434")},
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
    "groq": {"name": "Groq", "auth_kind": "api_key", "protocol": "chat_completions", "base_url": "https://api.groq.com/openai/v1",
             "note": "Free key from console.groq.com, no card. gpt-oss-120b and -20b: 30 requests/min, 1,000 a day."},
    "xai": {"name": "xAI Grok", "auth_kind": "api_key", "protocol": "chat_completions", "base_url": "https://api.x.ai/v1",
            "note": "API key from console.x.ai. SuperGrok sign-in is only offered to xAI's partner apps for now."},
    "mistral": {"name": "Mistral", "auth_kind": "api_key", "protocol": "chat_completions", "base_url": "https://api.mistral.ai/v1",
                "note": "Key from console.mistral.ai (the free Experiment plan works, with low limits)."},
    "deepseek": {"name": "DeepSeek", "auth_kind": "api_key", "protocol": "chat_completions", "base_url": "https://api.deepseek.com",
                 "note": "Pay as you go key from platform.deepseek.com."},
    "opencode-go": {"name": "OpenCode Go", "auth_kind": "api_key", "protocol": "chat_completions",
                    "base_url": "https://opencode.ai/inference/go/openai/v1", "note": "OpenCode Go subscription ($10/month flat). Key from opencode.ai/console."},
    "opencode-zen": {"name": "OpenCode Zen", "auth_kind": "api_key", "protocol": "chat_completions",
                     "base_url": "https://opencode.ai/inference/openai/v1", "note": "Pay as you go. Key from opencode.ai/console."},
    "compatible": {"name": "OpenAI compatible server", "auth_kind": "openai_compatible", "protocol": "chat_completions",
                   "base_url": "http://127.0.0.1:1234/v1" if config.DESKTOP else "http://host.docker.internal:1234/v1", "note": "LM Studio, vLLM, llama.cpp server, LiteLLM, Groq, Together. Key optional."},
}
SEED = {"openai": "openai", "anthropic": "anthropic", "openrouter": "openrouter", "gemini": "gemini", "groq": "groq", "xai": "xai"}  # .env key -> preset

# Shown by the first-run screen and Models: where to get a key, what the free tier gives, and how a key looks.
KEY_INFO = {
    "gemini": {"url": "https://aistudio.google.com/app/apikey", "prefix": "AIza", "free": True,
               "limits": "Free: Flash-Lite and Flash, daily limits per project (see AI Studio). Resets midnight Pacific."},
    "groq": {"url": "https://console.groq.com/keys", "prefix": "gsk_", "free": True,
             "limits": "Free: 30 requests/min and 1,000 a day on gpt-oss-120b. Resets daily."},
    "openrouter": {"url": "https://openrouter.ai/settings/keys", "prefix": "sk-or-", "free": True,
                   "limits": "Free models: 20/min and 50 a day (1,000 a day after $10 of credits)."},
    "openai": {"url": "https://platform.openai.com/api-keys", "prefix": "sk-"},
    "anthropic": {"url": "https://console.anthropic.com/settings/keys", "prefix": "sk-ant-"},
    "xai": {"url": "https://console.x.ai", "prefix": "xai-"},
    "mistral": {"url": "https://console.mistral.ai/api-keys", "prefix": ""},
    "deepseek": {"url": "https://platform.deepseek.com/api_keys", "prefix": "sk-"},
}


def detect_key(key: str) -> str | None:
    """Which preset a pasted key belongs to, from its prefix. Ambiguous "sk-" keys are guessed as OpenAI."""
    k = key.strip()
    for preset in ("openrouter", "anthropic", "gemini", "groq", "xai"):
        if k.startswith(KEY_INFO[preset]["prefix"]):
            return preset
    return "openai" if k.startswith("sk-") else None


def seed_connections() -> None:
    """First start only: connections for a host Ollama and for provider keys given in .env."""
    if db.one("SELECT id FROM connections LIMIT 1"):
        return
    # The desktop app always knows where a native Ollama would be; its welcome flow adds it only when the user picks it.
    seeds = [("ollama", "")] if config.OLLAMA_URL and not config.DESKTOP else []
    seeds += [(SEED[k], key) for k, key in config.SEED_KEYS.items() if key]
    for preset, key in seeds:
        p = PRESETS[preset]
        cid = db.run("INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, residency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     p["name"], p["auth_kind"], p["protocol"], p["base_url"], "", "local" if p["auth_kind"] == "local_ollama" else "cloud",
                     db.now())
        store_connection_secret(cid, key)


def _connection(cid: int) -> dict:
    c = db.one("SELECT * FROM connections WHERE id = ? AND enabled = 1", cid)
    if not c:
        raise HTTPException(404, f"Connection {cid} not found or disabled")
    return c


def split_ref(ref: str) -> tuple[dict, str]:
    from . import router

    if router.is_auto(ref):
        raise HTTPException(400, "The smart router picks a model per call; resolve it with router.candidates")
    cid, _, model = ref.partition("/")
    if not cid.isdigit() or not model:
        raise HTTPException(400, f"Invalid model reference '{ref}'")
    return _connection(int(cid)), model


def adapter(ref: str | None) -> tuple[Adapter, dict, str, str]:
    """(adapter, connection, model, full ref) for a model reference, or the default model."""
    ref = ref or default_model_ref()
    conn, model = split_ref(ref)
    return Adapter(conn), conn, model, ref


def check_ref(ref: str) -> None:
    """Raises unless ref names a usable model: "<connection>/<model>", or "auto" (the smart router)."""
    from . import router

    if not router.is_auto(ref):
        split_ref(ref)


def default_model_ref() -> str:
    saved = db.get_setting("default_model")
    if saved:
        return saved
    if db.one("SELECT id FROM connections WHERE enabled = 1 AND auth_kind NOT IN ('local_ollama', 'session_scraping_forbidden') LIMIT 1"):
        return "auto"  # cloud first: the smart router uses whatever is connected and falls back on limits
    local = db.one("SELECT id FROM connections WHERE auth_kind = 'local_ollama' AND enabled = 1 ORDER BY id LIMIT 1")
    if local:
        return f"{local['id']}/{_local_default(local['id'])}"
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


def _local_default(cid: int) -> str:
    """config.DEFAULT_MODEL when Ollama has it, else the first installed chat model (never a 404 on first chat)."""
    try:
        names = [m["model"] for m in list_models_for(cid)]
    except Exception:  # Ollama not reachable yet: keep the configured name
        return config.DEFAULT_MODEL
    if not names or config.DEFAULT_MODEL in names:
        return config.DEFAULT_MODEL
    chat = [n for n in names if not re.search(r"embed|bge|minilm|nomic", n, re.I)]
    return (chat or names)[0]


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
    """Every usable model, the smart router's "auto" refs first. A connection that cannot be used (refused, needs
    sign-in) is skipped, never fatal (R4)."""
    from . import router

    out: list[dict] = []
    if router.any_routable():
        out = [{"ref": r, "model": label, "provider": "Smart router", "provider_id": 0, "free": None, "virtual": True}
               for r, label in router.AUTO_LABELS.items()]
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
    from . import router

    try:
        if router.is_auto(ref or default_model_ref()):
            first = router.candidates("strong")
            return Adapter(first[0][0]).context_chars() if first else 300_000
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
    """One non-streaming turn with optional tool calling, recorded as one model_call action per provider tried.

    Returns {"message": {...}, "usage": {...}, "ref": str, "model": str, "usd": float | None, "action_id": int}, plus
    "router" (who answered, fallbacks, quota) when ref is "auto".
    """
    from . import router

    ref = ref or default_model_ref()
    if router.is_auto(ref):
        return await router.chat(ref, messages, tools, trace, schema)
    return await chat_one(ref, messages, tools, trace, schema)


async def chat_one(ref: str | None, messages: list[dict], tools: list[dict] | None = None, trace: dict | None = None,
                   schema: dict | None = None) -> dict:
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
    """Yields {"reasoning"} and {"delta"} chunks, then {"done": True, "usage", "ms", "first_token_ms"} or {"error"}.
    With "auto", also {"router": {...}} before the first token and on the done event."""
    from . import router

    ref = ref or default_model_ref()
    if router.is_auto(ref):
        async for e in router.stream(ref, messages, trace):
            yield e
        return
    async for e in stream_one(ref, messages, trace):
        yield e


async def stream_one(ref: str | None, messages: list[dict], trace: dict | None = None) -> AsyncIterator[dict]:
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
