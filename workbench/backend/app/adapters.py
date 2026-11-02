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

import contextvars
import json
import os
import re
import time
from typing import AsyncIterator

import httpx
from fastapi import HTTPException

from . import db
from .security import decrypt

AUTH_KINDS = ("api_key", "oauth_subscription", "session_scraping_forbidden", "local_ollama", "openai_compatible", "cli_subscription")
PROTOCOLS = ("responses", "chat_completions", "anthropic_messages", "ollama_native", "cli_plan")
ALLOWED = {
    "api_key": ("chat_completions", "responses", "anthropic_messages"),
    "oauth_subscription": ("responses",),  # Sign in with ChatGPT: plan usage covers Responses API requests only
    "session_scraping_forbidden": (),
    "local_ollama": ("ollama_native",),
    "openai_compatible": ("chat_completions",),
    # The user's own signed-in Claude Code or Codex CLI, run on their computer (cli_agents.py explains why this shape)
    "cli_subscription": ("cli_plan",),
}
# D7: shown to the user wherever such a connection would be created or used
R5_REFUSAL = ("This connection type is refused under rule R5 (legitimate only): reusing a consumer web session or its "
              "cookies breaks the provider's terms and gets accounts banned. Connect with an API key, a local model, or "
              "the provider's official sign-in instead.")

TIMEOUT = httpx.Timeout(connect=10, read=300, write=30, pool=10)
LIST_TIMEOUT_S = 8  # a model list should be quick; a dead provider must not hold up every answer for long
# Ollama's OpenAI endpoint (/v1) ignores num_ctx and runs at the server default (4096 tokens), silently cutting the
# start of long prompts. Ollama is therefore spoken to natively, with an explicit window.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
# A cap on one answer: small local models sometimes repeat themselves until the timeout (seen with qwen2.5 7B)
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "4096"))
# Local models on a CPU need minutes per answer (qwen3:4b on 4 cores ran past 300 s)
OLLAMA_TIMEOUT = httpx.Timeout(connect=10, read=float(os.getenv("OLLAMA_TIMEOUT_S", "900")), write=30, pool=10)
ANTHROPIC_VERSION = "2023-06-01"  # the version in Anthropic's own Messages API example
ANTHROPIC_MAX_TOKENS = 8192
CHARS_PER_TOKEN = 3
CLOUD_CONTEXT_CHARS = 300_000  # when the model catalog does not know the model
MIN_CONTEXT_CHARS = 12_000
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def validate(auth_kind: str, protocol: str) -> None:
    """Raise 400 for a combination that cannot exist. session_scraping_forbidden always refuses (R5)."""
    if auth_kind == "session_scraping_forbidden":
        raise HTTPException(400, R5_REFUSAL)
    if auth_kind not in AUTH_KINDS:
        raise HTTPException(400, f"Unknown auth_kind {auth_kind!r}. Use one of: {', '.join(AUTH_KINDS)}")
    if protocol not in ALLOWED[auth_kind]:
        raise HTTPException(400, f"{auth_kind} connections speak {', '.join(ALLOWED[auth_kind])}, not {protocol!r}")


class ProviderError(HTTPException):
    """A provider call that failed, with what the smart router needs to decide on a fallback (router.py).

    upstream: the provider's HTTP status (None when it never answered); kind: http | timeout | unreachable | quota.
    retry_after: seconds the provider asked us to wait, when it said. body: the start of its error text (never logged
    whole: some providers quote the key back).
    """

    def __init__(self, status_code: int, detail: str, upstream: int | None = None, kind: str = "http",
                 retry_after: float | None = None, body: str = ""):
        super().__init__(status_code, detail)
        self.upstream, self.kind, self.retry_after, self.body = upstream, kind, retry_after, body


# The router sets a dict here before a call; the transport writes the response's rate-limit headers into it
CAPTURE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("provider_capture", default=None)
_LIMIT_HEADERS = re.compile(r"^(x-ratelimit-|anthropic-ratelimit-|ratelimit|retry-after$)", re.I)


