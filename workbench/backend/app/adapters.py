"""The provider adapter layer (R1, item 88). Nothing above this module knows which backend answers.

A connection is two independent choices:
  auth_kind  where the credential comes from: api_key | oauth_subscription | session_scraping_forbidden |
             local_ollama | openai_compatible
  protocol   the wire format: chat_completions | responses | anthropic_messages | ollama_native

Callers speak one message shape (OpenAI chat style: system/user/assistant with tool_calls/tool) and get one result
shape back: {"message": {...}, "usage": {"prompt_tokens", "completion_tokens", "cost"?}}. Each protocol converts both
ways. Wire formats follow the vendors' documentation and their generated SDK types (docs/design/0002-adapters.md).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import AsyncIterator

import httpx
from fastapi import HTTPException

from . import db
from .security import decrypt

AUTH_KINDS = ("api_key", "oauth_subscription", "session_scraping_forbidden", "local_ollama", "openai_compatible")
PROTOCOLS = ("responses", "chat_completions", "anthropic_messages", "ollama_native")
ALLOWED = {
    "api_key": ("chat_completions", "responses", "anthropic_messages"),
    "oauth_subscription": ("responses",),  # Sign in with ChatGPT: plan usage covers Responses API requests only
    "session_scraping_forbidden": (),
    "local_ollama": ("ollama_native",),
    "openai_compatible": ("chat_completions",),
}
# D7: shown to the user wherever such a connection would be created or used
R5_REFUSAL = ("This connection type is refused under rule R5 (legitimate only): reusing a consumer web session or its "
              "cookies breaks the provider's terms and gets accounts banned. Connect with an API key, a local model, or "
              "the provider's official sign-in instead.")

TIMEOUT = httpx.Timeout(connect=10, read=300, write=30, pool=10)
# Ollama's OpenAI endpoint (/v1) ignores num_ctx and runs at the server default (4096 tokens), silently cutting the
# start of long prompts. Ollama is therefore spoken to natively, with an explicit window.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
# Local models on a CPU need minutes per answer (qwen3:4b on 4 cores ran past 300 s)
OLLAMA_TIMEOUT = httpx.Timeout(connect=10, read=float(os.getenv("OLLAMA_TIMEOUT_S", "900")), write=30, pool=10)
ANTHROPIC_VERSION = "2023-06-01"  # the version in Anthropic's own Messages API example
ANTHROPIC_MAX_TOKENS = 8192
CHARS_PER_TOKEN = 3
CLOUD_CONTEXT_CHARS = 300_000
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def validate(auth_kind: str, protocol: str) -> None:
    """Raise 400 for a combination that cannot exist. session_scraping_forbidden always refuses (R5)."""
    if auth_kind == "session_scraping_forbidden":
        raise HTTPException(400, R5_REFUSAL)
    if auth_kind not in AUTH_KINDS:
        raise HTTPException(400, f"Unknown auth_kind {auth_kind!r}. Use one of: {', '.join(AUTH_KINDS)}")
    if protocol not in ALLOWED[auth_kind]:
        raise HTTPException(400, f"{auth_kind} connections speak {', '.join(ALLOWED[auth_kind])}, not {protocol!r}")


# Credentials, by auth_kind
def credential(conn: dict) -> str:
    """The secret to send for this connection, or raise with the reason it cannot be used."""
    kind = conn["auth_kind"]
    if kind == "session_scraping_forbidden":
        raise HTTPException(403, R5_REFUSAL)
    if kind == "local_ollama":
        return ""
    if kind == "oauth_subscription":
        return _oauth_access_token(conn)
    return decrypt(conn["secret_enc"])  # api_key, openai_compatible (key optional)


def _oauth_access_token(conn: dict) -> str:
    """Access token from the connection's token store. Refresh is item 101; until then an expired token needs sign-in."""
    try:
        tokens = json.loads(decrypt(conn["secret_enc"]) or "{}")
    except ValueError:
        tokens = {}
    expires = tokens.get("expires_at") or 0
    if tokens.get("access_token") and expires > time.time() + 30:
        return tokens["access_token"]
    db.run("UPDATE connections SET state = 'needs_auth', last_error = ? WHERE id = ?",
           "Sign-in expired or missing", conn["id"])
    raise HTTPException(401, f"{conn['name']} needs you to sign in again (Settings → Model providers).")


