"""rooks login: connect a model in one step, check it really answers, and remember the choice.

  rooks login                       menu: paste a key (provider found from its shape), an AI Workspace server, or Ollama
  rooks login -P nvidia             a provider by name; the key is asked for without echo
  rooks login -P workbench --url http://localhost:3100
  echo "$KEY" | rooks login -P groq --key-stdin

A key is checked with one tiny request before anything is saved, then kept in rooks-keys.json next to the config
(mode 600). The config file only records which provider (and model) to use. An environment variable always wins.
"""

from __future__ import annotations

import getpass
import os
import re
import sys
from pathlib import Path

from . import config, providers

PREFIXES = (("sk-ant-", "anthropic"), ("sk-or-", "openrouter"), ("AIza", "gemini"), ("gsk_", "groq"), ("xai-", "xai"),
            ("nvapi-", "nvidia"), ("fw_", "fireworks"), ("wb_", "workbench"))
FREE = "free keys: NVIDIA https://build.nvidia.com, Groq https://console.groq.com/keys, Gemini https://aistudio.google.com/apikey"


def detect(key: str) -> str | None:
    """Provider from the key's shape. "sk-" alone is OpenAI or DeepSeek, so it is asked about (OpenAI when it can't)."""
    k = key.strip()
    for prefix, name in PREFIXES:
        if k.startswith(prefix):
            return name
    if k.startswith("sk-"):
        return _ask("Is this an OpenAI or a DeepSeek key? [o]penai / [d]eepseek: ", {"o": "openai", "d": "deepseek"}, "openai")
    return None


def _interactive() -> bool:
    return sys.stdin.isatty()


def _ask(prompt: str, options: dict[str, str], default: str) -> str:
    if not _interactive():
        return default
    while True:
        a = input(prompt).strip().lower()[:1]
        if not a:
            return default
        if a in options:
            return options[a]


def _read_key(label: str, from_stdin: bool) -> str:
    if from_stdin or not _interactive():
        return sys.stdin.readline().strip()
    return getpass.getpass(f"{label} (input hidden): ").strip()


def update_config(path: Path, values: dict[str, str]) -> None:
    """Set top level keys in rooks.toml, keeping every other line (comments, sections) as it was."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else config.EXAMPLE.splitlines()
    first_section = next((i for i, l in enumerate(lines) if l.strip().startswith("[")), len(lines))
    pattern = re.compile(r"^\s*(" + "|".join(map(re.escape, values)) + r")\s*=")
    head = [l for l in lines[:first_section] if not pattern.match(l)]
    new = [f'{k} = "{v}"' for k, v in values.items() if v]
    out = new + head + lines[first_section:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _check_model(s) -> str:
    """'' when the model answers a tiny request, else the reason it did not."""
    try:
        model = providers.make(s)
        model.timeout = 60
        r = model.complete("Reply with the single word: ready", [{"role": "user", "content": "ping"}], [])
    except providers.ProviderError as e:
        return str(e)
    return "" if (r.text or r.tool_calls or r.tokens_out) else "the model answered with nothing"


def _check_workbench(url: str, key: str) -> str:
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"{url}/v1/models", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            models = json.load(r).get("data") or []
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return ("the server refused that key. Use the token from Settings > Terminal access, or BROWSER_API_KEY "
                    "from the .env next to docker-compose.yml")
        return f"{url} answered {e.code}"
    except (OSError, ValueError) as e:
        return f"cannot reach {url}: {getattr(e, 'reason', e)}"
    return "" if models else "the server has no model connected yet: add one in the app under Models"


def main(a, root: Path) -> int:
    name = a.provider
    if not name:
        if not _interactive():
            print("rooks login: pass -P PROVIDER (and --key-stdin) when not at a terminal", file=sys.stderr)
            return 2
        print("Connect a model:\n  1  paste an API key (the provider is recognised from it)\n"
              "  2  my AI Workspace server (Docker or remote)\n  3  Ollama on this computer")
        name = _ask("Choose [1]/2/3: ", {"1": "key", "2": "workbench", "3": "ollama"}, "key")
    key = ""
    if name == "key":
        print(FREE)
        key = _read_key("API key", a.key_stdin)
        name = detect(key) or ""
        if not name:
            print("rooks login: could not tell which provider that key is for. Run again with -P PROVIDER, one of: "
                  + ", ".join(n for n, p in config.PROVIDERS.items() if p.get("key_env") and p["kind"] != "delegate"), file=sys.stderr)
            return 2
        print(f"looks like a {name} key")
    if name not in config.PROVIDERS:
        print(f"rooks login: unknown provider {name!r}; built in: {', '.join(config.PROVIDERS)}", file=sys.stderr)
        return 2
    if config.PROVIDERS[name]["kind"] == "delegate":
        print(f"{name} uses its own sign-in: run `{'claude' if name == 'claude-code' else 'codex'}` once to log in, "
              f"then rooks -P {name} \"task\"")
        return 0
    values = {"provider": name, "model": a.model or ""}
    if name == "workbench":
        url = (a.url or config.docker_workbench() or "").rstrip("/")
        if not url and _interactive():
            url = (input(f"Server URL [{config.DOCKER_URL}]: ").strip() or config.DOCKER_URL).rstrip("/")
        url = url or config.DOCKER_URL
        key = key or _read_key("Token from Settings > Terminal access (or BROWSER_API_KEY from .env)", a.key_stdin)
        problem = _check_workbench(url, key)
        if problem:
            print(f"rooks login: {problem}", file=sys.stderr)
            return 1
        values["workbench_url"] = url
    elif config.PROVIDERS[name].get("local"):
        pass  # nothing to keep: a local server needs no key
    else:
        key = key or _read_key(f"{name} API key", a.key_stdin)
        if not key:
            print("rooks login: no key given", file=sys.stderr)
            return 2
    if name != "workbench":
        s = config.resolve({"provider": name, "model": a.model}, root, file={})
        s.key = key
        if os.environ.get(s.key_env or "-"):
            s.key_env = ""  # check the key just given, not the one already in the environment
        problem = _check_model(s)
        if problem:
            print(f"rooks login: {name} did not answer: {problem}", file=sys.stderr)
            return 1
        print(f"{name}/{s.model} answered")
    if key:
        print(f"key saved in {config.store_key(name, key)} (only you can read it)")
    update_config(config.config_path(), values)
    print(f"rooks will use {name} from now on ({config.config_path()})")
    env = config.PROVIDERS[name].get("key_env")
    if env and os.environ.get(env) and os.environ.get(env) != key:
        print(f"note: {env} is also set in this shell and wins over the saved key")
    return 0
