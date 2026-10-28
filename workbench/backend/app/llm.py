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
# Ollama's own default window is 4096 tokens, smaller than the assistant's tools, memories and one web page together,
# and its OpenAI compatible endpoint cannot change it. Ollama providers therefore use the native API with num_ctx.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))


def ollama_num_ctx() -> int:
    try:
        return max(2048, int(db.get_setting("ollama_num_ctx", "") or OLLAMA_NUM_CTX))
    except ValueError:
        return OLLAMA_NUM_CTX


def seed_providers() -> None:
    first_start = not db.one("SELECT id FROM providers LIMIT 1")
    # Ollama turned on after the first start (setup.sh re-run with local models) is added once per URL,
    # so a provider you deleted on purpose does not come back on every restart
    if config.OLLAMA_URL and db.get_setting("ollama_seeded_url") != config.OLLAMA_URL:
        if not db.one("SELECT id FROM providers WHERE kind = 'ollama' LIMIT 1"):
            db.run(
                "INSERT INTO providers(name, kind, base_url, created_at) VALUES (?, 'ollama', ?, ?)",
                PRESETS["ollama"]["name"], PRESETS["ollama"]["base_url"], db.now(),
            )
        db.set_setting("ollama_seeded_url", config.OLLAMA_URL)
    if not first_start:
        return
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


# Ollama native API (/api/chat): same messages and tools as OpenAI, plus options.num_ctx
def _ollama_url(p: dict) -> str:
    return re.sub(r"/v1/?$", "", p["base_url"].rstrip("/")) + "/api/chat"


def _ollama_messages(messages: list[dict]) -> list[dict]:
    names: dict[str, str] = {}
    out = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc["function"]
                names[tc.get("id", "")] = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append({"function": {"name": fn["name"], "arguments": args if isinstance(args, dict) else {}}})
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif m["role"] == "tool":
            out.append({"role": "tool", "content": m.get("content") or "", "tool_name": names.get(m.get("tool_call_id", ""), "")})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def _ollama_body(model: str, messages: list[dict], stream: bool, tools: list[dict] | None = None) -> dict:
    body: dict = {"model": model, "messages": _ollama_messages(messages), "stream": stream,
                  "options": {"num_ctx": ollama_num_ctx()}}
    if tools:
        body["tools"] = tools
    return body


class NoNativeApi(Exception):
    """The "Ollama" endpoint has no /api/chat (an OpenAI compatible server or proxy): use /v1 instead."""


def _ollama_usage(data: dict) -> dict:
    return {"prompt_tokens": data.get("prompt_eval_count") or 0, "completion_tokens": data.get("eval_count") or 0}


