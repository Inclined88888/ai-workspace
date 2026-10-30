"""Model adapters for Rooks Desk (vendored from rooks_browser/llm.py, kept in step), standard library only: OpenAI, Anthropic, OpenRouter, Ollama, any OpenAI-compatible server, and
Workbench's own OpenAI-shaped /v1 endpoint (so inside AI Workspace keys stay in Workbench and every call is in its run log).

The agent asks for one JSON object per step, so no provider-specific tool calling is needed; that is what makes small
local models usable. Vision: when the model can see, the step's screenshot goes in the same message as the page text.
Every HTTP request is reported to `on_egress` for the receipt.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

EgressFn = Callable[[str, str, str, int, str], None]


@dataclass
class Reply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class ProviderInfo:
    name: str
    label: str
    key_env: tuple[str, ...]
    default_model: str
    notes: str = ""


PROVIDERS = {p.name: p for p in [
    ProviderInfo("openai", "OpenAI", ("OPENAI_API_KEY",), "gpt-4.1-mini", "Vision on by default."),
    ProviderInfo("anthropic", "Anthropic Claude", ("ANTHROPIC_API_KEY",), "claude-sonnet-4-5", "Vision on by default."),
    ProviderInfo("openrouter", "OpenRouter", ("OPENROUTER_API_KEY",), "openai/gpt-4.1-mini", "Any OpenRouter model id; :free models work."),
    ProviderInfo("groq", "Groq (free key at console.groq.com)", ("GROQ_API_KEY",), "qwen/qwen3.8-27b",
                 "Fast. qwen/qwen3.8-27b and meta-llama/llama-4-scout-17b-16e-instruct can see screenshots; openai/gpt-oss-120b is text only (accessibility tree)."),
    ProviderInfo("gemini", "Google Gemini (free key at aistudio.google.com)", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "gemini-2.5-flash",
                 "Vision on by default."),
    ProviderInfo("ollama", "Ollama (local, free)", (), "qwen2.5vl:7b", "OLLAMA_HOST, default http://localhost:11434. Text only unless the model can see (llava, qwen2.5vl, gemma3, ...)."),
    ProviderInfo("openai-compatible", "Any OpenAI-compatible server", ("OPENAI_COMPATIBLE_BASE_URL",), "", "LM Studio, vLLM, llama.cpp, Groq, ..."),
    ProviderInfo("workbench", "AI Workspace app (its smart router)", ("WORKBENCH_URL",), "default",
                 "Found automatically while the desktop app is open; or set WORKBENCH_URL and WORKBENCH_API_KEY."),
    ProviderInfo("scripted", "Scripted (tests and demos, no model)", (), "", "--model is a JSON file of actions."),
]}
AUTO_ORDER = ("workbench", "anthropic", "openai", "openrouter", "gemini", "groq", "ollama")
NO_MODEL_HELP = ("No AI model is set up yet. Either open the app (run `ai-workspace`) and choose \"Free cloud\" "
                 "(a free Gemini or Groq key: two minutes, no card), or set a key in this terminal and run again:\n"
                 "    export GROQ_API_KEY=...     (free at https://console.groq.com/keys)\n"
                 "    export GEMINI_API_KEY=...   (free at https://aistudio.google.com/apikey)\n"
                 "On Windows PowerShell: $env:GROQ_API_KEY=\"...\"")
_VISION_HINT = re.compile(r"(gpt-4o|gpt-4\.1|gpt-5|gpt-6|o3|o4|claude|gemini|grok-4|llava|vision|-vl|vl:|qwen2\.5vl|qwen3-vl|qwen3\.[5-9]|gemma3|minicpm-v|moondream|pixtral|llama4|llama-4|muse-spark)", re.I)


def _env(*names: str) -> str:
    for n in names:
        if os.getenv(n):
            return os.environ[n]
    return ""


def app_link() -> dict:
    """The running AI Workspace desktop app ({"url", "key"}) from the app.json it writes, or WORKBENCH_URL /
    WORKBENCH_API_KEY. Empty when neither is there. Same file as rooks_code.config.app_link."""
    if _env("WORKBENCH_URL"):
        return {"url": _env("WORKBENCH_URL").rstrip("/"), "key": _env("WORKBENCH_API_KEY", "BROWSER_API_KEY")}
    if os.getenv("AI_WORKSPACE_APP_FILE"):
        path = os.environ["AI_WORKSPACE_APP_FILE"]
    elif os.name == "nt" and os.getenv("APPDATA"):
        path = os.path.join(os.environ["APPDATA"], "ai-workspace", "app.json")
    else:
        path = os.path.join(os.getenv("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "ai-workspace", "app.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        url = str(d.get("url") or "").rstrip("/")
        if not url:
            return {}
        urllib.request.urlopen(f"{url}/healthz", timeout=1.5).close()  # a file left by a crashed app is ignored
        return {"url": url, "key": str(d.get("key") or "")}
    except (OSError, ValueError, AttributeError):
        return {}


def app_has_models(link: dict) -> bool:
    """False only when the app answers and lists no models at all (installed, but no model chosen yet)."""
    req = urllib.request.Request(f"{link['url']}/v1/models", headers={"Authorization": f"Bearer {link.get('key', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r).get("data") != []
    except (OSError, ValueError, AttributeError):
        return True


def _has_credentials(name: str) -> bool:
    if name == "workbench":
        link = app_link()
        return bool(link) and app_has_models(link)
    return bool(_env(*PROVIDERS[name].key_env))


def _ollama_up() -> bool:
    try:
        urllib.request.urlopen(f"{ollama_host()}/api/tags", timeout=1.5).close()
        return True
    except (OSError, ValueError):
        return False


def ollama_host() -> str:
    h = _env("OLLAMA_HOST") or "http://localhost:11434"
    return h if "://" in h else f"http://{h}"


def ready() -> list[str]:
    """Providers with credentials (ollama counts when its server answers)."""
    out = [n for n in AUTO_ORDER if n != "ollama" and _has_credentials(n)]
    try:
        urllib.request.urlopen(f"{ollama_host()}/api/tags", timeout=1.5).close()
        out.append("ollama")
    except (OSError, ValueError):
        pass
    if _env("OPENAI_COMPATIBLE_BASE_URL"):
        out.append("openai-compatible")
    return out


def resolve(provider: str | None) -> str:
    p = (provider or _env("ROOKS_DESK_PROVIDER") or "auto").lower()
    if p != "auto":
        if p not in PROVIDERS:
            raise ValueError(f"unknown provider {p!r} (choose from {', '.join(PROVIDERS)})")
        return p
    for n in AUTO_ORDER:
        if n != "ollama" and _has_credentials(n):
            return n
    if _ollama_up():
        return "ollama"
    raise ValueError(NO_MODEL_HELP)


def sees(provider: str, model: str, vision: str = "auto") -> bool:
    if vision in ("on", "off"):
        return vision == "on"
    if provider in ("scripted",):
        return False
    if provider in ("openai", "anthropic", "gemini", "workbench"):
        return True  # workbench: the smart router sends screenshots to a model that can see, or drops them
    return bool(_VISION_HINT.search(model or ""))


class Model:
    provider = ""
    model = ""
    no_vision = False  # set when the provider refused an image; the agent stops sending screenshots

    def complete(self, system: str, user: str, image_b64: str | None = None) -> Reply:  # pragma: no cover - interface
        raise NotImplementedError


class ProviderHTTPError(RuntimeError):
    def __init__(self, msg: str, code: int, body: str):
        super().__init__(msg)
        self.code, self.body = code, body


RETRY_CODES = (429, 498, 500, 502, 503, 504)  # rate limits (Groq's 8K tokens a minute on free keys), overload
MAX_WAIT_S = 90
_IMAGE_REFUSED = re.compile(r"(image|vision|multimodal|image_url|content.*must be a string|does not support)", re.I)  # past this (e.g. a daily limit) we stop and say so instead of hanging


def retry_after(headers, body: str, attempt: int) -> float:
    """Seconds to wait before retrying: Retry-After, Groq's "try again in 7.5s" / "in 1m2s", else 2, 4, 8, 16 s."""
    try:
        v = (headers or {}).get("retry-after")
        if v:
            return float(v)
    except (TypeError, ValueError):
        pass
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", body or "")
    if m and any(m.groups()):
        h, mi, se = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + se
    return float(min(2 ** (attempt + 1), 30))