def _capture(headers) -> None:
    box = CAPTURE.get()
    if box is not None:
        box["headers"] = {k.lower(): v for k, v in headers.items() if _LIMIT_HEADERS.match(k)}


def retry_after(headers) -> float | None:
    v = headers.get("retry-after") if headers else None
    try:
        return max(0.0, float(v)) if v is not None else None
    except ValueError:
        return None


def error_event(conn: dict, status: int, text: str, headers=None) -> dict:
    """A streaming error in the same terms as ProviderError, so the router can tell a quota from a crash."""
    return {"error": f"{conn['name']} error {status}: {text[:400]}", "status": status, "retry_after": retry_after(headers),
            "body": text[:400]}


# Credentials, by auth_kind
def credential(conn: dict) -> str:
    """The secret to send for this connection, or raise with the reason it cannot be used."""
    kind = conn["auth_kind"]
    if kind == "session_scraping_forbidden":
        raise HTTPException(403, R5_REFUSAL)
    if kind in ("local_ollama", "cli_subscription"):
        return ""  # a local server, or a CLI that holds its own sign-in: nothing for the app to send
    if kind == "oauth_subscription":
        return _oauth_access_token(conn)
    return decrypt(conn["secret_enc"])  # api_key, openai_compatible (key optional)


def _oauth_access_token(conn: dict) -> str:
    """Access token from the connection's token store, refreshed near expiry (Sign in with ChatGPT, siwc.py)."""
    from . import siwc

    try:
        tokens = json.loads(decrypt(conn["secret_enc"]) or "{}")
    except ValueError:
        tokens = {}
    expires = tokens.get("expires_at") or 0
    if tokens.get("access_token") and expires > time.time() + 30:
        return tokens["access_token"]
    if tokens.get("refresh_token"):
        fresh = siwc.refresh(conn, tokens)
        if fresh:
            return fresh
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
        raise ProviderError(504, f"{conn['name']} did not answer within {int(timeout.read or 0)} s", kind="timeout") from e
    except httpx.HTTPError as e:
        raise ProviderError(502, f"{conn['name']} unreachable: {e.__class__.__name__}", kind="unreachable") from e
    _capture(r.headers)
    if r.status_code >= 400:
        raise ProviderError(502, f"{conn['name']} error {r.status_code}: {r.text[:400]}", upstream=r.status_code,
                            retry_after=retry_after(r.headers), body=r.text[:400])
    return r


