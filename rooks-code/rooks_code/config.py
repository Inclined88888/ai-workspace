"""Settings: built-in defaults < config file < environment < command-line flags.

Config file (TOML): $ROOKS_CONFIG, else ~/.config/ai-workspace/rooks.toml (Windows: %APPDATA%\\ai-workspace\\rooks.toml).
API keys are never stored by Rooks: each provider names the environment variable that holds its key.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# kind: "openai" = OpenAI Chat Completions protocol (also OpenRouter, Ollama, LM Studio, vLLM, llama.cpp, Groq, ...)
#       "anthropic" = Anthropic Messages protocol
PROVIDERS: dict[str, dict] = {
    "openai": {"kind": "openai", "base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY", "model": "gpt-4.1-mini"},
    "anthropic": {"kind": "anthropic", "base_url": "https://api.anthropic.com/v1", "key_env": "ANTHROPIC_API_KEY",
                  "model": "claude-sonnet-4-5"},
    "openrouter": {"kind": "openai", "base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
                   "model": "openai/gpt-4.1-mini"},
    "ollama": {"kind": "openai", "base_url": "http://localhost:11434/v1", "key_env": "", "model": "qwen2.5-coder:7b",
               "local": True},
    "lmstudio": {"kind": "openai", "base_url": "http://localhost:1234/v1", "key_env": "", "model": "local-model",
                 "local": True},
    # any other OpenAI-compatible server: set base_url (and key_env if it needs a key)
    "openai-compatible": {"kind": "openai", "base_url": "", "key_env": "ROOKS_API_KEY", "model": "",
                          "key_optional": True},
}


def config_path() -> Path:
    if os.environ.get("ROOKS_CONFIG"):
        return Path(os.environ["ROOKS_CONFIG"])
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "ai-workspace" / "rooks.toml"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "ai-workspace" / "rooks.toml"


@dataclass
class Settings:
    provider: str = "ollama"
    model: str = ""
    base_url: str = ""
    key_env: str = ""
    kind: str = "openai"
    local: bool = False
    key_optional: bool = False
    max_usd: float | None = 1.0          # hard cap per session; None = no dollar cap
    max_tokens: int | None = 200_000     # hard cap per session (input + output)
    max_steps: int = 40                  # model calls per prompt before stopping
    command_timeout: int = 120
    trusted_folders: list[str] = field(default_factory=list)
    auto_approve: bool = False
    dry_run: bool = False
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    prices: dict[str, list[float]] = field(default_factory=dict)  # model -> [usd per 1M in, usd per 1M out]
    workbench_url: str = ""
    root: Path = field(default_factory=Path.cwd)

    @property
    def api_key(self) -> str:
        return os.environ.get(self.key_env, "") if self.key_env else ""

    def trusted(self) -> bool:
        root = self.root.resolve()
        for folder in self.trusted_folders:
            f = Path(os.path.expanduser(folder)).resolve()
            if root == f or f in root.parents:
                return True
        return False


def load_file(path: Path | None = None) -> dict:
    p = path or config_path()
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"rooks: cannot read {p}: {e}")


def resolve(flags: dict, root: Path, file: dict | None = None) -> Settings:
    """Build settings from the file, ROOKS_* environment variables and flags (flags win). Unknown providers are errors."""
    cfg = load_file() if file is None else file
    s = Settings(root=root)
    for key in ("max_usd", "max_tokens", "max_steps", "command_timeout", "trusted_folders", "auto_approve", "workbench_url"):
        if key in cfg:
            setattr(s, key, cfg[key])
    s.prices = dict(cfg.get("prices", {}))
    s.mcp_servers = dict(cfg.get("mcp", {}).get("servers", {}))
    s.mcp_servers.update(_project_mcp(root))
    env = {"provider": os.environ.get("ROOKS_PROVIDER"), "model": os.environ.get("ROOKS_MODEL"),
           "base_url": os.environ.get("ROOKS_BASE_URL")}
    name = flags.get("provider") or env["provider"] or cfg.get("provider") or "ollama"
    custom = cfg.get("providers", {}).get(name, {})
    if name not in PROVIDERS and not custom:
        raise SystemExit(f"rooks: unknown provider {name!r}. Built in: {', '.join(PROVIDERS)}; or add [providers.{name}] to "
                         f"{config_path()}")
    p = {**PROVIDERS.get(name, PROVIDERS["openai-compatible"]), **custom}
    s.provider, s.kind, s.key_env, s.local = name, p.get("kind", "openai"), p.get("key_env", ""), bool(p.get("local"))
    s.key_optional = bool(p.get("key_optional", s.local))
    s.base_url = (flags.get("base_url") or env["base_url"] or p.get("base_url") or "").rstrip("/")
    cfg_model = cfg.get("model", "") if name == cfg.get("provider") else ""  # a model named for another provider never leaks
    s.model = flags.get("model") or env["model"] or cfg_model or p.get("model", "")
    if s.provider == "openai-compatible" and not s.base_url:
        raise SystemExit("rooks: provider openai-compatible needs --base-url (or base_url in the config)")
    if not s.model:
        raise SystemExit(f"rooks: no model set for provider {name}; pass --model")
    for key in ("max_usd", "max_tokens", "max_steps"):
        if flags.get(key) is not None:
            setattr(s, key, flags[key] if flags[key] != 0 else None)
    if s.max_steps is None:
        s.max_steps = 40
    if flags.get("yes"):
        s.auto_approve = True
    if flags.get("dry_run"):
        s.dry_run = True
    return s


def _project_mcp(root: Path) -> dict:
    """A project's .mcp.json ({"mcpServers": {...}}, the format Claude Code and Cursor use) adds servers for that folder."""
    import json

    f = root / ".mcp.json"
    if not f.is_file():
        return {}
    try:
        return {k: v for k, v in json.loads(f.read_text()).get("mcpServers", {}).items() if v.get("command")}
    except (ValueError, AttributeError):
        return {}


EXAMPLE = '''# Rooks Code settings. API keys are read from the environment variables named here; never put a key in this file.
provider = "ollama"                 # openai | anthropic | openrouter | ollama | lmstudio | openai-compatible | your own
# model = "qwen2.5-coder:7b"
max_usd = 1.0                       # hard cap per session, in US dollars (0 = no cap)
max_tokens = 200000                 # hard cap per session (0 = no cap)
trusted_folders = []                # edits and commands inside these folders run without asking
# workbench_url = "http://localhost:3100"

# [providers.groq]                  # any OpenAI-compatible endpoint
# kind = "openai"
# base_url = "https://api.groq.com/openai/v1"
# key_env = "GROQ_API_KEY"
# model = "llama-3.3-70b-versatile"

# [mcp.servers.filesystem]          # stdio MCP servers; tools appear as mcp__<server>__<tool>
# command = "npx"
# args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
# trusted = false                   # true = its tools run without asking

# [prices]                          # USD per million tokens [input, output], overrides the built-in table
# "my-model" = [0.5, 1.5]
'''
