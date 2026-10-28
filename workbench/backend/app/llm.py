"""OpenAI compatible client for every provider (Ollama, OpenAI, Anthropic, OpenRouter, any /v1 endpoint).

Model references are "<provider_id>/<model>", e.g. "1/qwen3:4b" or "3/openai/gpt-4o".
"""

import asyncio
import json
import re
import time
from typing import AsyncIterator

import httpx
from fastapi import HTTPException

from . import config, db
from .security import decrypt, encrypt

PRESETS = {
    # Native Ollama app on the host (fast on Mac: uses the GPU) unless the ollama add-on container is configured
    "ollama": {"name": "Ollama (local)", "base_url": f"{config.OLLAMA_URL or 'http://host.docker.internal:11434'}/v1"},
    # llama.cpp's llama-server speaks the OpenAI API on port 8080; start it with --jinja so tool calls work
    "llamacpp": {"name": "llama.cpp (local)", "base_url": f"{config.LLAMACPP_URL or 'http://host.docker.internal:8080'}/v1"},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    "anthropic": {"name": "Anthropic", "base_url": "https://api.anthropic.com/v1"},
    "openrouter": {"name": "OpenRouter", "base_url": f"{config.OPENROUTER_URL or 'https://openrouter.ai/api'}/v1",
                   "note": "Free account key. Models ending in :free cost nothing (20 requests/min, 50/day; 1000/day once you have bought $10 of credits)."},
    "gemini": {"name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "note": "Free tier with a key from aistudio.google.com (Flash and Flash-Lite models). Google may use free tier prompts to improve its products."},
    # OpenCode's own free models are refused outside the OpenCode app ("Free models work in OpenCode"),
    # so only the paid Zen and the Go subscription are offered here. Free OpenCode models: the paperclip add-on.
    "opencode-go": {"name": "OpenCode Go", "base_url": "https://opencode.ai/inference/go/openai/v1",
                    "note": "OpenCode Go subscription ($10/month flat). Key from opencode.ai/console."},
    "opencode-zen": {"name": "OpenCode Zen", "base_url": "https://opencode.ai/inference/openai/v1",
                     "note": "Pay as you go. Key from opencode.ai/console."},
}

# Model families that OpenCode serves on other endpoints than chat completions, plus models only the OpenCode app may use
# (per opencode.ai/docs/zen and /docs/go endpoint tables)
_OPENCODE_SKIP = re.compile(r"^(claude-|gpt-|grok-|gemini-|muse-|jev-|test$|big-pickle$)|-free$")
_ZEN_SKIP = re.compile(r"^(qwen3\.8-flash|qwen3\.6-plus|qwen3\.5-plus)$")
_GO_SKIP = re.compile(r"^(qwen|minimax-m3$)")
# Gemini lists audio, image, embedding and robotics models too; only text chat models work here
_GEMINI_SKIP = re.compile(r"(tts|embedding|live|transcribe|translate|robotics|imagen|veo|aqa|image|audio|lyria)")


def _models_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if "opencode.ai/inference" in base:  # chat is served under /openai/v1, the model list under /v1
        return base.replace("/openai/v1", "/v1") + "/models"
    return base + "/models"


def _keep_model(base_url: str, model: str) -> bool:
    if "opencode.ai/inference" in base_url:
        extra = _GO_SKIP if "/inference/go/" in base_url else _ZEN_SKIP
        return not (_OPENCODE_SKIP.search(model) or extra.search(model))
    if "generativelanguage.googleapis.com" in base_url:
        return model.startswith(("gemini", "gemma")) and not _GEMINI_SKIP.search(model)
    return True


def is_free(p: dict, model: str) -> bool:
    """True only when ROOKS has evidence that a call to this model costs nothing. A free tier on the provider is not
    enough on its own:
    - Ollama on this computer or network (but not Ollama's cloud models, which run on ollama.com);
    - OpenRouter models ending in :free whose listed price, from OpenRouter's own model list, is zero;
    - providers you confirmed cannot bill you (a Gemini key from a Google project with no billing account, or a
      local server with no paid API behind it), because no API reports that; see free_basis."""
    return bool(free_basis(p, model))


def free_basis(p: dict, model: str) -> str:
    """Why a model counts as free ("" when it does not): "local", "price" or "confirmed"."""
    if _cloud_tag(model):
        return ""
    if p["kind"] == "ollama" and _local_endpoint(p["base_url"]):
        return "local"
    if _is_openrouter(p):
        return "price" if model.endswith(":free") and model in zero_priced(p["id"]) else ""
    if confirmed_free(p):
        return "confirmed"
    return ""


def _cloud_tag(model: str) -> bool:
    # Ollama's cloud models ("gpt-oss:120b-cloud") are served by ollama.com even through a local Ollama
    return bool(re.search(r"(^|[-:])cloud$", model))


def zero_priced(pid: int) -> set[str]:
    """Models OpenRouter listed with every price at zero, from the last model listing (see list_models_for)."""
    try:
        return set(json.loads(db.get_setting(f"provider_prices:{pid}", "") or "{}").get("zero", []))
    except ValueError:
        return set()


def _prices_age(pid: int) -> float:
    try:
        return time.time() - float(json.loads(db.get_setting(f"provider_prices:{pid}", "") or "{}").get("at", 0))
    except (ValueError, TypeError):
        return float("inf")


# Who may confirm that a provider cannot bill: Gemini (Google reports a key's tier only in AI Studio, not by API) and
# local servers other than Ollama (llama.cpp, LM Studio), which could be a proxy to a paid API.
def can_confirm_free(p: dict) -> bool:
    if _is_openrouter(p):
        return False  # OpenRouter reports its prices, so only its price list decides
    return "generativelanguage.googleapis.com" in p["base_url"] or (p["kind"] != "ollama" and _local_endpoint(p["base_url"]))


def confirmed_free(p: dict) -> bool:
    return can_confirm_free(p) and db.get_setting(f"provider_free_confirmed:{p['id']}") == "1"


# AI modes. "" means none was chosen (installs from before v11.1): everything works as it did, nothing is restricted.
MODES = ("free", "premium", "local", "custom")


def ai_mode() -> str:
    m = db.get_setting("ai_mode", "") or ""
    return m if m in MODES else ""


def mode_allows(ref: str) -> str:
    """"" when the current AI mode allows this model, otherwise the reason it does not."""
    m = ai_mode()
    if m not in ("free", "local"):
        return ""
    try:
        p, model = split_ref(ref)
    except HTTPException as e:
        return str(e.detail)
    if m == "local":
        return "" if is_local(ref) else (f"{model} runs in the cloud ({p['name']}), and ROOKS is set to Local AI. "
                                         "Pick a local model, or change the AI mode in Settings, under AI models.")
    if db.get_setting(f"free_blocked:{ref}") == "1":
        return (f"{model} reported a charge, so ROOKS stopped using it in Free Cloud AI mode. "
                "Pick another free model, or change the AI mode in Settings.")
    if is_free(p, model):
        return ""
    if _is_openrouter(p) and model.endswith(":free"):
        why = "its price could not be confirmed as zero from OpenRouter's model list"
    elif can_confirm_free(p):
        why = f"you have not confirmed that {p['name']} cannot bill you"
    else:
        why = "it is not verified as free"
    return (f"{model} was not used: ROOKS is in Free Cloud AI mode and {why}. Pick a model marked free, "
            "or change the AI mode in Settings, under AI models.")


async def mode_guard(ref: str) -> None:
    """Refuse a model call the AI mode does not allow. In Free Cloud AI mode an OpenRouter price list older than six
    hours is fetched again first, so a model that stopped being free is caught."""
    if ai_mode() == "free":
        try:
            p, model = split_ref(ref)
        except HTTPException:
            p, model = None, ""
        if p and _is_openrouter(p) and model.endswith(":free") and _prices_age(p["id"]) > 6 * 3600:
            await asyncio.to_thread(list_models_for, p["id"], True)
    why = mode_allows(ref)
    if why:
        raise ModelError(why, 402, retryable=True)


def charged_in_free_mode(ref: str, cost_usd) -> None:
    """A model that reports a cost while in Free Cloud AI mode is never used again in that mode."""
    if ai_mode() == "free" and isinstance(cost_usd, (int, float)) and cost_usd > 0:
        db.set_setting(f"free_blocked:{ref}", "1")
        from . import audit
        audit.record("system", "free mode: model reported a charge", ref, f"${cost_usd:.6f}; blocked in Free Cloud AI mode", ok=False)


def is_local(ref: str) -> bool:
    """True when the model runs on this computer or the local network, so data sent to it does not leave."""
    try:
        p, model = split_ref(ref)
    except HTTPException:
        return False
    return _local_endpoint(p["base_url"]) and not (p["kind"] == "ollama" and _cloud_tag(model))


def _local_endpoint(base_url: str) -> bool:
    import ipaddress
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "").lower()
    # Protocol selection does not establish locality: Ollama can be served
    # behind a public endpoint too. Classify the configured destination.
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    # A single label name ("ollama" in Docker Compose, a machine on your network) cannot be a public internet host
    if host and "." not in host and ":" not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def _ref_free(ref: str) -> bool:
    try:
        p, model = split_ref(ref)
    except HTTPException:
        return False
    return is_free(p, model)


