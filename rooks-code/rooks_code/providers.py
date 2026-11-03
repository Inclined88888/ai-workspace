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

    # argument names models carry over from other agents' tools, mapped to ours
    ALIASES = {"file_path": "path", "filepath": "path", "file": "path", "filename": "path", "cmd": "command",
               "old_string": "old_text", "new_string": "new_text", "old_str": "old_text", "new_str": "new_text"}

    def __post_init__(self):
        if isinstance(self.arguments, dict):
            for a, b in self.ALIASES.items():
                if a in self.arguments and b not in self.arguments:
                    self.arguments[b] = self.arguments.pop(a)


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


class ProviderError(Exception):
    """status: the provider's HTTP status when it answered; retry_after: seconds it asked us to wait, when it said."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after

    @property
    def transient(self) -> bool:
        """Worth trying again in a moment: rate limited or the provider is overloaded, not a bad key or request."""
        return self.status in (429, 500, 502, 503, 504, 529)


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


# Claude models (notably through the Claude Code plan) sometimes answer in Anthropic's own XML tool format instead of the
# <tool_call> JSON they were asked for: <invoke name="tool"><parameter name="p">value</parameter></invoke>, optionally
# inside <function_calls>. Parameter values are JSON when they parse (arrays, numbers), else text.
_INVOKE_RE = re.compile(r"<(?:antml:)?invoke\s+name=\"([^\"]+)\"\s*>(.*?)</(?:antml:)?invoke>", re.S)
_PARAM_RE = re.compile(r"<(?:antml:)?parameter\s+name=\"([^\"]+)\"\s*>(.*?)</(?:antml:)?parameter>", re.S)
_WRAP_RE = re.compile(r"</?(?:antml:)?function_calls>")


def _xml_calls(content: str, tool_names: set[str]) -> tuple[list[tuple[str, dict]], str]:
    found, rest, seen = [], content, set()
    for m in _INVOKE_RE.finditer(content):
        name = m.group(1)
        rest = rest.replace(m.group(0), "")
        if name not in tool_names:
            continue
        args = {}
        for p in _PARAM_RE.finditer(m.group(2)):
            raw = p.group(2).strip()
            try:
                args[p.group(1)] = json.loads(raw)
            except ValueError:
                args[p.group(1)] = raw
        key = (name, json.dumps(args, sort_keys=True))
        if key not in seen:  # the same call repeated in one reply runs once
            seen.add(key)
            found.append((name, args))
    return found, _WRAP_RE.sub("", rest).strip()


def parse_text_tool_calls(content: str, tool_names: set[str]) -> tuple[list[ToolCall], str]:
    if "invoke" in content:
        xml, left = _xml_calls(content, tool_names)
        if xml:
            return [ToolCall(f"text_{i}", n, a, from_text=True) for i, (n, a) in enumerate(xml)], left
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
OnText = Callable[[str], None]


def _http_error(e: urllib.error.HTTPError, url: str) -> ProviderError:
    try:
        after = float(e.headers.get("retry-after")) if e.headers and e.headers.get("retry-after") else None
    except ValueError:
        after = None
    try:
        detail = json.loads(e.read() or b"{}")
        detail = detail.get("error", detail)
        detail = detail.get("message", detail) if isinstance(detail, dict) else detail
    except ValueError:
        detail = e.reason
    if e.code == 400 and "No model provider configured" in str(detail):
        from .config import NO_MODEL_HELP

        return ProviderError(NO_MODEL_HELP, e.code)
    if e.code in (401, 403) and "127.0.0.1" not in url and "localhost" not in url:
        return ProviderError(f"{url} refused the API key ({e.code}: {detail}). Check the key and try again.", e.code)
    return ProviderError(f"{url} answered {e.code}: {detail}", e.code, after)


def _sse(url: str, body: dict, headers: dict, on_egress: Egress | None, timeout: int = 600):
    """Server-sent events from a streaming POST, as parsed JSON payloads. Errors raise ProviderError like _post."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                                          "User-Agent": "rooks-code", **headers})
    started, outcome, error = time.monotonic(), "ok", ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("error") and not obj.get("choices") and obj.get("type") != "error":
                    err = obj["error"]
                    raise ProviderError(f"{url}: {err.get('message', err) if isinstance(err, dict) else err}")
                yield obj
    except urllib.error.HTTPError as e:
        outcome, error = f"http_{e.code}", e.reason or ""
        raise _http_error(e, url) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        outcome, error = "error", e.__class__.__name__
        raise ProviderError(f"cannot reach {url}: {getattr(e, 'reason', e)}") from None
    finally:
        if on_egress:
            on_egress("POST", url, outcome, int((time.monotonic() - started) * 1000), error)