async def _sse(conn: dict, url: str, body: dict, headers: dict, timeout: httpx.Timeout) -> AsyncIterator[dict]:
    """Server-sent events as parsed JSON payloads. An HTTP error becomes one {"error": ...} item."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=body) as r:
            _capture(r.headers)
            if r.status_code >= 400:
                text = (await r.aread()).decode(errors="replace")
                yield error_event(conn, r.status_code, text, r.headers)
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
        if isinstance(data.get("models"), list) and not data.get("data"):  # Sign in with ChatGPT's per-account catalogue
            return [m["slug"] for m in data["models"] if m.get("slug") and m.get("visibility", "list") == "list"]
        return [m["id"].removeprefix("models/") for m in data.get("data", []) if m.get("id")]

    def context_chars(self, conn: dict, model: str = "") -> int:
        """Prompt characters the model takes, a quarter of its window kept for the reply. From the model catalog when
        it knows the model (a 32K model must not get 300K characters, a 1M model should not be cut at 300K)."""
        from . import catalog

        tokens = catalog.context_tokens(conn.get("base_url", ""), model) if model else None
        if not tokens:
            return CLOUD_CONTEXT_CHARS
        return max(MIN_CONTEXT_CHARS, int(tokens * CHARS_PER_TOKEN * 0.75))

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
                yield {**chunk, "error": chunk["error"] if isinstance(chunk["error"], str) else json.dumps(chunk["error"])[:400]}
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

    def context_chars(self, conn, model=""):
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
        body: dict = {"model": model, "messages": self.convert(messages), "stream": stream, "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": OLLAMA_NUM_PREDICT}}
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
                    yield error_event(conn, r.status_code, text, r.headers)
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
        if conn.get("auth_kind") == "oauth_subscription":  # ChatGPT plan usage: every request must be store:false, stream:true
            data = await self._collect(conn, secret, model, messages, tools, schema)
        else:
            data = (await _post(conn, conn["base_url"].rstrip("/") + "/responses", self._body(model, messages, False, tools, schema),
                                self.headers(conn, secret), self.timeout)).json()
        text, calls = "", []
        for item in data.get("output") or []:
            if item.get("type") == "message":
                text += "".join(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text")
            elif item.get("type") == "function_call" and item.get("name"):
                calls.append(_call(item.get("call_id") or f"call_{len(calls)}", item["name"], item.get("arguments") or "{}"))
        return {"message": {"role": "assistant", "content": text, "tool_calls": calls}, "usage": self._usage(data.get("usage"))}

    @staticmethod
    def _failure(conn: dict, ev: dict) -> dict:
        err = ev.get("error") or (ev.get("response") or {}).get("error") or ev
        text = json.dumps(err)[:400]
        code = err.get("code", "") if isinstance(err, dict) else ""
        # Sign in with ChatGPT: the plan's usage limit can arrive mid stream (developers.openai.com/siwc, models-and-inference)
        status = 429 if code in ("subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable",
                                 "rate_limit_exceeded", "insufficient_quota") else 502
        return {"error": f"{conn['name']}: {text}", "status": status, "body": text}

    async def _collect(self, conn, secret, model, messages, tools, schema) -> dict:
        """A whole answer read from the event stream: text, function calls and usage, complete only at response.completed."""
        text, items, final = "", [], None
        async for ev in _sse(conn, conn["base_url"].rstrip("/") + "/responses", self._body(model, messages, True, tools, schema),
                             self.headers(conn, secret), self.timeout):
            t = ev.get("type")
            if "error" in ev and not t:
                raise ProviderError(502, ev["error"], upstream=ev.get("status"), retry_after=ev.get("retry_after"), body=ev.get("body", ""))
            if t == "response.output_text.delta":
                text += ev.get("delta", "")
            elif t == "response.output_item.done" and isinstance(ev.get("item"), dict):
                items.append(ev["item"])
            elif t == "response.completed":
                final = ev.get("response") or {}
            elif t in ("error", "response.failed", "response.incomplete"):
                f = self._failure(conn, ev)
                raise ProviderError(502, f["error"], upstream=f["status"], kind="quota" if f["status"] == 429 else "http", body=f["body"])
        if final is None:
            raise ProviderError(502, f"{conn['name']} stream ended before response.completed", kind="unreachable")
        output = final.get("output") or items or ([{"type": "message", "content": [{"type": "output_text", "text": text}]}] if text else [])
        return {"output": output, "usage": final.get("usage")}

    async def stream(self, conn, secret, model, messages):
        usage: dict = {}
        completed = False
        async for ev in _sse(conn, conn["base_url"].rstrip("/") + "/responses", self._body(model, messages, True),
                             self.headers(conn, secret), self.timeout):
            t = ev.get("type")
            if "error" in ev and not t:
                yield ev
                return
            if t == "response.output_text.delta":
                yield {"delta": ev.get("delta", "")}
            elif t == "response.completed":
                completed = True
                usage = self._usage((ev.get("response") or {}).get("usage"))
            elif t in ("error", "response.failed", "response.incomplete"):
                yield self._failure(conn, ev)
                return
        if not completed and conn.get("auth_kind") == "oauth_subscription":
            yield {"error": f"{conn['name']} stream ended before response.completed", "status": 502}
            return
        yield {"done": True, "usage": usage}


# Prompt caching (Anthropic Messages API): the system prompt, the tool list and the conversation so far are marked as
# cache breakpoints (at most 4 are allowed; 3 are used). Later turns then reread them at a fraction of the input price
# instead of paying in full on every call. Prompts under the model's minimum cacheable length are simply not cached.
CACHE = {"type": "ephemeral"}


def _cache_last_turn(msgs: list[dict]) -> None:
    """Breakpoint on the newest message's last block, so the whole conversation before it is cached for the next turn."""
    if msgs and isinstance(msgs[-1].get("content"), list) and msgs[-1]["content"]:
        msgs[-1]["content"][-1] = {**msgs[-1]["content"][-1], "cache_control": CACHE}