def spend_guard() -> None:
    """Refuse model calls once this month's known spend reaches the limit set in Settings (0 or unset = no limit).
    Spend is what providers reported or what your prices in Settings give; calls without a known price count as 0."""
    limit = float(db.get_setting("monthly_limit_cents", "0") or 0)
    if limit <= 0:
        return
    from .usage import month_start

    spent = db.one("SELECT COALESCE(SUM(cost_cents), 0) AS c FROM usage WHERE ts >= ?", month_start())["c"]
    if spent >= limit:
        raise ModelError(f"This month's spending limit (${limit / 100:.2f}) is reached (${spent / 100:.2f} spent). "
                         "Raise it in Settings, under Usage, or use a free model.", 402, retryable=False)

THINK_RE = re.compile(r"<think>.*?</think>", re.S)
TIMEOUT = httpx.Timeout(connect=10, read=300, write=30, pool=10)


def seed_providers() -> None:
    if db.one("SELECT id FROM providers LIMIT 1"):
        return
    if config.OLLAMA_URL:
        db.run(
            "INSERT INTO providers(name, kind, base_url, created_at) VALUES (?, 'ollama', ?, ?)",
            PRESETS["ollama"]["name"], PRESETS["ollama"]["base_url"], db.now(),
        )
    for kind, key in config.SEED_KEYS.items():
        if key:
            p = PRESETS[kind]
            db.run(
                "INSERT INTO providers(name, kind, base_url, api_key_enc, created_at) VALUES (?, ?, ?, ?, ?)",
                p["name"], "anthropic" if kind == "anthropic" else "openai", p["base_url"], encrypt(key), db.now(),
            )