def _get(url: str, headers: dict, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "rooks-code", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise _http_error(e, url) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
        raise ProviderError(f"cannot list models at {url}: {getattr(e, 'reason', e)}") from None


def _parse_args(raw) -> tuple[dict, str]:
    try:
        args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except ValueError:
        return {}, str(raw)
    return (args if isinstance(args, dict) else {}), ""


def _post(url: str, body: dict, headers: dict, on_egress: Egress | None, timeout: int = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "rooks-code", **headers})
    started, outcome, error = time.monotonic(), "ok", ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        outcome, error = f"http_{e.code}", e.reason or ""
        raise _http_error(e, url) from None
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
        self.extra: dict = {}  # extra request fields, e.g. the app's reasoning_effort for a plan
        self.timeout = 600  # seconds for one answer; doctor and login use a short one

    def _headers(self) -> dict:
        return {**self.headers, **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})}

    def list_models(self) -> list[str]:
        data = _get(self.base_url + "/models", self._headers())
        return sorted(str(m.get("id", "")).removeprefix("models/") for m in data.get("data") or [] if m.get("id"))

    def list_models_detailed(self) -> list[dict]:
        """The raw list, in the server's order. The AI Workspace app adds owned_by, label, pick and effort per model."""
        return [m for m in _get(self.base_url + "/models", self._headers()).get("data") or [] if m.get("id")]

    def complete(self, system: str, messages: list[dict], tools: list[dict], on_text: OnText | None = None) -> Reply:
        body: dict = {"model": self.model, "messages": [{"role": "system", "content": system}, *messages], **self.extra}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        if on_text:
            return self._stream(body, on_text)
        data = _post(self.base_url + "/chat/completions", body, self._headers(), self.on_egress, self.timeout)
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

    def _stream(self, body: dict, on_text: OnText) -> Reply:
        """Text as it is written; tool calls arrive in pieces by index and are joined before they are used."""
        body = {**body, "stream": True, "stream_options": {"include_usage": True}}
        text, parts, usage = [], {}, {}
        for chunk in _sse(self.base_url + "/chat/completions", body, self._headers(), self.on_egress, self.timeout):
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                d = choice.get("delta") or {}
                if d.get("content"):
                    text.append(d["content"])
                    on_text(d["content"])
                for tc in d.get("tool_calls") or []:
                    slot = parts.setdefault(tc.get("index", len(parts)), {"id": "", "name": "", "args": ""})
                    fn = tc.get("function") or {}
                    slot["id"] = tc.get("id") or slot["id"]
                    slot["name"] = fn.get("name") or slot["name"]
                    a = fn.get("arguments")
                    slot["args"] += a if isinstance(a, str) else (json.dumps(a) if a else "")
        calls = []
        for i in sorted(parts):
            args, bad = _parse_args(parts[i]["args"] or "{}")
            calls.append(ToolCall(parts[i]["id"] or f"call_{i}", parts[i]["name"], args, bad))
        return Reply("".join(text), calls, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))