def _anthropic_usage(u: dict | None) -> dict:
    """Prompt tokens include cache writes and reads (Anthropic reports them apart from input_tokens)."""
    u = u or {}
    written, read = u.get("cache_creation_input_tokens") or 0, u.get("cache_read_input_tokens") or 0
    out = {"prompt_tokens": (u.get("input_tokens") or 0) + written + read, "completion_tokens": u.get("output_tokens") or 0}
    if written or read:
        out.update(cache_write_tokens=written, cache_read_tokens=read)
    return out


class AnthropicMessages(Protocol):
    """Anthropic's native Messages API (system is top level, tools use input_schema, results are tool_result blocks)."""
    name = "anthropic_messages"

    def headers(self, conn, secret):
        return {"x-api-key": secret, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}

    def models_url(self, conn):
        # GET /v1/models pages 20 at a time by default; limit=1000 takes the whole list in one call
        return conn["base_url"].rstrip("/") + "/models?limit=1000"

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
            body["system"] = [{"type": "text", "text": system, "cache_control": CACHE}]
        if tools:
            body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                              "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}} for t in tools]
            body["tools"][-1]["cache_control"] = CACHE
        _cache_last_turn(msgs)
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
        return {"message": {"role": "assistant", "content": text, "tool_calls": calls}, "usage": _anthropic_usage(data.get("usage"))}

    async def stream(self, conn, secret, model, messages):
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        async for ev in _sse(conn, conn["base_url"].rstrip("/") + "/messages", self._body(model, messages, True),
                             self.headers(conn, secret), self.timeout):
            t = ev.get("type")
            if "error" in ev and not t:
                yield ev
                return
            if t == "message_start":
                usage.update(_anthropic_usage((ev.get("message") or {}).get("usage")), completion_tokens=0)
            elif t == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                yield {"delta": ev["delta"].get("text", "")}
            elif t == "message_delta":
                usage["completion_tokens"] = (ev.get("usage") or {}).get("output_tokens") or 0
            elif t == "error":
                yield {"error": json.dumps(ev.get("error") or ev)[:400]}
                return
        yield {"done": True, "usage": usage}