def _provider(pid: int) -> dict:
    p = db.one("SELECT * FROM providers WHERE id = ? AND enabled = 1", pid)
    if not p:
        raise HTTPException(404, f"Provider {pid} not found or disabled")
    return p


def _key(p: dict) -> str:
    return decrypt(p["api_key_enc"]) or "none"


def engine(p: dict) -> str:
    """'any-llm' when this Anthropic connection uses the optional native engine (anyllm_engine.py), else ''."""
    if p["kind"] != "anthropic" or db.get_setting(f"provider_engine:{p['id']}", "") != "any-llm":
        return ""
    from . import anyllm_engine

    return "any-llm" if anyllm_engine.available() else ""


def _headers(p: dict) -> dict:
    key = _key(p)
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if p["kind"] == "anthropic":
        h["x-api-key"] = key
        h["anthropic-version"] = "2023-06-01"
    return h


def split_ref(ref: str) -> tuple[dict, str]:
    pid, _, model = ref.partition("/")
    if not pid.isdigit() or not model:
        raise HTTPException(400, f"Invalid model reference '{ref}'")
    return _provider(int(pid)), model


def _ref_ok(ref: str | None) -> bool:
    pid, _, model = (ref or "").partition("/")
    return bool(pid.isdigit() and model and db.one("SELECT id FROM providers WHERE id = ? AND enabled = 1", int(pid)))


def check_info(pid: int) -> dict:
    """The last connection check for a provider (see check_provider), or {} if it was never checked."""
    try:
        return json.loads(db.get_setting(f"provider_check:{pid}", "") or "{}")
    except ValueError:
        return {}


def _ordered_providers() -> list[dict]:
    # Cloud providers that passed their check first; local Ollama last (it may be stopped or missing the model)
    rows = db.all("SELECT * FROM providers WHERE enabled = 1 ORDER BY id")
    return sorted(rows, key=lambda p: (p["kind"] == "ollama", not check_info(p["id"]).get("ok"), p["id"]))


def recommended_ref(p: dict) -> str | None:
    info = check_info(p["id"])
    if info.get("ok") and info.get("recommended"):
        return f"{p['id']}/{info['recommended']}"
    # Never checked, or checked and failing: not picked automatically. A model is only used without being chosen by
    # the user after a real request to it succeeded (see check_provider).
    return None