class _HTTP(Model):
    retries = 5

    def __init__(self, on_egress: EgressFn | None, timeout: float):
        self.on_egress = on_egress or (lambda *a: None)
        self.timeout = timeout
        self.sleep = time.sleep

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "rooks-desk/0.1", **headers})
        for attempt in range(self.retries + 1):
            started = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                self.on_egress("POST", url, f"http_{e.code}", int((time.monotonic() - started) * 1000), "")
                raw = e.read().decode("utf-8", "replace")
                detail = raw[:500]
                if e.code in RETRY_CODES and attempt < self.retries:
                    wait = retry_after(e.headers, raw, attempt)
                    if wait <= MAX_WAIT_S:
                        self.sleep(wait + 0.25)
                        continue
                if e.code == 429:
                    raise ProviderHTTPError(f"{self.provider} rate limit reached and it did not clear in time "
                                            f"({_short(raw)}). Wait a minute, pick a bigger plan or another model, or lower max steps.", 429, raw) from e
                if e.code == 400 and "No model provider configured" in detail:
                    raise RuntimeError(NO_MODEL_HELP) from e
                if e.code in (401, 403) and self.provider in ("groq", "gemini", "openai", "anthropic", "openrouter"):
                    raise RuntimeError(f"{self.provider} refused the API key (HTTP {e.code}). Check the key and try again.") from e
                raise ProviderHTTPError(f"{self.provider} returned HTTP {e.code}: {detail}", e.code, raw) from e
            except (OSError, ValueError) as e:
                self.on_egress("POST", url, "error", int((time.monotonic() - started) * 1000), e.__class__.__name__)
                if attempt < min(self.retries, 2) and not isinstance(e, ValueError):  # a dropped connection: try twice more
                    self.sleep(2.0 * (attempt + 1))
                    continue
                raise RuntimeError(f"{self.provider} unreachable at {url}: {e}") from e
            self.on_egress("POST", url, "ok", int((time.monotonic() - started) * 1000), "")
            return data
        raise RuntimeError(f"{self.provider}: gave up after {self.retries} retries")  # pragma: no cover


