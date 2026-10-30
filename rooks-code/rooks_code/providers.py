"""Model adapters. Internally every conversation uses OpenAI Chat Completions message shapes; the Anthropic adapter
converts on the way out and back. Standard library only (urllib), so nothing to install and nothing phones home.

Each adapter returns Reply(text, tool_calls, tokens_in, tokens_out). Every HTTP request is reported to the `on_egress`
callback (method, url, outcome, ms, error) so the run receipt carries an outbound audit like Workbench's.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

# USD per million tokens [input, output]. Approximate list prices; unknown models are recorded as cost unknown (NULL),
# never guessed. Override or extend with [prices] in the config.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.0, 8.0), "gpt-4.1-mini": (0.4, 1.6), "gpt-4.1-nano": (0.1, 0.4), "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6), "o4-mini": (1.1, 4.4), "gpt-5": (1.25, 10.0), "gpt-5-mini": (0.25, 2.0),
    "claude-sonnet-4-5": (3.0, 15.0), "claude-sonnet-4": (3.0, 15.0), "claude-opus-4-1": (15.0, 75.0),
    "claude-haiku-4-5": (1.0, 5.0), "claude-3-5-haiku": (0.8, 4.0),
    "deepseek-chat": (0.27, 1.1), "deepseek-reasoner": (0.55, 2.19),
}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    bad_json: str = ""  # the raw text when the model sent arguments that were not JSON
    from_text: bool = False  # recovered from the reply's text, not a structured call


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


class ProviderError(Exception):
    pass


# Some local models (qwen2.5-coder on Ollama, for one) print a tool call as text instead of returning a structured call.
# Same rule as Workbench (workbench/backend/app/adapters.py parse_text_tool_calls): accept only well-formed calls to tools
# that were actually offered; anything else stays plain text. Also accepted here: a ```json fence inside other prose.
THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_TOOL_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_FENCE_ANY_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_DECODER = json.JSONDecoder()


def _objects(payload: str) -> list[str]:
    """Split a payload holding one or more JSON objects back to back (one per line, as small models often print)."""
    out, i = [], 0
    while i < len(payload):
        j = payload.find("{", i)
        if j < 0:
            break
        try:
            _, end = _DECODER.raw_decode(payload, j)
        except ValueError:
            return [payload]
        out.append(payload[j:end])
        i = end
    return out or [payload]


def parse_text_tool_calls(content: str, tool_names: set[str]) -> tuple[list[ToolCall], str]:
    candidates = [(m.group(0), m.group(1)) for m in _TOOL_TAG_RE.finditer(content)]
    if not candidates:
        candidates = [(m.group(0), m.group(1)) for m in _FENCE_ANY_RE.finditer(content)]
    if not candidates:
        body = content.strip()
        if body.startswith("{") and body.endswith("}"):
            candidates = [(content, body)]
    calls, rest, seen = [], content, set()
    for raw, payload in [(r, o) for r, p in candidates for o in _objects(p)]:
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
        rest = rest.replace(raw, "")
        key = (obj["name"], json.dumps(args, sort_keys=True))
        if key in seen:  # a model repeating itself: run each distinct call once
            continue
        seen.add(key)
        calls.append(ToolCall(f"text_{len(calls)}", obj["name"], args, from_text=True))
    return calls, (rest.strip() if calls else content)


def price(model: str, overrides: dict, local: bool) -> tuple[float, float] | None:
    if local:
        return (0.0, 0.0)
    if model in overrides:
        return tuple(overrides[model])  # type: ignore[return-value]
    bare = model.split("/")[-1]  # openrouter "openai/gpt-4.1-mini"
    for name in sorted(PRICES, key=len, reverse=True):  # dated variants: claude-sonnet-4-5-20250929
        if bare == name or bare.startswith(name + "-"):
            return PRICES[name]
    return None


def cost(model: str, tokens_in: int, tokens_out: int, overrides: dict, local: bool) -> float | None:
    p = price(model, overrides, local)
    return None if p is None else round((tokens_in * p[0] + tokens_out * p[1]) / 1_000_000, 6)


Egress = Callable[[str, str, str, int, str], None]


def _post(url: str, body: dict, headers: dict, on_egress: Egress | None, timeout: int = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "rooks-code", **headers})
    started, outcome, error = time.monotonic(), "ok", ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        outcome, error = f"http_{e.code}", e.reason or ""
        try:
            detail = json.loads(e.read() or b"{}")
            detail = detail.get("error", detail)
            detail = detail.get("message", detail) if isinstance(detail, dict) else detail
        except ValueError:
            detail = e.reason
        if e.code == 400 and "No model provider configured" in str(detail):
            from .config import NO_MODEL_HELP

            raise ProviderError(NO_MODEL_HELP) from None
        if e.code in (401, 403) and "127.0.0.1" not in url and "localhost" not in url:
            raise ProviderError(f"{url} refused the API key ({e.code}: {detail}). Check the key and try again.") from None
        raise ProviderError(f"{url} answered {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        outcome, error = "error", e.__class__.__name__
        reason = getattr(e, "reason", e)
        raise ProviderError(f"cannot reach {url}: {reason}") from None
    finally:
        if on_egress:
            on_egress("POST", url, outcome, int((time.monotonic() - started) * 1000), error)


class OpenAIChat:
    """OpenAI Chat Completions with tools: OpenAI, OpenRouter, Ollama, LM Studio and any compatible server."""

    def __init__(self, base_url: str, api_key: str, model: str, on_egress: Egress | None = None, headers: dict | None = None):
        self.base_url, self.api_key, self.model, self.on_egress = base_url, api_key, model, on_egress
        self.headers = headers or {}

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        body: dict = {"model": self.model, "messages": [{"role": "system", "content": system}, *messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        headers = {**self.headers, **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})}
        data = _post(self.base_url + "/chat/completions", body, headers, self.on_egress)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"unexpected reply from {self.base_url}: {str(data)[:300]}") from None
        calls = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function", {})
            raw = fn.get("arguments") or "{}"
            try:
                args, bad = (raw if isinstance(raw, dict) else json.loads(raw)), ""
            except ValueError:
                args, bad = {}, str(raw)
            calls.append(ToolCall(c.get("id") or f"call_{i}", fn.get("name", ""), args if isinstance(args, dict) else {}, bad))
        usage = data.get("usage") or {}
        return Reply(msg.get("content") or "", calls, int(usage.get("prompt_tokens") or 0),
                     int(usage.get("completion_tokens") or 0))


class Anthropic:
    """Anthropic Messages API with tools."""

    def __init__(self, base_url: str, api_key: str, model: str, on_egress: Egress | None = None, max_tokens: int = 8192):
        self.base_url, self.api_key, self.model, self.on_egress, self.max_tokens = base_url, api_key, model, on_egress, max_tokens

    @staticmethod
    def convert(messages: list[dict]) -> list[dict]:
        out: list[dict] = []

        def push(role: str, block: dict) -> None:
            if out and out[-1]["role"] == role:
                out[-1]["content"].append(block)
            else:
                out.append({"role": role, "content": [block]})

        for m in messages:
            if m["role"] == "user":
                push("user", {"type": "text", "text": m["content"]})
            elif m["role"] == "assistant":
                if m.get("content"):
                    push("assistant", {"type": "text", "text": m["content"]})
                for c in m.get("tool_calls") or []:
                    push("assistant", {"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                                       "input": json.loads(c["function"]["arguments"] or "{}")})
            elif m["role"] == "tool":
                push("user", {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]})
        return out

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        body: dict = {"model": self.model, "max_tokens": self.max_tokens, "system": system, "messages": self.convert(messages)}
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        data = _post(self.base_url + "/messages", body, headers, self.on_egress)
        if "content" not in data:
            raise ProviderError(f"unexpected reply from {self.base_url}: {str(data)[:300]}")
        text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        calls = [ToolCall(b["id"], b["name"], b.get("input") or {}) for b in data["content"] if b.get("type") == "tool_use"]
        usage = data.get("usage") or {}
        return Reply(text, calls, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))


def make(settings, on_egress: Egress | None = None):
    if settings.kind == "anthropic":
        return Anthropic(settings.base_url, settings.api_key, settings.model, on_egress)
    if settings.kind == "openai":
        return OpenAIChat(settings.base_url, settings.api_key, settings.model, on_egress, getattr(settings, "headers", None))
    raise SystemExit(f"rooks: provider kind {settings.kind!r} is not supported (use openai or anthropic)")