def effective_default() -> tuple[str, str]:
    """(model ref, source). source is "chosen" when the user picked it in Settings, "auto" otherwise.

    A saved default whose provider was removed or disabled is ignored instead of breaking every chat."""
    saved = db.get_setting("default_model")
    if _ref_ok(saved) and not mode_allows(saved):
        return saved, "chosen"
    providers = _ordered_providers()
    if not providers:
        raise HTTPException(400, "No AI model is connected yet. Add a key in Settings, under AI models.")
    for p in providers:
        ref = recommended_ref(p)
        if ref and not mode_allows(ref):
            return ref, "auto"
    m = ai_mode()
    if m == "free":
        raise HTTPException(400, "ROOKS is in Free Cloud AI mode and none of your models is verified as free and working. "
                            "Connect OpenRouter or Google Gemini in Settings, under AI models, or change the AI mode.")
    if m == "local":
        raise HTTPException(400, "ROOKS is set to Local AI and no local model is working. Start Ollama or your local server, "
                            "or change the AI mode in Settings, under AI models.")
    raise HTTPException(400, "None of your AI providers is working right now. Fix the key or connect another provider in Settings, under AI models.")


def default_model_ref() -> str:
    return effective_default()[0]


def fallback_refs(primary: str) -> list[str]:
    """Models tried in order when the default fails (OpenClaw's primary + fallbacks pattern).
    Uses the list saved in Settings, otherwise the recommended model of each other working provider."""
    try:
        saved = json.loads(db.get_setting("fallback_models", "") or "null")
    except ValueError:
        saved = None
    if isinstance(saved, list):
        refs = [r for r in saved if isinstance(r, str) and _ref_ok(r)]  # chosen by the user: their call
    else:
        refs = [r for r in (recommended_ref(p) for p in _ordered_providers()) if r]
        if _ref_free(primary) and db.get_setting("fallback_allow_paid", "0") != "1":
            # A free default never silently falls back to a model that costs money
            refs = [r for r in refs if _ref_free(r)]
    out: list[str] = []
    for r in refs:
        if mode_allows(r):  # Free Cloud AI and Local AI never fall back to a model they do not allow
            continue
        if r != primary and r not in out:
            out.append(r)
    return out[:3]


_model_cache: dict[int, tuple[float, list[dict]]] = {}
_list_errors: dict[int, tuple[int, str]] = {}  # provider id -> (HTTP status or 0, message) from the last model listing


def list_models_for(pid: int, fresh: bool = False) -> list[dict]:
    p = _provider(pid)
    cached = _model_cache.get(pid)
    if cached and not fresh and cached[0] > time.time() - 120:
        return cached[1]
    meta: dict[str, dict] = {}
    _list_errors.pop(pid, None)
    try:
        r = httpx.get(_models_url(p["base_url"]), headers=_headers(p), timeout=15)
        if r.status_code >= 400:
            _list_errors[pid] = (r.status_code, r.text[:300])
        else:
            for m in r.json().get("data", []):
                mid = (m.get("id") or "").removeprefix("models/")
                if mid and _keep_model(p["base_url"], mid):
                    params = m.get("supported_parameters")
                    meta[mid] = {"tools": ("tools" in params) if isinstance(params, list) else None,
                                 "ctx": m.get("context_length") if isinstance(m.get("context_length"), int) else None,
                                 **_prices(m.get("pricing"))}
            if _is_openrouter(p):
                zero = sorted(n for n, x in meta.items() if x.get("zero_price"))
                db.set_setting(f"provider_prices:{pid}", json.dumps({"zero": zero, "at": time.time()}))
    except (httpx.HTTPError, ValueError, KeyError, AttributeError) as e:
        _list_errors[pid] = (0, str(e)[:300] or e.__class__.__name__)
    for m in (x.strip() for x in p["models"].split(",")):
        if m:
            meta.setdefault(m, {"tools": None, "ctx": None})
    out = [{"ref": f"{pid}/{n}", "model": n, "provider": p["name"], "provider_id": pid, "free": is_free(p, n),
            "free_basis": free_basis(p, n), **{k: v for k, v in meta[n].items() if k != "zero_price"}}
           for n in sorted(meta)]
    _model_cache[pid] = (time.time(), out)
    return out


