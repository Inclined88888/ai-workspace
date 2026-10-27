import os
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "workbench.db"
STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))

APP_SECRET = os.getenv("APP_SECRET", "")
TOOLS_API_KEY = os.getenv("TOOLS_API_KEY", "")
def resolve_timezone(name: str | None) -> str:
    """An IANA zone name. A bare city typed during setup ("brisbane") becomes its zone ("Australia/Brisbane");
    anything unrecognised falls back to UTC instead of being silently ignored later."""
    from zoneinfo import ZoneInfo, available_timezones

    name = (name or "").strip()
    try:
        if name:
            ZoneInfo(name)
            return name
    except (ValueError, KeyError, OSError):
        pass
    key = name.lower().replace(" ", "_")
    matches = sorted(z for z in available_timezones() if z.rsplit("/", 1)[-1].lower() == key and not z.startswith(("posix/", "right/")))
    return matches[0] if len(matches) == 1 else "UTC"


TIMEZONE = resolve_timezone(os.getenv("TZ_NAME", os.getenv("WORKSPACE_TIMEZONE", "UTC")))
PRODUCT_NAME = "ROOKS"
# Installs from before the rename stored the old default name in .env and in settings; show those as ROOKS.
LEGACY_DEFAULT_NAMES = {"AI Workspace", "AI workspace"}


def display_name(name: str | None) -> str:
    name = (name or "").strip()
    return PRODUCT_NAME if not name or name in LEGACY_DEFAULT_NAMES else name


WORKSPACE_NAME = display_name(os.getenv("WORKSPACE_NAME"))
CHAT_URL = os.getenv("CHAT_URL", "")  # Open WebUI add-on link, empty when not installed
SEARXNG_URL = os.getenv("SEARXNG_URL", "").rstrip("/")  # optional add-on
OLLAMA_URL = os.getenv("OLLAMA_URL", "").rstrip("/")  # optional: add-on container or native app on the host
LLAMACPP_URL = os.getenv("LLAMACPP_URL", "").rstrip("/")  # optional: a llama.cpp llama-server, for the llama.cpp preset
# The assistant's terminal and workspace files run in a separate runner service (workbench/runner)
RUNNER_URL = os.getenv("RUNNER_URL", "").rstrip("/")
RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3:4b")
COOKIE_SECURE = _bool("COOKIE_SECURE")
BROWSER_URL = os.getenv("BROWSER_URL", "").rstrip("/")  # optional add-on
BROWSER_API_KEY = os.getenv("BROWSER_API_KEY", "")
PAPERCLIP_URL = os.getenv("PAPERCLIP_URL", "")
HERMES_URL = os.getenv("HERMES_URL", "")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
CALENDAR_SYNC_MINUTES = int(os.getenv("CALENDAR_SYNC_MINUTES", "15"))

SEED_KEYS = {
    "openai": os.getenv("OPENAI_API_KEY", ""),
    "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
    "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
    "gemini": os.getenv("GEMINI_API_KEY", ""),
}

if len(APP_SECRET) < 32:
    raise RuntimeError("APP_SECRET must be at least 32 characters. Run ./setup.sh to generate one.")
if len(TOOLS_API_KEY) < 24:
    raise RuntimeError("TOOLS_API_KEY must be at least 24 characters. Run ./setup.sh to generate one.")