# Message helpers shared by protocols
def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") in ("text", "input_text"))


def _images(content) -> list[tuple[str, str]]:
    """(media_type, base64) for every data: URL image in OpenAI-shaped content parts."""
    out = []
    for p in content if isinstance(content, list) else []:
        url = ((p.get("image_url") or {}).get("url") if isinstance(p.get("image_url"), dict) else p.get("image_url")) or ""
        m = re.match(r"data:(image/[\w.+-]+);base64,(.+)", url, re.S)
        if p.get("type") == "image_url" and m:
            out.append((m.group(1), m.group(2)))
    return out


def _system_and_rest(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n\n".join(_text(m["content"]) for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def _call(cid: str, name: str, args) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}


def _json_args(raw: str) -> dict:
    try:
        v = json.loads(raw or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


async def _post(conn: dict, url: str, body: dict, headers: dict, timeout: httpx.Timeout) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as e:
        raise HTTPException(504, f"{conn['name']} did not answer within {int(timeout.read or 0)} s") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{conn['name']} unreachable: {e.__class__.__name__}") from e
    if r.status_code >= 400:
        raise HTTPException(502, f"{conn['name']} error {r.status_code}: {r.text[:400]}")
    return r


async def _sse(conn: dict, url: str, body: dict, headers: dict, timeout: httpx.Timeout) -> AsyncIterator[dict]:
    """Server-sent events as parsed JSON payloads. An HTTP error becomes one {"error": ...} item."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=body) as r:
            if r.status_code >= 400:
                text = (await r.aread()).decode(errors="replace")
                yield {"error": f"{conn['name']} error {r.status_code}: {text[:400]}"}
                return
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except ValueError:
                    continue


class Protocol:
    name = ""
    timeout = TIMEOUT

    def headers(self, conn: dict, secret: str) -> dict:
        return {"Authorization": f"Bearer {secret or 'none'}", "Content-Type": "application/json"}

    def models_url(self, conn: dict) -> str:
        return conn["base_url"].rstrip("/") + "/models"

    def parse_models(self, data: dict) -> list[str]:
        return [m["id"].removeprefix("models/") for m in data.get("data", []) if m.get("id")]

    def context_chars(self, conn: dict) -> int:
        return CLOUD_CONTEXT_CHARS

    async def chat(self, conn: dict, secret: str, model: str, messages: list[dict], tools: list[dict] | None,
                   schema: dict | None = None) -> dict:
        """schema: {"name", "schema"} asks for JSON matching it, by the protocol's own structured output mechanism."""
        raise NotImplementedError

    def stream(self, conn: dict, secret: str, model: str, messages: list[dict]) -> AsyncIterator[dict]:
        raise NotImplementedError


class ChatCompletions(Protocol):
    """OpenAI Chat Completions, spoken by OpenAI, OpenRouter, Gemini's OpenAI endpoint, OpenCode, LM Studio, vLLM, ..."""
    name = "chat_completions"

    def headers(self, conn: dict, secret: str) -> dict:
        h = super().headers(conn, secret)
        if "api.anthropic.com" in conn["base_url"]:  # Anthropic's OpenAI compatibility layer (connections from before v2)
            h.update({"x-api-key": secret, "anthropic-version": ANTHROPIC_VERSION})
        return h

    def models_url(self, conn: dict) -> str:
        base = conn["base_url"].rstrip("/")
        if "opencode.ai/inference" in base:  # chat is served under /openai/v1, the model list under /v1
            return base.replace("/openai/v1", "/v1") + "/models"
        return base + "/models"

    def _body(self, conn: dict, model: str, messages: list[dict], stream: bool) -> dict:
        body: dict = {"model": model, "messages": messages, "stream": stream}
        if "api.anthropic.com" in conn["base_url"]:
            body["max_tokens"] = ANTHROPIC_MAX_TOKENS
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body  # temperature is deliberately not sent: recent Claude models reject values below 1

    async def chat(self, conn, secret, model, messages, tools, schema=None):
        body = self._body(conn, model, messages, False)
        if tools:
            body["tools"] = tools
        if schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": schema["name"], "schema": schema["schema"], "strict": True}}
        if "openrouter.ai" in conn["base_url"]:
            body["usage"] = {"include": True}
        data = (await _post(conn, f"{conn['base_url'].rstrip('/')}/chat/completions", body, self.headers(conn, secret), self.timeout)).json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        calls = [_call(tc.get("id") or f"call_{i}", tc["function"]["name"], tc["function"].get("arguments") or "{}")
                 for i, tc in enumerate(msg.get("tool_calls") or []) if (tc.get("function") or {}).get("name")]
        u = data.get("usage") or {}
        return {"message": {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls},
                "usage": {"prompt_tokens": u.get("prompt_tokens") or 0, "completion_tokens": u.get("completion_tokens") or 0,
                          **({"cost": u["cost"]} if isinstance(u.get("cost"), (int, float)) else {})}}

    async def stream(self, conn, secret, model, messages):
        usage: dict = {}
        async for chunk in _sse(conn, f"{conn['base_url'].rstrip('/')}/chat/completions", self._body(conn, model, messages, True),
                                self.headers(conn, secret), self.timeout):
            if "error" in chunk and not chunk.get("choices"):
                yield {"error": chunk["error"] if isinstance(chunk["error"], str) else json.dumps(chunk["error"])[:400]}
                return
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                d = choice.get("delta") or {}
                thought = d.get("reasoning") or d.get("reasoning_content")
                if thought:
                    yield {"reasoning": thought}
                if d.get("content"):
                    yield {"delta": d["content"]}
        yield {"done": True, "usage": usage}


class OllamaNative(Protocol):
    """Ollama's own /api/chat, with an explicit context window (see OLLAMA_NUM_CTX)."""
    name = "ollama_native"
    timeout = OLLAMA_TIMEOUT

    def root(self, conn: dict) -> str:
        return conn["base_url"].rstrip("/").removesuffix("/v1")

    def headers(self, conn, secret):
        return {"Content-Type": "application/json"}

    def models_url(self, conn):
        return self.root(conn) + "/api/tags"

    def parse_models(self, data):
        return [m["name"] for m in data.get("models", []) if m.get("name")]

    def context_chars(self, conn):
        return int(OLLAMA_NUM_CTX * CHARS_PER_TOKEN * 0.75)

    def convert(self, messages: list[dict]) -> list[dict]:
        names: dict[str, str] = {}
        out = []
        for m in messages:
            o = {"role": m["role"], "content": _text(m.get("content"))}
            images = _images(m.get("content"))
            if images:
                o["images"] = [b64 for _, b64 in images]
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    names[tc["id"]] = tc["function"]["name"]
                o["tool_calls"] = [{"function": {"name": tc["function"]["name"], "arguments": _json_args(tc["function"].get("arguments"))}}
                                   for tc in m["tool_calls"]]
            if m["role"] == "tool":
                o = {"role": "tool", "content": m["content"], "tool_name": names.get(m.get("tool_call_id", ""), "")}
            out.append(o)
        return out

    def _body(self, model, messages, stream, tools=None, schema=None):
        body: dict = {"model": model, "messages": self.convert(messages), "stream": stream, "options": {"num_ctx": OLLAMA_NUM_CTX}}
        if tools:
            body["tools"] = tools
        if schema:
            body["format"] = schema["schema"]  # Ollama structured outputs: a JSON schema in "format"
        return body

    @staticmethod
    def _usage(data: dict) -> dict:
        return {"prompt_tokens": data.get("prompt_eval_count") or 0, "completion_tokens": data.get("eval_count") or 0}

    async def chat(self, conn, secret, model, messages, tools, schema=None):
        data = (await _post(conn, self.root(conn) + "/api/chat", self._body(model, messages, False, tools, schema), self.headers(conn, secret), self.timeout)).json()
        if (data.get("prompt_eval_count") or 0) >= OLLAMA_NUM_CTX:
            raise HTTPException(502, f"The prompt filled the whole {OLLAMA_NUM_CTX} token window of {model}, so Ollama cut part of it. "
                                     "Start a new conversation, or raise OLLAMA_NUM_CTX if you have the memory.")
        msg = data.get("message") or {}
        calls = [_call(f"call_{i}", tc["function"]["name"], tc["function"].get("arguments") or {})
                 for i, tc in enumerate(msg.get("tool_calls") or []) if (tc.get("function") or {}).get("name")]
        return {"message": {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}, "usage": self._usage(data)}

    async def stream(self, conn, secret, model, messages):
        usage: dict = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", self.root(conn) + "/api/chat", json=self._body(model, messages, True)) as r:
                if r.status_code >= 400:
                    text = (await r.aread()).decode(errors="replace")
                    yield {"error": f"{conn['name']} error {r.status_code}: {text[:400]}"}
                    return
                async for line in r.aiter_lines():
                    try:
                        chunk = json.loads(line) if line.strip() else None
                    except ValueError:
                        continue
                    if not chunk:
                        continue
                    if chunk.get("error"):
                        yield {"error": f"{conn['name']}: {chunk['error']}"}
                        return
                    m = chunk.get("message") or {}
                    if m.get("thinking"):
                        yield {"reasoning": m["thinking"]}
                    if m.get("content"):
                        yield {"delta": m["content"]}
                    if chunk.get("done"):
                        usage = self._usage(chunk)
        if usage.get("prompt_tokens", 0) >= OLLAMA_NUM_CTX:
            yield {"error": f"The prompt filled the whole {OLLAMA_NUM_CTX} token window, so Ollama cut part of it."}
            return
        yield {"done": True, "usage": usage}


class Responses(Protocol):
    """OpenAI Responses API. The only protocol a ChatGPT plan (Sign in with ChatGPT) can be used through."""
    name = "responses"

    def convert(self, messages: list[dict]) -> tuple[str, list[dict]]:
        system, rest = _system_and_rest(messages)
        items: list[dict] = []
        for m in rest:
            if m["role"] == "tool":
                items.append({"type": "function_call_output", "call_id": m.get("tool_call_id", ""), "output": m["content"]})
            elif m["role"] == "assistant":
                if _text(m.get("content")):
                    items.append({"role": "assistant", "content": _text(m["content"])})
                items += [{"type": "function_call", "call_id": tc["id"], "name": tc["function"]["name"],
                           "arguments": tc["function"].get("arguments") or "{}"} for tc in m.get("tool_calls") or []]
            else:
                parts = [{"type": "input_text", "text": _text(m.get("content"))}] if _text(m.get("content")) else []
                parts += [{"type": "input_image", "image_url": f"data:{mt};base64,{b64}", "detail": "auto"} for mt, b64 in _images(m.get("content"))]
                items.append({"role": "user", "content": parts})
        return system, items

    def _body(self, model, messages, stream, tools=None, schema=None):
        system, items = self.convert(messages)
        body: dict = {"model": model, "input": items, "store": False, "stream": stream}  # store=False: nothing kept server side
        if schema:
            body["text"] = {"format": {"type": "json_schema", "name": schema["name"], "schema": schema["schema"], "strict": True}}
        if system:
            body["instructions"] = system
        if tools:
            body["tools"] = [{"type": "function", "name": t["function"]["name"], "description": t["function"].get("description", ""),
                              "parameters": t["function"].get("parameters") or {"type": "object", "properties": {}}, "strict": False}
                             for t in tools]
        return body

    @staticmethod
    def _usage(u: dict | None) -> dict:
        u = u or {}
        return {"prompt_tokens": u.get("input_tokens") or 0, "completion_tokens": u.get("output_tokens") or 0}

    async def chat(self, conn, secret, model, messages, tools, schema=None):
        data = (await _post(conn, conn["base_url"].rstrip("/") + "/responses", self._body(model, messages, False, tools, schema),
                            self.headers(conn, secret), self.timeout)).json()
        text, calls = "", []
        for item in data.get("output") or []:
            if item.get("type") == "message":
                text += "".join(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text")
            elif item.get("type") == "function_call" and item.get("name"):
                calls.append(_call(item.get("call_id") or f"call_{len(calls)}", item["name"], item.get("arguments") or "{}"))
        return {"message": {"role": "assistant", "content": text, "tool_calls": calls}, "usage": self._usage(data.get("usage"))}

    async def stream(self, conn, secret, model, messages):
        usage: dict = {}
        async for ev in _sse(conn, conn["base_url"].rstrip("/") + "/responses", self._body(model, messages, True),
                             self.headers(conn, secret), self.timeout):
            t = ev.get("type")
            if "error" in ev and not t:
                yield ev
                return
            if t == "response.output_text.delta":
                yield {"delta": ev.get("delta", "")}
            elif t == "response.completed":
                usage = self._usage((ev.get("response") or {}).get("usage"))
            elif t in ("error", "response.failed"):
                yield {"error": json.dumps(ev.get("error") or (ev.get("response") or {}).get("error") or ev)[:400]}
                return
        yield {"done": True, "usage": usage}


class AnthropicMessages(Protocol):
    """Anthropic's native Messages API (system is top level, tools use input_schema, results are tool_result blocks)."""
    name = "anthropic_messages"

    def headers(self, conn, secret):
        return {"x-api-key": secret, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}

    def convert(self, messages: list[dict]) -> tuple[str, list[dict]]:
        system, rest = _system_and_rest(messages)
        out: list[dict] = []

        def add(role: str, blocks: list[dict]) -> None:
            if not blocks:
                return
            if out and out[-1]["role"] == role:  # consecutive same-role turns become one (tool results after tool calls)
                out[-1]["content"] += blocks
            else:
                out.append({"role": role, "content": blocks})

        for m in rest:
            if m["role"] == "tool":
                add("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""), "content": m["content"]}])
            elif m["role"] == "assistant":
                blocks = [{"type": "text", "text": _text(m.get("content"))}] if _text(m.get("content")) else []
                blocks += [{"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": _json_args(tc["function"].get("arguments"))}
                           for tc in m.get("tool_calls") or []]
                add("assistant", blocks)
            else:
                blocks = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}} for mt, b64 in _images(m.get("content"))]
                if _text(m.get("content")):
                    blocks.append({"type": "text", "text": _text(m["content"])})
                add("user", blocks)
        return system, out

    @staticmethod
    def schema_tool(schema: dict) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]", "_", schema["name"])[:128] or "respond"

    def _body(self, model, messages, stream, tools=None, schema=None):
        system, msgs = self.convert(messages)
        body: dict = {"model": model, "max_tokens": ANTHROPIC_MAX_TOKENS, "messages": msgs, "stream": stream}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                              "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}} for t in tools]
        if schema:  # the Messages API has no response_format: a forced tool with the schema as input is the documented route
            name = self.schema_tool(schema)
            body["tools"] = [*body.get("tools", []), {"name": name, "description": "Give the answer in this structure.",
                                                       "input_schema": schema["schema"]}]
            body["tool_choice"] = {"type": "tool", "name": name}
        return body

    async def chat(self, conn, secret, model, messages, tools, schema=None):
        data = (await _post(conn, conn["base_url"].rstrip("/") + "/messages", self._body(model, messages, False, tools, schema),
                            self.headers(conn, secret), self.timeout)).json()
        text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        calls = [_call(b["id"], b["name"], b.get("input") or {}) for b in data.get("content") or [] if b.get("type") == "tool_use"]
        if schema:  # the forced tool's input is the structured answer
            forced = [c for c in calls if c["function"]["name"] == self.schema_tool(schema)]
            text, calls = (forced[0]["function"]["arguments"], []) if forced else (text, calls)
        u = data.get("usage") or {}
        return {"message": {"role": "assistant", "content": text, "tool_calls": calls},
                "usage": {"prompt_tokens": u.get("input_tokens") or 0, "completion_tokens": u.get("output_tokens") or 0}}

    async def stream(self, conn, secret, model, messages):
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        async for ev in _sse(conn, conn["base_url"].rstrip("/") + "/messages", self._body(model, messages, True),
                             self.headers(conn, secret), self.timeout):
            t = ev.get("type")
            if "error" in ev and not t:
                yield ev
                return
            if t == "message_start":
                usage["prompt_tokens"] = ((ev.get("message") or {}).get("usage") or {}).get("input_tokens") or 0
            elif t == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                yield {"delta": ev["delta"].get("text", "")}
            elif t == "message_delta":
                usage["completion_tokens"] = (ev.get("usage") or {}).get("output_tokens") or 0
            elif t == "error":
                yield {"error": json.dumps(ev.get("error") or ev)[:400]}
                return
        yield {"done": True, "usage": usage}