class CliPlan(Protocol):
    """A chat turn answered by the user's own signed-in `claude` or `codex` CLI, so their Claude or ChatGPT plan pays.

    The app never sees a token: it runs the unmodified binary in print mode with no tools of its own (Claude Code:
    --tools ""; Codex: read-only sandbox) in an empty folder, sends the conversation on stdin and reads the answer.
    The app's own tools are described in the prompt and a call comes back as <tool_call>{...}</tool_call> text, which
    Adapter.chat already turns into a structured call. base_url is cli://claude or cli://codex.
    """
    name = "cli_plan"
    timeout_s = 600

    @staticmethod
    def agent(conn: dict) -> str:
        base = conn["base_url"].rstrip("/")
        return "codex" if base.endswith("codex") else "copilot" if base.endswith("copilot") else "claude"

    def models(self, conn: dict) -> list[str]:
        # Claude Code takes these aliases for the newest model of each family (claude --help, --model)
        return ["default", "sonnet", "opus", "haiku"] if self.agent(conn) == "claude" else ["default"]

    def context_chars(self, conn, model=""):
        return 400_000  # both CLIs manage their own window; this only bounds what the app sends in one turn

    @staticmethod
    def transcript(messages: list[dict], tools: list[dict] | None) -> tuple[str, str]:
        system, rest = _system_and_rest(messages)
        if tools:
            specs = "\n".join(json.dumps({"name": t["function"]["name"], "description": t["function"].get("description", ""),
                                           "parameters": t["function"].get("parameters") or {}}) for t in tools)
            system += ("\n\nYou can call these tools. To call one, reply with only <tool_call>{\"name\": ..., \"arguments\": "
                       "{...}}</tool_call> (one per call) and nothing else; the result comes back in the next message.\n" + specs)
        lines = []
        for m in rest:
            if m["role"] == "tool":
                lines.append(f"TOOL RESULT ({m.get('tool_call_id', '')}):\n{_text(m.get('content'))}")
            elif m["role"] == "assistant":
                calls = "".join(f"<tool_call>{json.dumps({'name': c['function']['name'], 'arguments': _json_args(c['function'].get('arguments'))})}</tool_call>"
                                for c in m.get("tool_calls") or [])
                lines.append(f"ASSISTANT:\n{_text(m.get('content'))}{calls}")
            else:
                lines.append(f"USER:\n{_text(m.get('content'))}")
        prompt = "\n\n".join(lines) if len(rest) > 1 else _text(rest[-1].get("content")) if rest else ""
        if len(rest) > 1:
            prompt = "The conversation so far. Reply as the assistant to the last message.\n\n" + prompt
        return system, prompt

    def argv(self, conn: dict, path: str, model: str, system: str) -> list[str]:
        if self.agent(conn) == "claude":
            argv = [path, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
            if system:
                argv += ["--system-prompt", system]
            # a chat backend, not a coding session: none of the user's hooks, plugins, skills, MCP servers or CLAUDE.md
            # (a hook could otherwise run commands on every chat message), and only the tokens of this conversation
            argv += ["--safe-mode", "--strict-mcp-config"]
            return argv + (["--model", model] if model and model != "default" else [])
        if self.agent(conn) == "copilot":
            # one prompt, answer only, no tools and no built-in GitHub MCP server: a chat backend, not an agent
            return [path, "-s", "--no-ask-user", "--no-color", "--stream", "off", "--available-tools=",
                    "--disable-builtin-mcps", "--no-custom-instructions", "--model", model if model and model != "default" else "auto"]
        argv = [path, "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never"]
        return argv + (["-m", model] if model and model != "default" else []) + ["-"]

    async def chat(self, conn, secret, model, messages, tools, schema=None):
        import asyncio
        import tempfile

        from . import cli_agents

        agent = self.agent(conn)
        path = cli_agents.find_binary(agent)
        if not path:
            raise ProviderError(502, f"{cli_agents.AGENTS[agent]['name']} isn't installed on this computer "
                                     f"({cli_agents.AGENTS[agent]['install']})", kind="unreachable")
        system, prompt = self.transcript(messages, tools)
        if schema:
            system += f"\n\nAnswer with only JSON matching this schema, no other text:\n{json.dumps(schema['schema'])}"
        if agent in ("codex", "copilot") and system:  # no system prompt flag: it leads the prompt
            prompt = f"{system}\n\n{prompt}"
        argv = self.argv(conn, path, model, system if agent == "claude" else "")
        if agent == "copilot":  # takes the prompt as an argument, not on stdin
            argv[1:1] = ["-p", prompt]
            prompt = ""
        with tempfile.TemporaryDirectory(prefix="aiw-cli-") as empty:  # no project files, no CLAUDE.md, nothing to touch
            try:
                proc = await asyncio.create_subprocess_exec(*argv,
                                                            cwd=empty, env=cli_agents.child_env(agent),
                                                            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.PIPE)
                out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), self.timeout_s)
            except asyncio.TimeoutError as e:
                proc.kill()
                raise ProviderError(504, f"{conn['name']} did not answer within {self.timeout_s} s", kind="timeout") from e
            except OSError as e:
                raise ProviderError(502, f"{conn['name']} could not start: {e}", kind="unreachable") from e
        text, usage, error = self.parse(agent, out.decode(errors="replace"))
        if agent == "copilot" and proc.returncode != 0 and not error:
            error = err.decode(errors="replace").strip() or out.decode(errors="replace").strip()
        if proc.returncode != 0 or error:
            detail = (error or err.decode(errors="replace") or f"exit code {proc.returncode}")[:400]
            limited = any(w in detail.lower() for w in ("usage limit", "rate limit", "limit reached", "quota"))
            if agent == "copilot" and "monthly quota" in detail.lower():
                detail = ("Your Copilot plan's premium requests for this month are used up. They reset on the 1st; "
                          "until then the other connected models answer.")
            signed_out = any(w in detail.lower() for w in ("not logged in", "login", "sign in", "authenticate"))
            raise ProviderError(502, f"{conn['name']}: {detail}", upstream=429 if limited else (401 if signed_out else None),
                                kind="quota" if limited else "http", body=detail)
        return {"message": {"role": "assistant", "content": text, "tool_calls": []}, "usage": usage}

    @staticmethod
    def parse(agent: str, out: str) -> tuple[str, dict, str]:
        """(answer, usage, error) from the CLI's JSON output (Copilot: its plain answer with -s)."""
        if agent == "copilot":
            return out.strip(), {"prompt_tokens": 0, "completion_tokens": 0}, ""
        if agent == "claude":
            try:
                d = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
            except ValueError:
                return "", {}, f"unreadable output: {out[:200]}"
            u = d.get("usage") or {}
            usage = {"prompt_tokens": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                     + (u.get("cache_read_input_tokens") or 0), "completion_tokens": u.get("output_tokens") or 0}
            err = str(d.get("result") or d.get("subtype") or "error") if d.get("is_error") else ""
            return str(d.get("result") or ""), usage, err
        text, usage, err = "", {"prompt_tokens": 0, "completion_tokens": 0}, ""
        for line in out.splitlines():  # codex exec --json: one event per line (same events cli_agents.parse_codex reads)
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("type", "")
            it = ev.get("item") or {}
            if t == "item.completed" and it.get("type") == "agent_message":
                text = it.get("text", "")
            elif t == "turn.completed":
                u = ev.get("usage") or {}
                usage = {"prompt_tokens": int(u.get("input_tokens") or 0), "completion_tokens": int(u.get("output_tokens") or 0)}
                err = ""
            elif t in ("turn.failed", "error"):
                e = ev.get("error")
                err = (e.get("message") if isinstance(e, dict) else None) or ev.get("message") or "Codex reported an error"
        return text, usage, err

    def stream(self, conn, secret, model, messages):
        async def gen():
            try:
                res = await self.chat(conn, secret, model, messages, None)
            except ProviderError as e:
                yield {"error": str(e.detail), "status": e.upstream, "body": e.body}
                return
            yield {"delta": res["message"]["content"]}
            yield {"done": True, "usage": res["usage"]}
        return gen()