def _prices(pricing) -> dict:
    """Prices from a model listing that reports them (OpenRouter: USD per token, as strings), per million tokens.
    zero_price is True only when every listed price parses as zero; a missing or unreadable price is never zero."""
    if not isinstance(pricing, dict) or not pricing:
        return {}
    try:
        vals = {k: float(v) for k, v in pricing.items() if v not in (None, "")}
    except (TypeError, ValueError):
        return {}
    if "prompt" not in vals or "completion" not in vals:
        return {}
    return {"price_in": round(vals["prompt"] * 1_000_000, 4), "price_out": round(vals["completion"] * 1_000_000, 4),
            "zero_price": all(v == 0 for v in vals.values())}


def list_models(fresh: bool = False) -> list[dict]:
    out: list[dict] = []
    for p in db.all("SELECT id FROM providers WHERE enabled = 1 ORDER BY id"):
        out.extend(list_models_for(p["id"], fresh))
    return out


def _body(p: dict, model: str, messages: list[dict], stream: bool) -> dict:
    body: dict = {"model": model, "messages": messages, "stream": stream}
    if p["kind"] == "anthropic":
        body["max_tokens"] = 8192
    if stream:
        body["stream_options"] = {"include_usage": True}
    # temperature is deliberately not sent: recent Claude models reject values below 1
    return body


class ModelError(HTTPException):
    """A provider refused or failed a request. `status` is the provider's HTTP status (0 when unreachable)."""

    def __init__(self, detail: str, status: int = 0, retryable: bool = True):
        super().__init__(502, detail)
        self.status = status
        self.retryable = retryable


def explain(p: dict, status: int, text: str) -> str:
    """Turn a provider error into something a person can act on; the raw detail is kept at the end."""
    name, low = p["name"], (text or "").lower()
    # Google answers a bad key with 400 "Please pass a valid API key" rather than 401
    bad_key = status in (401, 403) or (status == 400 and re.search(r"api[ _-]?key", low) and re.search(r"valid|incorrect|missing|pass", low))
    if bad_key:
        msg = f"{name} rejected the API key. Paste the key again in Settings, under AI models."
    elif status == 402:
        msg = f"{name} says there are no credits for this model. Pick a model marked (free), or add credits with {name}."
    elif status == 429:
        extra = " Free OpenRouter models allow 50 requests a day until you have bought $10 of credits." if _is_openrouter(p) else ""
        msg = f"{name} is limiting requests right now. Wait a minute or switch model.{extra}"
    elif "tool" in low and ("support" in low or "endpoint" in low):
        msg = f"This {name} model can't use tools, which the assistant needs. Pick another model."
    elif status == 404:
        msg = f"{name} doesn't offer this model to your key. Pick another model in Settings, under AI models."
    elif status == 0:
        msg = f"Can't reach {name}. Check the internet connection, or that the local server is running."
    elif status >= 500:
        msg = f"{name} had a problem on their side ({status}). Try again shortly."
    else:
        msg = f"{name} refused the request ({status})."
    detail = re.sub(r"\s+", " ", text or "").strip()[:240]
    return f"{msg} Details: {detail}" if detail else msg


async def _call(p: dict, body: dict) -> dict:
    if engine(p):
        from . import anyllm_engine

        try:
            return await anyllm_engine.call(p, body)
        except Exception as e:  # noqa: BLE001 - any-llm and the SDK raise their own types; explain them the same way
            status, text = anyllm_engine.error_text(e)
            raise ModelError(explain(p, status, text), status, status != 400) from e
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{p['base_url'].rstrip('/')}/chat/completions", headers=_headers(p), json=body)
    except httpx.HTTPError as e:
        raise ModelError(explain(p, 0, str(e) or e.__class__.__name__), 0) from e
    if r.status_code >= 400:
        # A malformed request (400) is ours, not the provider's; only tool support problems are worth a fallback
        retryable = r.status_code != 400 or "tool" in r.text.lower()
        raise ModelError(explain(p, r.status_code, r.text), r.status_code, retryable)
    try:
        return r.json()
    except ValueError as e:
        raise ModelError(explain(p, r.status_code, "The reply was not JSON."), r.status_code) from e


async def complete(ref: str | None, messages: list[dict]) -> str:
    res = await chat(ref, messages)
    return res["message"].get("content") or ""