def _short(raw: str) -> str:
    try:
        return str(json.loads(raw)["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return raw[:200]


def fit_prompt(text: str, limit: int) -> str:
    """Trim the middle of a prompt (the long page listing) so it fits a small context or rate limit. The task, the
    previous steps and the reply instructions at the end are kept."""
    if limit <= 0 or len(text) <= limit:
        return text
    mark = "\n…(rest of the page trimmed to fit the model's limit; scroll or extract to see more)…\n"
    tail = min(800, limit // 4)
    head = text[: max(0, limit - tail - len(mark))]
    head = head[: head.rfind("\n")] if "\n" in head[-400:] else head
    return head + mark + text[-tail:]


_TOO_LARGE = re.compile(r"request too large|reduce your message size|context.length|maximum context|too many tokens|"
                        r"rate_limit_exceeded.*tokens per minute|prompt is too long", re.I)
_BAD_GENERATION = re.compile(r"tool_use_failed|json_validate_failed|failed_generation|Failed to (?:generate|call a function)|"
                             r"output_parse_failed", re.I)


class OpenAIChat(_HTTP):
    def __init__(self, provider: str, model: str, base_url: str, api_key: str, on_egress=None, timeout=180, json_mode=True,
                 extra_headers: dict | None = None, max_prompt_chars: int = 0, extra_body: dict | None = None):
        super().__init__(on_egress, timeout)
        self.provider, self.model, self.base_url, self.api_key = provider, model, base_url.rstrip("/"), api_key
        self.json_mode, self.extra_headers = json_mode, extra_headers or {}
        self.max_prompt_chars, self.extra_body = max_prompt_chars, extra_body or {}

    def complete(self, system, user, image_b64=None, _tries=0):
        user = fit_prompt(user, self.max_prompt_chars)
        content: list | str = user
        if image_b64:
            content = [{"type": "text", "text": user},
                       {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                "temperature": 0, **self.extra_body}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        h = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers} if self.api_key else dict(self.extra_headers)
        try:
            d = self._post(f"{self.base_url}/chat/completions", body, h)
        except ProviderHTTPError as e:
            if image_b64 and e.code in (400, 404, 415, 422) and _IMAGE_REFUSED.search(e.body):
                # this model cannot take screenshots: carry on with the accessibility-tree text only
                self.no_vision = True
                return self.complete(system, user + "\n\n(The screenshot could not be sent to this model; use the window and element lists.)",
                                     None, _tries)
            if self.json_mode and "response_format" in e.body:  # servers without JSON mode: retry plain once
                self.json_mode = False
                return self.complete(system, user, image_b64, _tries)
            if e.code in (400, 413, 422) and _BAD_GENERATION.search(e.body):
                # Groq rejects replies that are not clean JSON or that try to call a tool; the text it did write is
                # usually the action we want, so hand it to the parser (which also tolerates prose around the JSON).
                try:
                    failed = json.loads(e.body)["error"].get("failed_generation") or ""
                except (ValueError, KeyError, TypeError, AttributeError):
                    failed = ""
                if failed:
                    return Reply(str(failed), 0, 0)
                if _tries < 1:
                    return self.complete(system, user + "\n\nAnswer with plain text containing only the JSON object. Do not call tools.",
                                         image_b64, _tries + 1)
            if e.code in (400, 413, 429) and _TOO_LARGE.search(e.body) and _tries < 2:
                limit = int(len(user) * 0.6)
                self.max_prompt_chars = limit if not self.max_prompt_chars else min(self.max_prompt_chars, limit)
                return self.complete(system, user, image_b64, _tries + 1)
            raise
        u = d.get("usage") or {}
        msg = ((d.get("choices") or [{}])[0].get("message") or {})
        text = msg.get("content") or ""
        if not text.strip() and isinstance(msg.get("tool_calls"), list) and msg["tool_calls"]:  # a tool call instead of text
            fn = msg["tool_calls"][0].get("function") or {}
            text = json.dumps({"thought": "", "action": fn.get("name", ""), "args": _loads(fn.get("arguments"))})
        return Reply(text, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))


def _loads(v) -> dict:
    if isinstance(v, dict):
        return v
    try:
        d = json.loads(v or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


class AnthropicChat(_HTTP):
    def __init__(self, model: str, api_key: str, on_egress=None, timeout=180, base_url="https://api.anthropic.com"):
        super().__init__(on_egress, timeout)
        self.provider, self.model, self.api_key, self.base_url = "anthropic", model, api_key, base_url.rstrip("/")

    def complete(self, system, user, image_b64=None):
        content: list = [{"type": "text", "text": user}]
        if image_b64:
            content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}})
        body = {"model": self.model, "max_tokens": 1500, "system": system, "temperature": 0,
                "messages": [{"role": "user", "content": content}]}
        d = self._post(f"{self.base_url}/v1/messages", body, {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        u = d.get("usage") or {}
        return Reply(text, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0))


class OllamaChat(_HTTP):
    def __init__(self, model: str, host: str, on_egress=None, timeout=600):
        super().__init__(on_egress, timeout)
        self.provider, self.model, self.host = "ollama", model, host.rstrip("/")

    def complete(self, system, user, image_b64=None):
        msg: dict = {"role": "user", "content": user}
        if image_b64:
            msg["images"] = [image_b64]
        body = {"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0, "num_ctx": 8192},
                "messages": [{"role": "system", "content": system}, msg]}
        d = self._post(f"{self.host}/api/chat", body, {})
        return Reply((d.get("message") or {}).get("content", ""), int(d.get("prompt_eval_count") or 0), int(d.get("eval_count") or 0))


class ScriptedModel(Model):
    """Replays a list of actions (a JSON file or a Python list). For tests, CI and demos: no model, no network.
    An item may be a callable taking the prompt (tests use this to look up element ids by name)."""

    provider = "scripted"

    def __init__(self, script: list | str | Path):
        if isinstance(script, (str, Path)):
            self.model = str(script)
            script = json.loads(Path(script).read_text(encoding="utf-8"))
        self.script = list(script)
        self.prompts: list[str] = []

    def complete(self, system, user, image_b64=None):
        self.prompts.append(user)
        if not self.script:
            return Reply(json.dumps({"thought": "script finished", "action": "done", "args": {"answer": "", "success": False}}))
        item = self.script.pop(0)
        if callable(item):
            item = item(user)
        return Reply(item if isinstance(item, str) else json.dumps(item))


def make(provider: str | None, model: str | None = None, *, base_url: str | None = None, api_key: str | None = None,
         on_egress: EgressFn | None = None) -> Model:
    p = resolve(provider)
    m = model or PROVIDERS[p].default_model or _env("ROOKS_DESK_MODEL")
    if p == "scripted":
        if not m:
            raise ValueError("the scripted provider needs --model PATH_TO_SCRIPT.json")
        return ScriptedModel(m)
    if p == "ollama":
        return OllamaChat(m, base_url or ollama_host(), on_egress)
    if p == "anthropic":
        key = api_key or _env("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("anthropic needs ANTHROPIC_API_KEY")
        return AnthropicChat(m, key, on_egress, base_url=base_url or "https://api.anthropic.com")
    if p == "openai":
        key = api_key or _env("OPENAI_API_KEY")
        if not key:
            raise ValueError("openai needs OPENAI_API_KEY")
        return OpenAIChat("openai", m, base_url or "https://api.openai.com/v1", key, on_egress)
    if p == "openrouter":
        key = api_key or _env("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("openrouter needs OPENROUTER_API_KEY")
        return OpenAIChat("openrouter", m, base_url or "https://openrouter.ai/api/v1", key, on_egress, json_mode=False,
                          extra_headers={"X-Title": "Rooks Desk"})
    if p in ("groq", "gemini"):
        key = api_key or _env(*PROVIDERS[p].key_env)
        if not key:
            raise ValueError(f"{p} needs {PROVIDERS[p].key_env[0]}")
        base = base_url or ("https://api.groq.com/openai/v1" if p == "groq" else "https://generativelanguage.googleapis.com/v1beta/openai")
        if p == "groq":  # free keys allow ~8K tokens a minute: smaller prompts, capped replies, light reasoning
            extra = {"max_completion_tokens": 1500}
            if m.startswith("openai/gpt-oss"):
                extra["reasoning_effort"] = "low"
            return OpenAIChat(p, m, base, key, on_egress, json_mode=False, max_prompt_chars=int(_env("ROOKS_DESK_MAX_PROMPT_CHARS") or 14000),
                              extra_body=extra)
        return OpenAIChat(p, m, base, key, on_egress, json_mode=False)
    if p == "workbench":
        link = {} if base_url else app_link()
        base = (base_url or link.get("url") or "").rstrip("/")
        if not base:
            raise ValueError("the workbench provider needs the AI Workspace app open, or WORKBENCH_URL (e.g. http://localhost:3100)")
        return OpenAIChat("workbench", m, f"{base}/v1", api_key or link.get("key") or _env("WORKBENCH_API_KEY", "BROWSER_API_KEY"),
                          on_egress, json_mode=False, extra_headers={"X-Workbench-Channel": "rooks-desk"})
    base = base_url or _env("OPENAI_COMPATIBLE_BASE_URL")
    if not base:
        raise ValueError("openai-compatible needs --base-url or OPENAI_COMPATIBLE_BASE_URL")
    if not m:
        raise ValueError("openai-compatible needs --model")
    return OpenAIChat("openai-compatible", m, base, api_key or _env("OPENAI_COMPATIBLE_API_KEY"), on_egress, json_mode=False)


def parse_action(text: str) -> dict:
    """The first JSON object in a reply (code fences, prose and <think> blocks around it are ignored)."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.M)
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", t):
        try:
            d, _ = dec.raw_decode(t, m.start())
        except ValueError:
            continue
        if isinstance(d, dict):
            return _normalise(d)
    raise ValueError("no JSON object in the reply")


def _normalise(d: dict) -> dict:
    """Accept the shapes small models drift into: {"click": {"id": 3}}, {"action": "click", "id": 3}, {"tool": ...}."""
    action = d.get("action") or d.get("tool") or d.get("name")
    args = d.get("args") or d.get("arguments") or d.get("parameters") or {}
    if isinstance(action, dict):  # {"action": {"name": "click", "args": {...}}}
        args = action.get("args") or action.get("arguments") or {k: v for k, v in action.items() if k not in ("name", "type")}
        action = action.get("name") or action.get("type")
    if not action:
        for k in ("search", "open", "click", "type", "scroll", "extract", "download", "back", "select", "done", "wait"):
            if k in d:
                action, v = k, d[k]
                args = v if isinstance(v, dict) else {"value": v}
                break
    if not isinstance(args, dict):
        args = {"value": args}
    for k, v in d.items():
        if k not in ("action", "tool", "name", "args", "arguments", "parameters", "thought", "reasoning") and k not in args \
                and not isinstance(v, dict):
            args[k] = v
    return {"thought": str(d.get("thought") or d.get("reasoning") or ""), "action": str(action or "").lower().strip(), "args": args}
