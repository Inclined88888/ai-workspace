"""OpenAI compatible client for every provider (Ollama, OpenAI, Anthropic, OpenRouter, any /v1 endpoint).

Model references are "<provider_id>/<model>", e.g. "1/qwen3:4b" or "3/openai/gpt-4o".
"""

import json
import re
import time
from typing import AsyncIterator

import httpx
from fastapi import HTTPException

from . import config, db
from .security import decrypt, encrypt

PRESETS = {
    "ollama": {"name": "Ollama (local)", "base_url": f"{config.OLLAMA_URL}/v1"},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    "anthropic": {"name": "Anthropic", "base_url": "https://api.anthropic.com/v1"},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
}

THINK_RE = re.compile(r"<think>.*?</think>", re.S)
TIMEOUT = httpx.Timeout(connect=10, read=300, write=30, pool=10)


def seed_providers() -> None:
    if db.one("SELECT id FROM providers LIMIT 1"):
        return
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
    p = db.one("SELECT id FROM providers WHERE enabled = 1 ORDER BY id LIMIT 1")
    if not p:
        raise HTTPException(400, "No model provider configured. Add one in Settings.")
    models = list_models_for(p["id"])
    if not models:
        raise HTTPException(400, "No models available. Pick a default model in Settings.")
    return models[0]["ref"]


_model_cache: dict[int, tuple[float, list[dict]]] = {}


def list_models_for(pid: int, fresh: bool = False) -> list[dict]:
    p = _provider(pid)
    cached = _model_cache.get(pid)
    if cached and not fresh and cached[0] > time.time() - 120:
        return cached[1]
    names: list[str] = []
    try:
        r = httpx.get(f"{p['base_url'].rstrip('/')}/models", headers=_headers(p), timeout=15)
        r.raise_for_status()
        names = sorted({m["id"] for m in r.json().get("data", []) if m.get("id")})
    except (httpx.HTTPError, ValueError, KeyError):
        names = []
    manual = [m.strip() for m in p["models"].split(",") if m.strip()]
    names = sorted(set(names) | set(manual))
    out = [{"ref": f"{pid}/{n}", "model": n, "provider": p["name"], "provider_id": pid} for n in names]
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