async def stream(ref: str | None, messages: list[dict], cap: bool = False) -> AsyncIterator[dict]:
    """Yields {"reasoning": str} and {"delta": str} chunks, then a final {"done": True, "usage": {...}, "ms": int}.
    cap: a capability test, with output capped like a connection check."""
    try:
        ref = ref or default_model_ref()
        p, model = split_ref(ref)
        await mode_guard(ref)
        spend_guard()
    except HTTPException as e:
        yield {"error": str(e.detail)}
        return
    started = time.monotonic()
    first_token_ms = None
    usage: dict = {}
    if engine(p):
        from . import anyllm_engine

        body = _cap(p, _body(p, model, messages, True)) if cap else _body(p, model, messages, True)
        try:
            async for chunk in anyllm_engine.stream(p, body):
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk["choices"]:
                    if delta := choice["delta"].get("content"):
                        if first_token_ms is None:
                            first_token_ms = int((time.monotonic() - started) * 1000)
                        yield {"delta": delta}
        except Exception as e:  # noqa: BLE001
            status, text = anyllm_engine.error_text(e)
            yield {"error": explain(p, status, text)}
            return
        yield {"done": True, "usage": usage, "ms": int((time.monotonic() - started) * 1000), "first_token_ms": first_token_ms}
        return
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream(
                "POST", f"{p['base_url'].rstrip('/')}/chat/completions", headers=_headers(p),
                json=_cap(p, _body(p, model, messages, True)) if cap else _body(p, model, messages, True)
            ) as r:
                if r.status_code >= 400:
                    text = (await r.aread()).decode(errors="replace")
                    yield {"error": explain(p, r.status_code, text)}
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        d = choice.get("delta") or {}
                        # Thinking models stream reasoning separately (Ollama: "reasoning", others: "reasoning_content")
                        thought = d.get("reasoning") or d.get("reasoning_content")
                        if thought:
                            yield {"reasoning": thought}
                        delta = d.get("content")
                        if delta:
                            if first_token_ms is None:
                                first_token_ms = int((time.monotonic() - started) * 1000)
                            yield {"delta": delta}
    except httpx.HTTPError as e:
        yield {"error": explain(p, 0, str(e) or e.__class__.__name__)}
        return
    charged_in_free_mode(ref, usage.get("cost"))
    yield {"done": True, "usage": usage, "ms": int((time.monotonic() - started) * 1000), "first_token_ms": first_token_ms}


PING_TIMEOUT = 45      # seconds for one connection-check request
CHECK_BUDGET = 120     # seconds for a whole provider check, however many models it tries


def _cap(p: dict, body: dict) -> dict:
    """Small output cap for connection checks, so checking a paid model costs at most a few tokens."""
    if p["kind"] == "anthropic":
        body["max_tokens"] = 32
    elif "api.openai.com" in p["base_url"]:
        body["max_completion_tokens"] = 64  # reasoning models count their thinking here
    else:
        body["max_tokens"] = 32
    return body


async def _chat_once(ref: str, messages: list[dict], tools: list[dict] | None, ping: bool = False) -> dict:
    p, model = split_ref(ref)
    # Every model call (chat, missions, workers, research, check-ins, vision, connection checks) passes here
    await mode_guard(ref)
    # A provider health check can still incur usage charges; it must not
    # bypass a configured monthly spending limit.
    spend_guard()
    body = _body(p, model, messages, False)
    if tools:
        body["tools"] = tools  # tool_choice left to the default ("auto"): some Claude models reject forced choice
    if _is_openrouter(p):
        body["usage"] = {"include": True}
    if ping:
        try:
            data = await asyncio.wait_for(_call(p, _cap(p, dict(body))), PING_TIMEOUT)
        except ModelError:
            # Never retry a charged model check without an output cap.
            # A provider rejecting the cap is a configuration error, not a
            # reason to send an unbounded paid request.
            raise
        except asyncio.TimeoutError as e:
            raise ModelError(f"{p['name']} did not answer within {PING_TIMEOUT} seconds.", 0) from e
    else:
        data = await _call(p, body)
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    content = THINK_RE.sub("", msg.get("content") or "").strip()
    out = {"role": "assistant", "content": content}
    if msg.get("tool_calls"):
        out["tool_calls"] = [
            {"id": tc.get("id") or f"call_{i}", "type": "function",
             "function": {"name": tc["function"]["name"], "arguments": tc["function"].get("arguments") or "{}"}}
            for i, tc in enumerate(msg["tool_calls"]) if tc.get("function", {}).get("name")
        ]
    usage = data.get("usage") or {}
    cost = usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None
    charged_in_free_mode(ref, cost)
    return {"message": out, "usage": usage, "ref": ref, "model": model, "cost_usd": cost}


