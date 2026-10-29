"""Pluggable model providers.

Inside the AI Workspace stack there is one provider: `workbench`. browser-use's OpenAI client talks to Workbench's
OpenAI-shaped /v1 endpoint, which runs every request through Workbench's single adapter layer (rule R1): any
connection the user set up there works, provider keys stay in Workbench, and every call lands in its run log.
With BROWSER_REQUIRE_WORKBENCH=true (set by docker-compose) every other provider is refused.

The other providers serve the standalone CLI, used without Workbench. Pick one explicitly (`provider="anthropic"`) or let
`resolve_provider("auto")` choose: workbench if WORKBENCH_URL is set, else anthropic if ANTHROPIC_API_KEY is set, else
gemini if a Gemini key is set, else local ollama (no key needed). OpenAI, OpenRouter and generic endpoints need a model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    key_env: tuple[str, ...]          # any of these env vars enables the provider
    default_model: str | None         # None = caller must pass a model
    factory: Callable[[str, dict[str, Any]], Any]
    notes: str = ""


def _env(*names: str) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None


def _anthropic(model: str, kw: dict[str, Any]):
    from browser_use import ChatAnthropic

    return ChatAnthropic(model=model, api_key=kw.get("api_key") or _env("ANTHROPIC_API_KEY"))


def _gemini(model: str, kw: dict[str, Any]):
    from browser_use import ChatGoogle

    return ChatGoogle(model=model, api_key=kw.get("api_key") or _env("GEMINI_API_KEY", "GOOGLE_API_KEY"))


def _openai(model: str, kw: dict[str, Any]):
    from browser_use import ChatOpenAI

    return ChatOpenAI(model=model, api_key=kw.get("api_key") or _env("OPENAI_API_KEY"), base_url=kw.get("base_url"))


def _openrouter(model: str, kw: dict[str, Any]):
    from browser_use import ChatOpenRouter

    return ChatOpenRouter(model=model, api_key=kw.get("api_key") or _env("OPENROUTER_API_KEY"))


def _ollama(model: str, kw: dict[str, Any]):
    from browser_use import ChatOllama

    return ChatOllama(model=model, host=kw.get("base_url") or _env("OLLAMA_HOST") or "http://localhost:11434")


def _workbench(model: str, kw: dict[str, Any]):
    from browser_use import ChatOpenAI

    base = (kw.get("base_url") or _env("WORKBENCH_URL") or "").rstrip("/")
    if not base:
        raise ValueError("The workbench provider needs WORKBENCH_URL, e.g. http://workbench:8000")
    return ChatOpenAI(model=model, api_key=kw.get("api_key") or _env("BROWSER_API_KEY"), base_url=f"{base}/v1")


def _compatible(model: str, kw: dict[str, Any]):
    from browser_use import ChatOpenAI

    base_url = kw.get("base_url") or _env("OPENAI_COMPATIBLE_BASE_URL")
    if not base_url:
        raise ValueError("openai-compatible needs --base-url or OPENAI_COMPATIBLE_BASE_URL")
    return ChatOpenAI(model=model, api_key=kw.get("api_key") or _env("OPENAI_COMPATIBLE_API_KEY") or "none", base_url=base_url)


PROVIDERS: dict[str, Provider] = {
    p.name: p
    for p in [
        Provider("workbench", "Workbench (your model connections)", ("WORKBENCH_URL",), "default", _workbench,
                 "Any model connected in Workbench. Model ids are Workbench model refs, or 'default'."),
        Provider("anthropic", "Anthropic Claude", ("ANTHROPIC_API_KEY",), "claude-sonnet-5-5", _anthropic,
                 "Most reliable at multi step browsing."),
        Provider("gemini", "Google Gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "gemini-flash-latest", _gemini,
                 "Fast and cheap. Google requires keys restricted to the Gemini API."),
        Provider("openai", "OpenAI", ("OPENAI_API_KEY",), None, _openai, "Pass --model with a current OpenAI model id."),
        Provider("openrouter", "OpenRouter", ("OPENROUTER_API_KEY",), None, _openrouter,
                 "Any OpenRouter model id, including its ':free' models."),
        Provider("ollama", "Ollama (local)", (), "qwen3:8b", _ollama,
                 "Free and private. Needs a capable model (8B+); small models often loop or stop early."),
        Provider("openai-compatible", "Any OpenAI compatible API", ("OPENAI_COMPATIBLE_BASE_URL",), None, _compatible,
                 "LM Studio, vLLM, LiteLLM, Groq, Together and others."),
    ]
}

AUTO_ORDER = ("workbench", "anthropic", "gemini", "ollama")
R1_REFUSAL = ("Inside AI Workspace, browser model calls go through Workbench (rule R1: one adapter layer, keys stay in "
              "Workbench, every call in its run log). Pick a model connected in Workbench instead.")


def workbench_required() -> bool:
    return (os.getenv("BROWSER_REQUIRE_WORKBENCH") or "").lower() == "true"


def available() -> list[str]:
    """Providers that have credentials (ollama is always listed: it needs none). In the stack, only workbench."""
    if workbench_required():
        return ["workbench"] if _env("WORKBENCH_URL") else []
    return [n for n, p in PROVIDERS.items() if n != "workbench" and (not p.key_env or _env(*p.key_env))] + \
        (["workbench"] if _env("WORKBENCH_URL") else [])


def resolve_provider(name: str | None) -> str:
    name = (name or os.getenv("BROWSER_LLM_PROVIDER") or "auto").lower()
    if workbench_required():
        if name not in ("auto", "workbench"):
            raise ValueError(R1_REFUSAL)
        if not _env("WORKBENCH_URL"):
            raise ValueError("BROWSER_REQUIRE_WORKBENCH is set but WORKBENCH_URL is not")
        return "workbench"
    if name != "auto":
        if name not in PROVIDERS:
            raise ValueError(f"Unknown provider '{name}'. Choose from: {', '.join(PROVIDERS)}")
        return name
    # auto: first provider with credentials and a default model
    have = set(available())
    for n in AUTO_ORDER:
        if n in have and PROVIDERS[n].default_model:
            return n
    return "ollama"


def make_llm(provider: str | None = None, model: str | None = None, *, api_key: str | None = None, base_url: str | None = None):
    """Build a browser-use chat model. Returns (llm, provider_name, model_name)."""
    name = resolve_provider(provider)
    p = PROVIDERS[name]
    # BROWSER_LLM_MODEL belongs to BROWSER_LLM_PROVIDER; never apply it to a provider picked by "auto"
    env_model = os.getenv("BROWSER_LLM_MODEL") if (os.getenv("BROWSER_LLM_PROVIDER") or "").lower() == name else None
    model = model or env_model or p.default_model
    if not model:
        raise ValueError(f"Provider '{name}' has no default model. Pass --model (or set BROWSER_LLM_MODEL).")
    return p.factory(model, {"api_key": api_key, "base_url": base_url}), name, model
