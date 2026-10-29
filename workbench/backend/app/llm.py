"""OpenAI compatible client for every provider (Ollama, OpenAI, Anthropic, OpenRouter, any /v1 endpoint).

Model references are "<provider_id>/<model>", e.g. "1/qwen3:4b" or "3/openai/gpt-4o".
"""

import json
import os
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
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    "anthropic": {"name": "Anthropic", "base_url": "https://api.anthropic.com/v1"},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
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
    return p["kind"] == "ollama" or ("openrouter.ai" in p["base_url"] and model.endswith(":free"))

THINK_RE = re.compile(r"<think>.*?</think>", re.S)
TIMEOUT = httpx.Timeout(connect=10, read=300, write=30, pool=10)
# Ollama's OpenAI endpoint (/v1) ignores num_ctx and runs at the server default (4096 tokens), silently cutting
# the start of the prompt (system prompt, memories, tool rules) once a page or a few messages are added.
# Ollama is therefore called on its native /api/chat with an explicit window.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
# Local models on a CPU need minutes per answer; measured here: qwen3:4b on 4 cores ran past the 300 s cloud timeout
OLLAMA_TIMEOUT = httpx.Timeout(connect=10, read=float(os.getenv("OLLAMA_TIMEOUT_S", "900")), write=30, pool=10)
CHARS_PER_TOKEN = 3  # conservative: real text averages 3.5 to 4, code and JSON less
CLOUD_CONTEXT_CHARS = 300_000  # about 100K tokens; every current cloud chat model has at least this


def context_chars(ref: str | None) -> int:
    """Characters of prompt this model can take, keeping a quarter of the window for the reply."""
    try:
        p, _ = split_ref(ref or default_model_ref())
    except HTTPException:
        return CLOUD_CONTEXT_CHARS
    if p["kind"] == "ollama":
        return int(OLLAMA_NUM_CTX * CHARS_PER_TOKEN * 0.75)
    return CLOUD_CONTEXT_CHARS


def _ollama_root(p: dict) -> str:
    return p["base_url"].rstrip("/").removesuffix("/v1")


def _ollama_messages(messages: list[dict]) -> list[dict]:
    """OpenAI shaped messages to Ollama's native shape: tool arguments are objects, tool results carry tool_name."""
    names: dict[str, str] = {}
    out = []
    for m in messages:
        m = dict(m)
        if m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                names[tc["id"]] = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append({"function": {"name": tc["function"]["name"], "arguments": args if isinstance(args, dict) else {}}})
            m["tool_calls"] = calls
        if m["role"] == "tool":
            m = {"role": "tool", "content": m["content"], "tool_name": names.get(m.get("tool_call_id", ""), "")}
        out.append(m)
    return out


def _ollama_body(model: str, messages: list[dict], stream: bool, tools: list[dict] | None = None) -> dict:
    body: dict = {"model": model, "messages": _ollama_messages(messages), "stream": stream,
                  "options": {"num_ctx": OLLAMA_NUM_CTX}}
    if tools:
        body["tools"] = tools
    return body


def _ollama_usage(data: dict) -> dict:
    return {"prompt_tokens": data.get("prompt_eval_count") or 0, "completion_tokens": data.get("eval_count") or 0}