_PROTOCOLS: dict[str, Protocol] = {p.name: p for p in (ChatCompletions(), OllamaNative(), Responses(), AnthropicMessages())}


class Adapter:
    """The one interface every caller uses, whatever the connection (R1)."""

    def __init__(self, conn: dict):
        self.conn = conn
        self.protocol = _PROTOCOLS.get(conn["protocol"])
        if conn["auth_kind"] != "session_scraping_forbidden" and (self.protocol is None or conn["protocol"] not in ALLOWED.get(conn["auth_kind"], ())):
            raise HTTPException(400, f"Connection {conn['name']} has an invalid auth_kind/protocol pair")

    @property
    def auth_kind(self) -> str:
        return self.conn["auth_kind"]

    def describe(self) -> tuple[str, str]:
        return self.conn["auth_kind"], self.conn["protocol"]

    def is_free(self, model: str) -> bool:
        return self.auth_kind == "local_ollama" or ("openrouter.ai" in self.conn["base_url"] and model.endswith(":free"))

    def context_chars(self) -> int:
        if self.auth_kind == "session_scraping_forbidden":
            raise HTTPException(403, R5_REFUSAL)
        return self.protocol.context_chars(self.conn)

    def list_models(self) -> list[str]:
        """Models the endpoint lists plus the ones typed in by hand. A failed listing is not an error: manual ones remain."""
        secret = credential(self.conn) if self.auth_kind != "oauth_subscription" else ""
        manual = [m.strip() for m in (self.conn.get("models") or "").split(",") if m.strip()]
        names: list[str] = []
        if self.auth_kind != "oauth_subscription":  # plan tokens are for Responses requests only; models are typed in
            try:
                r = httpx.get(self.protocol.models_url(self.conn), headers=self.protocol.headers(self.conn, secret), timeout=15)
                r.raise_for_status()
                names = [n for n in self.protocol.parse_models(r.json()) if keep_model(self.conn["base_url"], n)]
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                names = []
        return sorted(set(names) | set(manual))

    async def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None, schema: dict | None = None) -> dict:
        """schema: {"name", "schema"} for structured output; the answer's content is then JSON text matching it."""
        secret = credential(self.conn)
        res = await self.protocol.chat(self.conn, secret, model, messages, tools, schema)
        msg = res["message"]
        msg["content"] = THINK_RE.sub("", msg.get("content") or "").strip()
        names = {t["function"]["name"] for t in tools or []}
        calls = msg.pop("tool_calls", [])  # structured calls pass as they are; an unknown name gets "Unknown tool" from run_tool
        if not calls and names and msg["content"]:  # a call printed as text, recovered only when valid (llm.parse_text_tool_calls)
            calls, msg["content"] = parse_text_tool_calls(msg["content"], names)
            if calls:
                msg["parsed_from_text"] = True
        if calls:
            msg["tool_calls"] = calls
        return res

    async def stream(self, model: str, messages: list[dict]) -> AsyncIterator[dict]:
        secret = credential(self.conn)
        async for e in self.protocol.stream(self.conn, secret, model, messages):
            yield e