_PROTOCOLS: dict[str, Protocol] = {p.name: p for p in (ChatCompletions(), OllamaNative(), Responses(), AnthropicMessages(), CliPlan())}


class Adapter:
    """The one interface every caller uses, whatever the connection (R1)."""

    def __init__(self, conn: dict):
        self.conn = conn
        self.list_error = ""  # why the last model listing failed, for the user (never the provider's error body)
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

    def context_chars(self, model: str = "") -> int:
        if self.auth_kind == "session_scraping_forbidden":
            raise HTTPException(403, R5_REFUSAL)
        return self.protocol.context_chars(self.conn, model)

    def list_models(self) -> list[str]:
        """Models the endpoint lists plus the ones typed in by hand. A failed listing is not an error: manual ones remain."""
        try:
            secret = credential(self.conn)
        except HTTPException:
            if self.auth_kind != "oauth_subscription":
                raise
            secret = ""  # signed out: typed-in models remain listed, a call then asks to sign in again
        manual = [m.strip() for m in (self.conn.get("models") or "").split(",") if m.strip()]
        if isinstance(self.protocol, CliPlan):
            return sorted(set(self.protocol.models(self.conn)) | set(manual))
        names: list[str] = []
        if secret or self.auth_kind != "oauth_subscription":  # plan tokens list the account's own catalogue
            try:
                r = httpx.get(self.protocol.models_url(self.conn), headers=self.protocol.headers(self.conn, secret), timeout=LIST_TIMEOUT_S)
                if r.status_code in (401, 403):
                    self.list_error = f"the key was rejected (HTTP {r.status_code})"
                r.raise_for_status()
                names = [n for n in self.protocol.parse_models(r.json()) if keep_model(self.conn["base_url"], n)]
            except httpx.TimeoutException:
                self.list_error = f"no model list within {LIST_TIMEOUT_S} s"
            except httpx.HTTPStatusError as e:
                self.list_error = self.list_error or f"model list failed (HTTP {e.response.status_code})"
            except httpx.HTTPError as e:
                self.list_error = f"unreachable ({e.__class__.__name__})"
            except (ValueError, KeyError, TypeError):
                self.list_error = "its model list could not be read"
        return sorted(set(names) | set(manual))

    def probe(self, deep: bool = False) -> tuple[str, str]:
        """Health check (item 25): (ok | degraded | down, detail). Unlike list_models, a failure is reported, not hidden.
        deep: also make one tiny chat request, which catches keys that list models but cannot chat.

        Only status codes are reported, never the provider's error body: some quote the rejected key back (R7).
        """
        secret = credential(self.conn)  # raises for a refused kind (R5) or an expired sign-in
        manual = [m.strip() for m in (self.conn.get("models") or "").split(",") if m.strip()]
        if isinstance(self.protocol, CliPlan):
            from . import cli_agents

            info = cli_agents.detect(self.protocol.agent(self.conn))
            if not info["installed"] or not info["signed_in"]:
                return "down", info["detail"]
            if not deep:
                return ("ok" if info["auth"] == "subscription" else "degraded"), info["detail"]
            return self._answer_check(self.protocol.models(self.conn), info["detail"])
        try:
            r = httpx.get(self.protocol.models_url(self.conn), headers=self.protocol.headers(self.conn, secret), timeout=8)
        except httpx.HTTPError as e:
            return "down", f"unreachable ({e.__class__.__name__})"
        if r.status_code in (401, 403):
            return "down", f"the key was rejected (HTTP {r.status_code})"
        if r.status_code >= 400:
            if manual:
                return "degraded", f"no model list (HTTP {r.status_code}); {len(manual)} models typed in are used"
            return "down", f"HTTP {r.status_code}"
        try:
            names = [n for n in self.protocol.parse_models(r.json()) if keep_model(self.conn["base_url"], n)]
        except (ValueError, KeyError, TypeError):
            return "degraded", "reachable, but its model list could not be read"
        n = len(set(names) | set(manual))
        if not n:
            return "degraded", "reachable, but it lists no models"
        listed = f"{n} model{'s' if n != 1 else ''}"
        if not deep:
            return "ok", listed
        return self._answer_check(sorted(set(names) | set(manual)), listed)

    def _answer_check(self, models: list[str], listed: str) -> tuple[str, str]:
        """One tiny real request: a key can list models and still be refused for chat (no credit, no billing, no
        access to the model). Costs a few tokens, so it runs when a key is added or checked, not on every page load."""
        import asyncio

        from . import router

        model = router.choose_model(self.conn, models, "fast")
        if not model:
            return "degraded", f"{listed}, none of them a chat model"
        try:
            asyncio.run(self.protocol.chat(self.conn, credential(self.conn), model, [{"role": "user", "content": "Reply with OK."}], None))
        except ProviderError as e:
            reason = router.classify(e)[0]
            if e.upstream in (401, 403):
                return "down", f"lists models, but chat was refused: {reason} (HTTP {e.upstream})"
            return "degraded", f"lists models, but a test answer from {model} failed: {reason}" + (f" (HTTP {e.upstream})" if e.upstream else "")
        except HTTPException as e:
            return "degraded", f"lists models, but a test answer from {model} failed: {e.detail}"
        return "ok", f"{listed}; {model} answered"

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