async def chat(ref: str | None, messages: list[dict], tools: list[dict] | None = None, local_only: bool = False) -> dict:
    """One non-streaming turn with optional tool calling.

    With no ref, the default model runs and, if it fails, each fallback in turn; the result's "fallback_from" lists
    what failed. A ref the user picked explicitly is strict and fails visibly, like OpenClaw's session selection.
    Returns {"message", "usage", "ref", "model", "cost_usd", "fallback_from"}.
    OpenRouter reports the real cost of each call; other providers are priced from Settings (usage.py).
    """
    chain = [ref] if ref else [default_model_ref()]
    if not ref:
        chain += fallback_refs(chain[0])
    if local_only:
        # Local only data never goes to a cloud model, not even as a fallback
        if ref and not is_local(ref):
            raise ModelError(f"{ref} is not a local model, and this work is set to local models only. Pick a local model "
                             "(for example Ollama) for it.", 400, retryable=False)
        chain = [r for r in chain if is_local(r)]
        if not chain:
            raise ModelError("This work is set to local models only, and no local model is set up. Connect one (for example "
                             "Ollama) or choose it for this conversation.", 400, retryable=False)
    if not ref and ai_mode() in ("free", "local"):
        chain = [r for r in chain if not mode_allows(r)] or chain[:1]  # the first one then fails with the reason
    failed: list[dict] = []
    for i, r in enumerate(chain):
        try:
            res = await _chat_once(r, messages, tools)
        except ModelError as e:
            failed.append({"ref": r, "error": str(e.detail)})
            if i + 1 < len(chain) and e.retryable:
                continue
            if len(failed) > 1:
                raise ModelError(f"{failed[0]['error']} The backup models failed too: "
                                 + "; ".join(f"{f['ref']}: {f['error'][:120]}" for f in failed[1:]), e.status, e.retryable) from e
            raise
        except HTTPException as e:  # provider removed or disabled
            failed.append({"ref": r, "error": str(e.detail)})
            if i + 1 < len(chain):
                continue
            raise
        res["fallback_from"] = failed
        return res
    raise HTTPException(400, "No model available.")


# Connecting a provider: check the key, find a model that answers with tool support, remember it
PING_TOOL = {"type": "function", "function": {"name": "noop", "description": "Not needed for this reply.",
                                              "parameters": {"type": "object", "properties": {}}}}
_OPENAI_SKIP = re.compile(r"(audio|realtime|image|tts|transcribe|search|embedding|moderation|dall-e|whisper|davinci|babbage|instruct|codex)")


def candidates(p: dict, models: list[dict], free_tier: bool | None) -> list[str]:
    """Models worth trying first for the assistant, best first. Heuristic only: check_provider tests them for real."""
    names = [m["model"] for m in models]
    tools_ok = [m for m in models if m.get("tools") is not False]
    base = p["base_url"]
    if p["kind"] == "ollama":
        pref = [n for n in names if n.split(":")[0] == config.DEFAULT_MODEL.split(":")[0]]
        return pref + [n for n in names if n not in pref and "embed" not in n]
    if _is_openrouter(p):
        free = sorted((m for m in tools_ok if m["model"].endswith(":free") and m.get("tools")),
                      key=lambda m: -(m.get("ctx") or 0))
        paid = [m["model"] for m in tools_ok if not m["model"].endswith(":free") and m.get("tools")]
        pref_paid = [n for n in ("anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-4.5", "openai/gpt-5") if n in paid]
        free_names = [m["model"] for m in free]
        return free_names + pref_paid if free_tier is not False else pref_paid + free_names + paid[:5]
    if "anthropic.com" in base:
        return sorted((n for n in names if "sonnet" in n), reverse=True) + sorted((n for n in names if "haiku" in n), reverse=True) + names
    if "generativelanguage.googleapis.com" in base:
        flash = [n for n in names if "flash" in n and "lite" not in n and "preview" not in n]
        return sorted(flash, reverse=True) + sorted((n for n in names if "flash" in n and n not in flash), reverse=True) + names
    if "api.openai.com" in base:
        chat_models = [n for n in names if n.startswith(("gpt-", "o")) and not _OPENAI_SKIP.search(n)]
        return sorted(chat_models, key=lambda n: ("mini" not in n, n), reverse=True) + names
    return names


def _is_openrouter(p: dict) -> bool:
    # OPENROUTER_URL points the OpenRouter preset at a compatible stand-in (the browser tests) or a proxy
    return "openrouter.ai" in p["base_url"] or bool(config.OPENROUTER_URL and p["base_url"].startswith(config.OPENROUTER_URL))