async def _post(p: dict, url: str, body: dict, headers: dict | None = None, timeout: httpx.Timeout = TIMEOUT) -> httpx.Response:
    """A network failure or timeout becomes a clear error the caller can record, not a bare exception."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as e:
        raise HTTPException(504, f"{p['name']} did not answer within {int(timeout.read or 0)} s") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{p['name']} unreachable: {e.__class__.__name__}") from e


async def _ollama_post(p: dict, body: dict) -> dict:
    r = await _post(p, f"{_ollama_root(p)}/api/chat", body, timeout=OLLAMA_TIMEOUT)
    if r.status_code >= 400:
        raise HTTPException(502, f"{p['name']} error {r.status_code}: {r.text[:400]}")
    data = r.json()
    if (data.get("prompt_eval_count") or 0) >= OLLAMA_NUM_CTX:
        raise HTTPException(502, f"The prompt filled the whole {OLLAMA_NUM_CTX} token window of {body['model']}, so Ollama cut "
                                 "part of it. Start a new conversation, or raise OLLAMA_NUM_CTX if you have the memory.")
    return data


# Some local models (and some servers' parsers) print a tool call as text instead of returning a structured call.
# Accept only well formed calls to tools that were actually offered; anything else stays plain text.
_TOOL_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def parse_text_tool_calls(content: str, tool_names: set[str]) -> tuple[list[dict], str]:
    """Returns (tool_calls, remaining_text). Empty list when the text holds no valid call to an offered tool."""
    candidates = [(m.group(0), m.group(1)) for m in _TOOL_TAG_RE.finditer(content)]
    if not candidates:
        stripped = content.strip()
        fenced = _FENCE_RE.match(stripped)
        body = fenced.group(1) if fenced else stripped
        if body.startswith("{") and body.endswith("}"):
            candidates = [(content, body)]
    calls, rest = [], content
    for raw, payload in candidates:
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("name") not in tool_names:
            continue
        args = obj.get("arguments", obj.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                continue
        if not isinstance(args, dict):
            continue
        calls.append({"id": f"text_{len(calls)}", "type": "function",
                      "function": {"name": obj["name"], "arguments": json.dumps(args)}})
        rest = rest.replace(raw, "")
    return calls, (rest.strip() if calls else content)


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


def _headers(p: dict) -> dict:
    key = decrypt(p["api_key_enc"]) or "none"
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


def default_model_ref() -> str:
    saved = db.get_setting("default_model")
    if saved:
        return saved
    p = db.one("SELECT id FROM providers WHERE kind = 'ollama' AND enabled = 1 ORDER BY id LIMIT 1")
    if p:
        return f"{p['id']}/{config.DEFAULT_MODEL}"
    providers = db.all("SELECT id, base_url FROM providers WHERE enabled = 1 ORDER BY id")
    if not providers:
        raise HTTPException(400, "No model provider configured. Add an API key in Settings.")
    # Sensible current defaults when the provider lists them; otherwise the first model it lists
    preferred = {"api.anthropic.com": "claude-sonnet-5-5", "openrouter.ai": "anthropic/claude-sonnet-5.5",
                 "generativelanguage.googleapis.com": "gemini-3.8-flash"}
    for p in providers:
        want = next((m for host, m in preferred.items() if host in p["base_url"]), None)
        models = list_models_for(p["id"])
        if want and any(m["model"] == want for m in models):
            return f"{p['id']}/{want}"
    for p in providers:
        models = list_models_for(p["id"])
        if models:
            return models[0]["ref"]
    raise HTTPException(400, "No models available. Pick a default model in Settings.")


_model_cache: dict[int, tuple[float, list[dict]]] = {}


def list_models_for(pid: int, fresh: bool = False) -> list[dict]:
    p = _provider(pid)
    cached = _model_cache.get(pid)
    if cached and not fresh and cached[0] > time.time() - 120:
        return cached[1]
    names: list[str] = []
    try:
        r = httpx.get(_models_url(p["base_url"]), headers=_headers(p), timeout=15)
        r.raise_for_status()
        ids = {m["id"].removeprefix("models/") for m in r.json().get("data", []) if m.get("id")}
        names = sorted(i for i in ids if _keep_model(p["base_url"], i))
    except (httpx.HTTPError, ValueError, KeyError):
        names = []
    manual = [m.strip() for m in p["models"].split(",") if m.strip()]
    names = sorted(set(names) | set(manual))
    out = [{"ref": f"{pid}/{n}", "model": n, "provider": p["name"], "provider_id": pid, "free": is_free(p, n)} for n in names]
    _model_cache[pid] = (time.time(), out)
    return out


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


async def complete(ref: str | None, messages: list[dict]) -> str:
    p, model = split_ref(ref or default_model_ref())
    if p["kind"] == "ollama":
        data = await _ollama_post(p, _ollama_body(model, messages, False))
        return THINK_RE.sub("", (data.get("message") or {}).get("content") or "").strip()
    r = await _post(p, f"{p['base_url'].rstrip('/')}/chat/completions", _body(p, model, messages, False), _headers(p))
    if r.status_code >= 400:
        raise HTTPException(502, f"{p['name']} error {r.status_code}: {r.text[:400]}")
    content = r.json()["choices"][0]["message"].get("content") or ""
    return THINK_RE.sub("", content).strip()


async def stream(ref: str | None, messages: list[dict]) -> AsyncIterator[dict]:
    """Yields {"reasoning": str} and {"delta": str} chunks, then a final {"done": True, "usage": {...}, "ms": int}."""
    p, model = split_ref(ref or default_model_ref())
    started = time.monotonic()
    first_token_ms = None
    usage: dict = {}
    if p["kind"] == "ollama":
        async for e in _ollama_stream(p, model, messages, started):
            yield e
        return
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream(
            "POST", f"{p['base_url'].rstrip('/')}/chat/completions", headers=_headers(p), json=_body(p, model, messages, True)
        ) as r:
            if r.status_code >= 400:
                text = (await r.aread()).decode(errors="replace")
                yield {"error": f"{p['name']} error {r.status_code}: {text[:400]}"}
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
    yield {"done": True, "usage": usage, "ms": int((time.monotonic() - started) * 1000), "first_token_ms": first_token_ms}


async def chat(ref: str | None, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """One non-streaming turn with optional tool calling.

    Returns {"message": {...assistant message...}, "usage": {...}, "ref": str, "cost_usd": float | None}.
    OpenRouter reports the real cost of each call; other providers are priced from Settings (usage.py).
    """
    ref = ref or default_model_ref()
    p, model = split_ref(ref)
    names = {t["function"]["name"] for t in tools or []}
    if p["kind"] == "ollama":
        data = await _ollama_post(p, _ollama_body(model, messages, False, tools))
        msg = data.get("message") or {}
        out = {"role": "assistant", "content": THINK_RE.sub("", msg.get("content") or "").strip()}
        calls = [{"id": f"call_{i}", "type": "function",
                  "function": {"name": tc["function"]["name"], "arguments": json.dumps(tc["function"].get("arguments") or {})}}
                 for i, tc in enumerate(msg.get("tool_calls") or []) if (tc.get("function") or {}).get("name") in names]
        _attach_calls(out, calls, names)
        return {"message": out, "usage": _ollama_usage(data), "ref": ref, "model": model, "cost_usd": None}
    body = _body(p, model, messages, False)
    if tools:
        body["tools"] = tools  # tool_choice left to the default ("auto"): some Claude models reject forced choice
    if "openrouter.ai" in p["base_url"]:
        body["usage"] = {"include": True}
    r = await _post(p, f"{p['base_url'].rstrip('/')}/chat/completions", body, _headers(p))
    if r.status_code >= 400:
        raise HTTPException(502, f"{p['name']} error {r.status_code}: {r.text[:400]}")
    data = r.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    content = THINK_RE.sub("", msg.get("content") or "").strip()
    out = {"role": "assistant", "content": content}
    calls = [
        {"id": tc.get("id") or f"call_{i}", "type": "function",
         "function": {"name": tc["function"]["name"], "arguments": tc["function"].get("arguments") or "{}"}}
        for i, tc in enumerate(msg.get("tool_calls") or []) if tc.get("function", {}).get("name")
    ]
    _attach_calls(out, calls, names)
    usage = data.get("usage") or {}
    cost = usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None
    return {"message": out, "usage": usage, "ref": ref, "model": model, "cost_usd": cost}


def _attach_calls(out: dict, calls: list[dict], names: set[str]) -> None:
    """Structured calls win; otherwise a call the model printed as text is recovered and flagged."""
    if not calls and names and out["content"]:
        calls, out["content"] = parse_text_tool_calls(out["content"], names)
        if calls:
            out["parsed_from_text"] = True
    if calls:
        out["tool_calls"] = calls


async def _ollama_stream(p: dict, model: str, messages: list[dict], started: float) -> AsyncIterator[dict]:
    first_token_ms = None
    usage: dict = {}
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        async with client.stream("POST", f"{_ollama_root(p)}/api/chat", json=_ollama_body(model, messages, True)) as r:
            if r.status_code >= 400:
                text = (await r.aread()).decode(errors="replace")
                yield {"error": f"{p['name']} error {r.status_code}: {text[:400]}"}
                return
            async for line in r.aiter_lines():
                try:
                    chunk = json.loads(line) if line.strip() else None
                except ValueError:
                    continue
                if not chunk:
                    continue
                if chunk.get("error"):
                    yield {"error": f"{p['name']}: {chunk['error']}"}
                    return
                m = chunk.get("message") or {}
                if m.get("thinking"):
                    yield {"reasoning": m["thinking"]}
                if m.get("content"):
                    if first_token_ms is None:
                        first_token_ms = int((time.monotonic() - started) * 1000)
                    yield {"delta": m["content"]}
                if chunk.get("done"):
                    usage = _ollama_usage(chunk)
    if usage.get("prompt_tokens", 0) >= OLLAMA_NUM_CTX:
        yield {"error": f"The prompt filled the whole {OLLAMA_NUM_CTX} token window, so Ollama cut part of it."}
        return
    yield {"done": True, "usage": usage, "ms": int((time.monotonic() - started) * 1000), "first_token_ms": first_token_ms}


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
