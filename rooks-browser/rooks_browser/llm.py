"""Model adapters, standard library only: OpenAI, Anthropic, OpenRouter, Ollama, any OpenAI-compatible server, and
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
    ProviderInfo("ollama", "Ollama (local, free)", (), "qwen2.5:7b", "OLLAMA_HOST, default http://localhost:11434. Text only unless the model can see (llava, qwen2.5vl, gemma3, ...)."),
    ProviderInfo("openai-compatible", "Any OpenAI-compatible server", ("OPENAI_COMPATIBLE_BASE_URL",), "", "LM Studio, vLLM, llama.cpp, Groq, ..."),
    ProviderInfo("workbench", "AI Workspace app (its smart router)", ("WORKBENCH_URL",), "default",
                 "Found automatically while the desktop app is open; or set WORKBENCH_URL and WORKBENCH_API_KEY."),
    ProviderInfo("scripted", "Scripted (tests and demos, no model)", (), "", "--model is a JSON file of actions."),
]}
AUTO_ORDER = ("workbench", "anthropic", "openai", "openrouter", "ollama")
_VISION_HINT = re.compile(r"(gpt-4o|gpt-4\.1|gpt-5|o3|o4|claude|gemini|llava|vision|-vl|vl:|qwen2\.5vl|qwen3-vl|gemma3|minicpm-v|moondream|pixtral|llama4)", re.I)


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


def _has_credentials(name: str) -> bool:
    return bool(app_link()) if name == "workbench" else bool(_env(*PROVIDERS[name].key_env))


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
    p = (provider or _env("ROOKS_BROWSER_PROVIDER") or "auto").lower()
    if p != "auto":
        if p not in PROVIDERS:
            raise ValueError(f"unknown provider {p!r} (choose from {', '.join(PROVIDERS)})")
        return p
    for n in AUTO_ORDER:
        if n == "ollama" or _has_credentials(n):
            return n
    return "ollama"


def sees(provider: str, model: str, vision: str = "auto") -> bool:
    if vision in ("on", "off"):
        return vision == "on"
    if provider in ("scripted",):
        return False
    if provider in ("openai", "anthropic"):
        return True
    return bool(_VISION_HINT.search(model or ""))


class Model:
    provider = ""
    model = ""

    def complete(self, system: str, user: str, image_b64: str | None = None) -> Reply:  # pragma: no cover - interface
        raise NotImplementedError


class _HTTP(Model):
    def __init__(self, on_egress: EgressFn | None, timeout: float):
        self.on_egress = on_egress or (lambda *a: None)
        self.timeout = timeout

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "rooks-browser/0.1", **headers})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self.on_egress("POST", url, f"http_{e.code}", int((time.monotonic() - started) * 1000), "")
            detail = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"{self.provider} returned HTTP {e.code}: {detail}") from e
        except (OSError, ValueError) as e:
            self.on_egress("POST", url, "error", int((time.monotonic() - started) * 1000), e.__class__.__name__)
            raise RuntimeError(f"{self.provider} unreachable at {url}: {e}") from e
        self.on_egress("POST", url, "ok", int((time.monotonic() - started) * 1000), "")
        return data


class OpenAIChat(_HTTP):
    def __init__(self, provider: str, model: str, base_url: str, api_key: str, on_egress=None, timeout=180, json_mode=True,
                 extra_headers: dict | None = None):
        super().__init__(on_egress, timeout)
        self.provider, self.model, self.base_url, self.api_key = provider, model, base_url.rstrip("/"), api_key
        self.json_mode, self.extra_headers = json_mode, extra_headers or {}

    def complete(self, system, user, image_b64=None):
        content: list | str = user
        if image_b64:
            content = [{"type": "text", "text": user},
                       {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                "temperature": 0}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        h = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers} if self.api_key else dict(self.extra_headers)
        try:
            d = self._post(f"{self.base_url}/chat/completions", body, h)
        except RuntimeError as e:
            if self.json_mode and "response_format" in str(e):  # servers without JSON mode: retry plain once
                self.json_mode = False
                return self.complete(system, user, image_b64)
            raise
        u = d.get("usage") or {}
        msg = ((d.get("choices") or [{}])[0].get("message") or {})
        return Reply(msg.get("content") or "", int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))


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
    m = model or PROVIDERS[p].default_model or _env("ROOKS_BROWSER_MODEL")
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
                          extra_headers={"X-Title": "Rooks Browser"})
    if p == "workbench":
        link = {} if base_url else app_link()
        base = (base_url or link.get("url") or "").rstrip("/")
        if not base:
            raise ValueError("the workbench provider needs the AI Workspace app open, or WORKBENCH_URL (e.g. http://localhost:3100)")
        return OpenAIChat("workbench", m, f"{base}/v1", api_key or link.get("key") or _env("WORKBENCH_API_KEY", "BROWSER_API_KEY"),
                          on_egress, json_mode=False, extra_headers={"X-Workbench-Channel": "rooks-browser"})
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