def for_connection(conn: dict) -> Adapter:
    return Adapter(conn)


# Model list filters for hosts that list models the chat endpoint cannot serve
_OPENCODE_SKIP = re.compile(r"^(claude-|gpt-|grok-|gemini-|muse-|jev-|test$|big-pickle$)|-free$")
_ZEN_SKIP = re.compile(r"^(qwen3\.8-flash|qwen3\.6-plus|qwen3\.5-plus)$")
_GO_SKIP = re.compile(r"^(qwen|minimax-m3$)")
_GEMINI_SKIP = re.compile(r"(tts|embedding|live|transcribe|translate|robotics|imagen|veo|aqa|image|audio|lyria)")


def keep_model(base_url: str, model: str) -> bool:
    if "opencode.ai/inference" in base_url:
        extra = _GO_SKIP if "/inference/go/" in base_url else _ZEN_SKIP
        return not (_OPENCODE_SKIP.search(model) or extra.search(model))
    if "generativelanguage.googleapis.com" in base_url:
        return model.startswith(("gemini", "gemma")) and not _GEMINI_SKIP.search(model)
    return True


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
        calls.append(_call(f"text_{len(calls)}", obj["name"], args))
        rest = rest.replace(raw, "")
    return calls, (rest.strip() if calls else content)
