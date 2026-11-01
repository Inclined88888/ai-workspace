"""`rooks login`: interactive setup. Connect to a running Workbench (URL + Terminal access token), paste a provider key,
or use Ollama. Writes ~/.config/ai-workspace/rooks.toml; the key itself goes to the OS keychain when `keyring` is
importable, else to rooks.env (mode 600) next to the TOML. The key is never printed and never written into the TOML."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from . import config


def _ask(prompt: str, default: str = "") -> str:
    line = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return line or default


def _ask_secret(prompt: str) -> str:
    try:
        return getpass.getpass(prompt + " (not shown): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _write_toml(values: dict) -> Path:
    p = config.config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if p.exists():
        try:
            existing = config.load_file(p)
        except SystemExit:
            existing = {}
    merged = {**existing, **{k: v for k, v in values.items() if v}}
    lines = ["# written by `rooks login`. Keys live in the OS keychain or rooks.env, never here.\n"]
    for k, v in merged.items():
        if isinstance(v, str):
            lines.append(f'{k} = "{v}"\n')
        elif isinstance(v, bool):
            lines.append(f"{k} = {str(v).lower()}\n")
        elif isinstance(v, (int, float)):
            lines.append(f"{k} = {v}\n")
    p.write_text("".join(lines), encoding="utf-8")
    return p


def _connect_workbench() -> dict:
    url = _ask("Workbench URL", config.workbench_probe().get("url", "http://localhost:3100"))
    token = _ask_secret("Terminal access token (Assistant > Settings > Terminal access)")
    if not token:
        print("no token given; Workbench needs the wb_… token from Settings > Terminal access")
        return {}
    where = config.store_key("WORKBENCH_API_KEY", token)
    print(f"token stored in {where}")
    return {"provider": "workbench", "workbench_url": url.rstrip("/")}


def _paste_key() -> dict:
    key = _ask_secret("Provider API key")
    if not key:
        print("no key given")
        return {}
    name = config.provider_for_key(key)
    if not name:
        print("that key's prefix is not one I know; which provider is it for?")
        for i, n in enumerate(config.KEY_ORDER, 1):
            print(f"  {i}. {n}")
        choice = _ask("number or name")
        name = config.KEY_ORDER[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(config.KEY_ORDER) \
            else choice if choice in config.PROVIDERS else ""
        if not name:
            print("unknown provider; nothing saved")
            return {}
    env = config.PROVIDERS[name]["key_env"]
    where = config.store_key(env, key)
    print(f"key for {name} stored in {where}")
    return {"provider": name}


def _ollama() -> dict:
    model = _ask("Ollama model", "qwen2.5:7b")
    return {"provider": "ollama", "model": model}


def main() -> int:
    print("rooks login — how should rooks reach a model?")
    print("  a. connect to a running Workbench (recommended: uses its keys and free tiers)")
    print("  b. paste a provider API key (OpenAI, Anthropic, OpenRouter, Groq, Gemini, xai, DeepSeek, Mistral)")
    print("  c. Ollama on this machine (fully offline)")
    choice = _ask("a, b or c", "a").lower()[:1]
    values = {"a": _connect_workbench, "b": _paste_key, "c": _ollama}.get(choice, lambda: {})()
    if not values:
        return 1
    p = _write_toml(values)
    print(f"wrote {p}")
    print("check it with: rooks doctor")
    return 0