async def _ollama_chat(p: dict, model: str, messages: list[dict], tools: list[dict] | None) -> tuple[dict, dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(_ollama_url(p), headers=_headers(p), json=_ollama_body(model, messages, False, tools))
    if r.status_code in (404, 405):
        raise NoNativeApi
    if r.status_code >= 400:
        raise HTTPException(502, f"{p['name']} error {r.status_code}: {r.text[:400]}")
    data = r.json()
    msg = data.get("message") or {}
    out = {"role": "assistant", "content": THINK_RE.sub("", msg.get("content") or "").strip()}
    calls = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        if not fn.get("name"):
            continue
        args = fn.get("arguments")
        calls.append({"id": tc.get("id") or f"call_{i}", "type": "function",
                      "function": {"name": fn["name"], "arguments": json.dumps(args) if isinstance(args, dict) else (args or "{}")}})
    if calls:
        out["tool_calls"] = calls
    return out, _ollama_usage(data)


async def _ollama_stream(p: dict, model: str, messages: list[dict]) -> AsyncIterator[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream("POST", _ollama_url(p), headers=_headers(p), json=_ollama_body(model, messages, True)) as r:
            if r.status_code in (404, 405):
                raise NoNativeApi
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
                msg = chunk.get("message") or {}
                if msg.get("thinking"):
                    yield {"reasoning": msg["thinking"]}
                if msg.get("content"):
                    yield {"delta": msg["content"]}
                if chunk.get("done"):
                    yield {"usage": _ollama_usage(chunk)}


async def complete(ref: str | None, messages: list[dict]) -> str:
    p, model = split_ref(ref or default_model_ref())
    if p["kind"] == "ollama":
        try:
            return (await _ollama_chat(p, model, messages, None))[0]["content"]
        except NoNativeApi:
            pass
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(f"{p['base_url'].rstrip('/')}/chat/completions", headers=_headers(p), json=_body(p, model, messages, False))
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
        try:
            async for e in _ollama_stream(p, model, messages):
                if "usage" in e:
                    usage = e["usage"]
                    continue
                if "delta" in e and first_token_ms is None:
                    first_token_ms = int((time.monotonic() - started) * 1000)
                yield e
                if "error" in e:
                    return
            yield {"done": True, "usage": usage, "ms": int((time.monotonic() - started) * 1000), "first_token_ms": first_token_ms}
            return
        except NoNativeApi:
            pass  # nothing was yielded yet: the 404 arrives before any chunk
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


def recover_tool_call(message: dict, tools: list[dict] | None) -> dict:
    """Accept only an entire tool-shaped response, never JSON embedded in prose."""
    if not tools or message.get("tool_calls"):
        return message
    text = (message.get("content") or "").strip()
    block = re.fullmatch(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.S)
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    payload = block[1] if block else fence[1] if fence else text
    try:
        call = json.loads(payload)
    except ValueError:
        return message
    names = {t["function"]["name"] for t in tools}
    if (not isinstance(call, dict) or set(call) != {"name", "arguments"}
            or not isinstance(call["name"], str) or call["name"] not in names or not isinstance(call["arguments"], dict)):
        return message
    return {**message, "content": "", "tool_calls": [{"id": "call_recovered_0", "type": "function",
            "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])}}]}


async def chat(ref: str | None, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """One non-streaming turn with optional tool calling.

    Returns {"message": {...assistant message...}, "usage": {...}, "ref": str, "cost_usd": float | None}.
    OpenRouter reports the real cost of each call; other providers are priced from Settings (usage.py).
    """
    ref = ref or default_model_ref()
    p, model = split_ref(ref)
    if p["kind"] == "ollama":
        try:
            out, usage = await _ollama_chat(p, model, messages, tools)
            return {"message": recover_tool_call(out, tools), "usage": usage, "ref": ref, "model": model, "cost_usd": None}
        except NoNativeApi:
            pass
    body = _body(p, model, messages, False)
    if tools:
        body["tools"] = tools  # tool_choice left to the default ("auto"): some Claude models reject forced choice
    if "openrouter.ai" in p["base_url"]:
        body["usage"] = {"include": True}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(f"{p['base_url'].rstrip('/')}/chat/completions", headers=_headers(p), json=body)
    if r.status_code >= 400:
        raise HTTPException(502, f"{p['name']} error {r.status_code}: {r.text[:400]}")
    data = r.json()
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
    return {"message": recover_tool_call(out, tools), "usage": usage, "ref": ref, "model": model, "cost_usd": cost}


class _TextStream:
    """Hold only a possible tag suffix; inline thinking must not leak into the answer."""
    def __init__(self):
        self.pending = ""
        self.thinking = False

    def feed(self, text: str, final: bool = False) -> list[dict]:
        self.pending += text
        out = []
        while self.pending:
            tag = "</think>" if self.thinking else "<think>"
            pos = self.pending.find(tag)
            if pos >= 0:
                if pos:
                    out.append({"reasoning" if self.thinking else "delta": self.pending[:pos]})
                self.pending = self.pending[pos + len(tag):]
                self.thinking = not self.thinking
                continue
            keep = 0 if final else next((n for n in range(len(tag) - 1, 0, -1) if self.pending.endswith(tag[:n])), 0)
            text = self.pending[:-keep] if keep else self.pending
            if text:
                out.append({"reasoning" if self.thinking else "delta": text})
            self.pending = self.pending[-keep:] if keep else ""
            break
        return out


async def _chat_chunks(p: dict, model: str, messages: list[dict], tools: list[dict] | None) -> AsyncIterator[dict]:
    native = p["kind"] == "ollama"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # Only an unavailable native endpoint falls back, never an interrupted or rejected generation.
        for attempt in range(2 if native else 1):
            body = _ollama_body(model, messages, True, tools) if native else _body(p, model, messages, True)
            if tools:
                body["tools"] = tools
            if "openrouter.ai" in p["base_url"]:
                body["usage"] = {"include": True}
            url = _ollama_url(p) if native else f"{p['base_url'].rstrip('/')}/chat/completions"
            finished = False
            async with client.stream("POST", url, headers=_headers(p), json=body) as r:
                if native and r.status_code in (404, 405):
                    native = False
                    continue
                if r.status_code >= 400:
                    text = (await r.aread()).decode(errors="replace")
                    raise HTTPException(502, f"{p['name']} error {r.status_code}: {text[:400]}")
                async for line in r.aiter_lines():
                    if not line.strip() or (not native and not line.startswith("data:")):
                        continue
                    data = line if native else line[5:].strip()
                    if data == "[DONE]":
                        finished = True
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError as e:
                        raise HTTPException(502, f"{p['name']}: invalid stream data") from e
                    if chunk.get("error"):
                        raise HTTPException(502, f"{p['name']}: {str(chunk['error'])[:400]}")
                    if native:
                        msg = chunk.get("message") or {}
                        if msg.get("thinking"):
                            yield {"reasoning": msg["thinking"]}
                        if msg.get("content"):
                            yield {"delta": msg["content"]}
                        if msg.get("tool_calls"):
                            yield {"native_calls": msg["tool_calls"]}
                        if chunk.get("done"):
                            finished = True
                            yield {"usage": _ollama_usage(chunk)}
                            break
                    else:
                        if chunk.get("usage"):
                            yield {"usage": chunk["usage"]}
                        for choice in chunk.get("choices") or []:
                            if choice.get("index", 0) != 0:
                                continue
                            d = choice.get("delta") or {}
                            if d.get("reasoning") or d.get("reasoning_content"):
                                yield {"reasoning": d.get("reasoning") or d["reasoning_content"]}
                            if d.get("content"):
                                yield {"delta": d["content"]}
                            if d.get("tool_calls"):
                                yield {"fragments": d["tool_calls"]}
                            if choice.get("finish_reason"):
                                finished = True
                if not finished:
                    raise HTTPException(502, f"{p['name']}: stream ended before completion")
            return


async def chat_stream(ref: str | None, messages: list[dict], tools: list[dict] | None = None) -> AsyncIterator[dict]:
    """Text/reasoning deltas, then one assembled result with the same fields as chat()."""
    ref = ref or default_model_ref()
    p, model = split_ref(ref)
    text = _TextStream()
    content, calls, usage = [], {}, {}
    async for e in _chat_chunks(p, model, messages, tools):
        if "usage" in e:
            usage = e["usage"]
        if "reasoning" in e:
            yield {"reasoning": e["reasoning"]}
        if "delta" in e:
            for part in text.feed(e["delta"]):
                if "delta" in part:
                    content.append(part["delta"])
                yield part
        for tc in e.get("native_calls", []):
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            i = len(calls)
            calls[i] = {"id": tc.get("id") or f"call_{i}", "type": "function",
                        "function": {"name": fn.get("name", ""), "arguments": json.dumps(args) if isinstance(args, dict) else (args or "{}")}}
        for tc in e.get("fragments", []):
            i = tc.get("index", 0)
            call = calls.setdefault(i, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            call["id"] += tc.get("id") or ""
            fn = tc.get("function") or {}
            for key in ("name", "arguments"):
                call["function"][key] += fn.get(key) or ""
    for part in text.feed("", final=True):
        if "delta" in part:
            content.append(part["delta"])
        yield part
    out = {"role": "assistant", "content": "".join(content).strip()}
    if calls:
        out["tool_calls"] = []
        for i, call in sorted(calls.items()):
            if not call["function"]["name"]:
                raise HTTPException(502, f"{p['name']}: tool call has no name")
            call["id"] = call["id"] or f"call_{i}"
            call["function"]["arguments"] = call["function"]["arguments"] or "{}"
            out["tool_calls"].append(call)
    cost = usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None
    yield {"message": recover_tool_call(out, tools), "usage": usage, "ref": ref, "model": model, "cost_usd": cost}


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