async def key_status(p: dict) -> dict:
    """OpenRouter's own key endpoint (GET /api/v1/key): whether the key works and is on the free tier."""
    if not _is_openrouter(p):
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{p['base_url'].rstrip('/')}/key", headers=_headers(p))
    except httpx.HTTPError as e:
        return {"error": explain(p, 0, str(e))}
    if r.status_code in (401, 403):
        return {"error": explain(p, r.status_code, r.text), "status": r.status_code}
    try:
        d = r.json().get("data") or {}
    except ValueError:
        return {}
    return {"free_tier": d.get("is_free_tier"), "limit_remaining": d.get("limit_remaining")}


async def check_provider(pid: int, max_tries: int = 4, allow_paid: bool = False) -> dict:
    """List the provider's models, check the key, then send a tiny real request (with a tool attached, as the
    assistant does) to the most suitable models until one answers. Only a model that answered is recommended."""
    import asyncio

    p = _provider(pid)
    deadline = time.monotonic() + CHECK_BUDGET
    _model_cache.pop(pid, None)
    models = await asyncio.to_thread(list_models_for, pid, True)
    report: dict = {"ok": False, "provider": p["name"], "models": len(models), "free_models": sum(1 for m in models if m["free"]),
                    "free_tool_models": sum(1 for m in models if m["free"] and m.get("tools")), "mode": ai_mode(),
                    "recommended": "", "free_tier": None, "tried": [], "error": "", "checked_at": db.now()}
    key = await key_status(p)
    report["free_tier"] = key.get("free_tier")
    if key.get("status") in (401, 403):
        report["error"] = key["error"]
    elif not models:
        status, text = _list_errors.get(pid, (0, "The provider listed no models."))
        report["error"] = explain(p, status, text) if status else (
            explain(p, 0, text) if text != "The provider listed no models." else
            f"{p['name']} listed no models. For local models, download one first; for other endpoints, type model names under Advanced.")
    else:
        # Automatic and startup verification must never send a paid request.
        # A user must deliberately opt in to a bounded paid connection check.
        if ai_mode() in ("free", "local"):
            allow_paid = False  # these modes never send a request that could be charged, not even to check a key
        choices = [n for n in candidates(p, models, key.get("free_tier"))
                   if (allow_paid or is_free(p, n) or is_local(f"{pid}/{n}")) and not mode_allows(f"{pid}/{n}")]
        for name in choices[:max_tries]:
            if time.monotonic() > deadline:
                report["tried"].append({"model": name, "error": f"Not tried: the check's {CHECK_BUDGET} second limit was reached."})
                break
            try:
                ping_result = await _chat_once(f"{pid}/{name}", [{"role": "user", "content": "Reply with only the word OK."}], [PING_TOOL], ping=True)
                # Connection tests are still model calls and must count toward usage.
                from . import usage
                usage.record(ping_result, channel="provider_check")
            except ModelError as e:
                report["tried"].append({"model": name, "error": str(e.detail)[:300]})
                if e.status in (401, 403) or "rejected the API key" in str(e.detail):
                    break  # the key itself is wrong: no other model will work
                continue
            report.update(ok=True, recommended=name)
            break
        if not report["ok"]:
            report["error"] = (report["tried"][0]["error"] if report["tried"] else _no_candidate(p))
    db.set_setting(f"provider_check:{pid}", json.dumps(report))
    return report


def _no_candidate(p: dict) -> str:
    m = ai_mode()
    if m == "free":
        if can_confirm_free(p) and not confirmed_free(p):
            return (f"Not checked: ROOKS is in Free Cloud AI mode and cannot tell from {p['name']} whether this key can be "
                    "billed. Confirm it below if it comes from a project with no billing, or change the AI mode.")
        return (f"Not checked: ROOKS is in Free Cloud AI mode and {p['name']} has no model verified as free. "
                "No request was sent.")
    if m == "local":
        return f"Not checked: ROOKS is set to Local AI and {p['name']} is not on this computer or network."
    return "No free model available for automatic verification. Use Check and explicitly approve a paid verification request."


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


async def check_unchecked() -> None:
    """At startup, check providers saved before checks existed (or never checked), so their keys start working
    without being pasted again. Best effort: a failure is recorded on the provider, never raised."""
    import logging

    for p in db.all("SELECT id FROM providers WHERE enabled = 1 ORDER BY id"):
        if check_info(p["id"]):
            continue
        try:
            await check_provider(p["id"])
        except Exception as e:  # noqa: BLE001 - startup must not fail because a provider is down
            logging.getLogger("llm").warning("provider %s check failed: %s", p["id"], e)