class Anthropic:
    """Anthropic Messages API with tools."""

    def __init__(self, base_url: str, api_key: str, model: str, on_egress: Egress | None = None, max_tokens: int = 8192):
        self.base_url, self.api_key, self.model, self.on_egress, self.max_tokens = base_url, api_key, model, on_egress, max_tokens
        self.timeout = 600

    @staticmethod
    def convert(messages: list[dict]) -> list[dict]:
        out: list[dict] = []

        def push(role: str, block: dict) -> None:
            if out and out[-1]["role"] == role:
                out[-1]["content"].append(block)
            else:
                out.append({"role": role, "content": [block]})

        for m in messages:
            if m["role"] == "user" and isinstance(m["content"], list):  # text + images (images.py)
                for p in m["content"]:
                    url = (p.get("image_url") or {}).get("url", "") if p.get("type") == "image_url" else ""
                    if url.startswith("data:") and ";base64," in url:
                        mime, data = url[5:].split(";base64,", 1)
                        push("user", {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
                    elif p.get("type") == "text":
                        push("user", {"type": "text", "text": p.get("text") or " "})
            elif m["role"] == "user":
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

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}

    def list_models(self) -> list[str]:
        data = _get(self.base_url + "/models?limit=1000", self._headers())  # pages of 20 by default
        return sorted(str(m.get("id", "")) for m in data.get("data") or [] if m.get("id"))

    def body(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """Prompt caching: system prompt, tool list and the conversation so far are cache breakpoints (3 of the 4
        allowed), so each step rereads them at the cache price instead of paying full input price again."""
        msgs = self.convert(messages)
        if msgs and msgs[-1]["content"]:
            msgs[-1]["content"][-1] = {**msgs[-1]["content"][-1], "cache_control": {"type": "ephemeral"}}
        body: dict = {"model": self.model, "max_tokens": self.max_tokens, "messages": msgs,
                      "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]}
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
            body["tools"][-1] = {**body["tools"][-1], "cache_control": {"type": "ephemeral"}}
        return body

    @staticmethod
    def _tokens_in(u: dict) -> int:
        return int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)

    def complete(self, system: str, messages: list[dict], tools: list[dict], on_text: OnText | None = None) -> Reply:
        body = self.body(system, messages, tools)
        if on_text:
            return self._stream(body, on_text)
        data = _post(self.base_url + "/messages", body, self._headers(), self.on_egress, self.timeout)
        if "content" not in data:
            raise ProviderError(f"unexpected reply from {self.base_url}: {str(data)[:300]}")
        text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        calls = [ToolCall(b["id"], b["name"], b.get("input") or {}) for b in data["content"] if b.get("type") == "tool_use"]
        usage = data.get("usage") or {}
        return Reply(text, calls, self._tokens_in(usage), int(usage.get("output_tokens") or 0))

    def _stream(self, body: dict, on_text: OnText) -> Reply:
        text, blocks, tin, tout = [], {}, 0, 0
        for ev in _sse(self.base_url + "/messages", {**body, "stream": True}, self._headers(), self.on_egress, self.timeout):
            t = ev.get("type")
            if t == "message_start":
                tin = self._tokens_in((ev.get("message") or {}).get("usage") or {})
            elif t == "content_block_start":
                b = ev.get("content_block") or {}
                if b.get("type") == "tool_use":
                    blocks[ev.get("index", len(blocks))] = {"id": b.get("id", ""), "name": b.get("name", ""), "json": ""}
            elif t == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    text.append(d["text"])
                    on_text(d["text"])
                elif d.get("type") == "input_json_delta" and ev.get("index") in blocks:
                    blocks[ev["index"]]["json"] += d.get("partial_json", "")
            elif t == "message_delta":
                tout = int((ev.get("usage") or {}).get("output_tokens") or 0)
            elif t == "error":
                raise ProviderError(f"{self.base_url}: {(ev.get('error') or {}).get('message', ev)}")
        calls = []
        for i in sorted(blocks):
            args, bad = _parse_args(blocks[i]["json"] or "{}")
            calls.append(ToolCall(blocks[i]["id"], blocks[i]["name"], args, bad))
        return Reply("".join(text), calls, tin, tout)


def make(settings, on_egress: Egress | None = None):
    if settings.kind == "anthropic":
        return Anthropic(settings.base_url, settings.api_key, settings.model, on_egress)
    if settings.kind == "openai":
        m = OpenAIChat(settings.base_url, settings.api_key, settings.model, on_egress, getattr(settings, "headers", None))
        if settings.provider == "workbench" and getattr(settings, "effort", ""):
            m.extra = {"reasoning_effort": settings.effort}  # only the app reads it (for plans); other servers may reject it
        return m
    raise SystemExit(f"rooks: provider kind {settings.kind!r} is not supported (use openai or anthropic)")
