"""Model adapters. Internally every conversation uses OpenAI Chat Completions message shapes; the Anthropic adapter
converts on the way out and back. Standard library only (urllib), so nothing to install and nothing phones home.

Each adapter returns Reply(text, tool_calls, tokens_in, tokens_out). Every HTTP request is reported to the `on_egress`
callback (method, url, outcome, ms, error) so the run receipt carries an outbound audit like Workbench's.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterator

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
OnToken = Callable[[str, str], None]  # (text, style): style "text" or "reasoning" (printed dimmed)


_models_cache: dict[str, tuple[float, list[str]]] = {}  # url+key -> (fetched at, ids)
_MODELS_TTL = 300.0


def list_models(base_url: str, api_key: str, headers: dict | None = None) -> list[str]:
    """GET /models, cached for 5 minutes. [] on any failure (the caller shows 'could not list')."""
    url = base_url.rstrip("/") + "/models"
    ck = url + "|" + api_key[:8]
    if ck in _models_cache and time.monotonic() - _models_cache[ck][0] < _MODELS_TTL:
        return _models_cache[ck][1]
    try:
        req = urllib.request.Request(url, headers={**(headers or {}),
                                                   **({"Authorization": f"Bearer {api_key}"} if api_key else {})})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read() or b"{}")
        ids = sorted(str(m.get("id")) for m in data.get("data", []) if m.get("id"))
    except Exception:
        ids = []
    _models_cache[ck] = (time.monotonic(), ids)
    return ids


def _want_stream(on_token: OnToken | None) -> bool:
    """Stream only to a real terminal. On a pipe or in tests on_token is never set, so a scripted reply is read once."""
    return on_token is not None and sys.stdout.isatty()


def _sse_events(stream) -> Iterator[tuple[str, str]]:
    """Yield (event, data) from a text/event-stream byte stream (http.client reads at the byte level)."""
    event, data = "message", ""
    for raw in stream:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield event, data.rstrip("\n")
            event, data = "message", ""
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data += line[5:].lstrip() + "\n"


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


def _post_stream(url: str, body: dict, headers: dict, on_egress: Egress | None, timeout: int = 600):
    """Open an SSE POST with http.client (stdlib) and return the live response. Caller closes it. Raises
    ProviderError on a non-2xx answer (the error body is read first, so it is small and complete)."""
    import http.client
    from urllib.parse import urlsplit

    u = urlsplit(url)
    host = u.hostname or "localhost"
    conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(
        host, u.port, timeout=timeout)
    payload = json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "rooks-code", **headers}
    started, outcome, error = time.monotonic(), "ok", ""
    try:
        conn.request("POST", (u.path or "/") + (("?" + u.query) if u.query else ""), body=payload, headers=hdrs)
        resp = conn.getresponse()
        if resp.status >= 400:
            detail = resp.read()[:2000].decode("utf-8", "replace")
            conn.close()
            try:
                d = json.loads(detail)
                d = d.get("error", d)
                d = d.get("message", d) if isinstance(d, dict) else d
            except ValueError:
                d = detail
            outcome, error = f"http_{resp.status}", str(d)
            raise ProviderError(f"{url} answered {resp.status}: {d}")
        return conn, resp
    except (ProviderError,):
        raise
    except (OSError, TimeoutError) as e:
        outcome, error = "error", e.__class__.__name__
        conn.close()
        raise ProviderError(f"cannot reach {url}: {e}") from None
    finally:
        if on_egress:
            on_egress("POST", url, outcome, int((time.monotonic() - started) * 1000), error)


class OpenAIChat:
    """OpenAI Chat Completions with tools: OpenAI, OpenRouter, Ollama, LM Studio and any compatible server."""

    def __init__(self, base_url: str, api_key: str, model: str, on_egress: Egress | None = None, headers: dict | None = None,
                 on_token: OnToken | None = None, stream: bool = True):
        self.base_url, self.api_key, self.model, self.on_egress = base_url, api_key, model, on_egress
        self.headers = headers or {}
        self.on_token = on_token
        self.stream = stream

    def _body(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        body: dict = {"model": self.model, "messages": [{"role": "system", "content": system}, *messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        return body

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        headers = {**self.headers, **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})}
        body = self._body(system, messages, tools)
        if self.stream and self.on_token and _want_stream(self.on_token):
            try:
                return self._complete_stream(body, headers)
            except ProviderError as e:
                if "answered" in str(e) or "stream" in str(e).lower():
                    pass  # a server that refuses stream: fall back to a plain call
                else:
                    raise
            except ValueError:  # a non-SSE body (a proxy ignoring stream:1): same fallback
                pass
        data = _post(self.base_url + "/chat/completions", body, headers, self.on_egress)
        return self._parse(data)

    def _parse(self, data: dict) -> Reply:
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

    def _complete_stream(self, body: dict, headers: dict) -> Reply:
        """SSE chat completions: text deltas printed live, reasoning dimmed, index-based tool call deltas assembled,
        usage from the final chunk (stream_options include_usage)."""
        body = {**body, "stream": True, "stream_options": {"include_usage": True}}
        conn, resp = _post_stream(self.base_url + "/chat/completions", body, headers, self.on_egress)
        text_parts: list[str] = []
        calls: dict[int, dict] = {}  # tool call index -> {id, name, args (json text)}
        tin = tout = 0
        try:
            for _event, data in _sse_events(resp):
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    u = chunk["usage"]
                    tin, tout = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content") or delta.get("reasoning"):
                        if self.on_token:
                            self.on_token(delta.get("reasoning_content") or delta.get("reasoning") or "", "reasoning")
                    if delta.get("content"):
                        text_parts.append(delta["content"])
                        if self.on_token:
                            self.on_token(delta["content"], "text")
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index", 0))
                        slot = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
        except ProviderError:
            conn.close()
            raise
        except (OSError, ValueError) as e:
            conn.close()
            raise ProviderError(f"stream from {self.base_url} broke: {e}") from None
        conn.close()
        out_calls = []
        for idx in sorted(calls):
            c = calls[idx]
            try:
                args, bad = json.loads(c["args"] or "{}"), ""
            except ValueError:
                args, bad = {}, c["args"]
            out_calls.append(ToolCall(c["id"] or f"call_{idx}", c["name"], args, bad))
        return Reply("".join(text_parts), out_calls, tin, tout)


class Anthropic:
    """Anthropic Messages API with tools."""

    def __init__(self, base_url: str, api_key: str, model: str, on_egress: Egress | None = None, max_tokens: int = 8192,
                 on_token: OnToken | None = None, stream: bool = True):
        self.base_url, self.api_key, self.model, self.on_egress, self.max_tokens = base_url, api_key, model, on_egress, max_tokens
        self.on_token = on_token
        self.stream = stream

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
        if self.stream and self.on_token and _want_stream(self.on_token):
            try:
                return self._complete_stream(body, headers)
            except ProviderError as e:
                if "answered" in str(e) or "stream" in str(e).lower():
                    pass
                else:
                    raise
            except ValueError:
                pass
        data = _post(self.base_url + "/messages", body, headers, self.on_egress)
        if "content" not in data:
            raise ProviderError(f"unexpected reply from {self.base_url}: {str(data)[:300]}")
        text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        calls = [ToolCall(b["id"], b["name"], b.get("input") or {}) for b in data["content"] if b.get("type") == "tool_use"]
        usage = data.get("usage") or {}
        return Reply(text, calls, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))

    def _complete_stream(self, body: dict, headers: dict) -> Reply:
        """Messages API events: content_block_start/delta/stop, message_delta carries the final usage. Text is printed
        live, thinking deltas are dimmed, tool_use input arrives as input_json_delta partial JSON."""
        conn, resp = _post_stream(self.base_url + "/messages", {**body, "stream": True}, headers, self.on_egress)
        text_parts: list[str] = []
        blocks: dict[int, dict] = {}  # content block index -> {type, id, name, json (text)}
        tin = tout = 0
        try:
            for event, data in _sse_events(resp):
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                etype = ev.get("type", event)
                if etype == "message_start":
                    u = (ev.get("message") or {}).get("usage") or {}
                    tin = int(u.get("input_tokens") or 0)
                elif etype == "content_block_start":
                    i = int(ev.get("index", 0))
                    b = ev.get("content_block") or {}
                    blocks[i] = {"type": b.get("type", "text"), "id": b.get("id", ""), "name": b.get("name", ""), "json": ""}
                elif etype == "content_block_delta":
                    i = int(ev.get("index", 0))
                    d = ev.get("delta") or {}
                    if d.get("type") == "text_delta":
                        text_parts.append(d.get("text", ""))
                        if self.on_token:
                            self.on_token(d.get("text", ""), "text")
                    elif d.get("type") == "thinking_delta":
                        if self.on_token:
                            self.on_token(d.get("thinking", ""), "reasoning")
                    elif d.get("type") == "input_json_delta":
                        blocks.setdefault(i, {"type": "tool_use", "id": "", "name": "", "json": ""})
                        blocks[i]["json"] += d.get("partial_json", "")
                elif etype == "message_delta":
                    u = ev.get("usage") or {}
                    tout = int(u.get("output_tokens") or 0)
                elif etype == "error":
                    raise ProviderError(f"stream error from {self.base_url}: {(ev.get('error') or {}).get('message', ev)}")
        except ProviderError:
            conn.close()
            raise
        except (OSError, ValueError) as e:
            conn.close()
            raise ProviderError(f"stream from {self.base_url} broke: {e}") from None
        conn.close()
        calls = []
        for i in sorted(blocks):
            b = blocks[i]
            if b["type"] != "tool_use":
                continue
            try:
                args, bad = json.loads(b["json"] or "{}"), ""
            except ValueError:
                args, bad = {}, b["json"]
            calls.append(ToolCall(b["id"] or f"toolu_{i}", b["name"], args, bad))
        return Reply("".join(text_parts), calls, tin, tout)


def make(settings, on_egress: Egress | None = None, on_token: OnToken | None = None):
    if settings.kind == "anthropic":
        return Anthropic(settings.base_url, settings.api_key, settings.model, on_egress, on_token=on_token)
    if settings.kind == "openai":
        return OpenAIChat(settings.base_url, settings.api_key, settings.model, on_egress,
                          getattr(settings, "headers", None), on_token=on_token)
    raise SystemExit(f"rooks: provider kind {settings.kind!r} is not supported (use openai or anthropic)")
