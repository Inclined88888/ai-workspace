import os
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "workbench.db"
STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))

APP_SECRET = os.getenv("APP_SECRET", "")
TOOLS_API_KEY = os.getenv("TOOLS_API_KEY", "")
TIMEZONE = os.getenv("TZ_NAME", os.getenv("WORKSPACE_TIMEZONE", "UTC"))
WORKSPACE_NAME = os.getenv("WORKSPACE_NAME", "AI Workspace")
CHAT_URL = os.getenv("CHAT_URL", "")  # Open WebUI add-on link, empty when not installed
SEARXNG_URL = os.getenv("SEARXNG_URL", "").rstrip("/")  # optional add-on
OLLAMA_URL = os.getenv("OLLAMA_URL", "").rstrip("/")  # optional: add-on container or native app on the host
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3:4b")
COOKIE_SECURE = _bool("COOKIE_SECURE")
CLAUDE_CODE_URL = os.getenv("CLAUDE_CODE_URL", "").rstrip("/")  # optional official CLI launcher
CLAUDE_CODE_API_KEY = os.getenv("CLAUDE_CODE_API_KEY", "")
BROWSER_URL = os.getenv("BROWSER_URL", "").rstrip("/")  # optional add-on
BROWSER_API_KEY = os.getenv("BROWSER_API_KEY", "")
DESK_URL = os.getenv("DESK_URL", "").rstrip("/")  # optional add-on: Rooks Desk, computer use on this machine
DESK_API_KEY = os.getenv("DESK_API_KEY", "")
PAPERCLIP_URL = os.getenv("PAPERCLIP_URL", "")
HERMES_URL = os.getenv("HERMES_URL", "")
# Desktop app (desktop/): the native shell runs this backend as a sidecar on 127.0.0.1 for one local user.
DESKTOP = _bool("WORKBENCH_DESKTOP")
DESKTOP_TOKEN = os.getenv("WORKBENCH_DESKTOP_TOKEN", "")  # one-time per launch; the shell opens /api/desktop/session with it
SECRET_BACKEND = os.getenv("WORKBENCH_SECRET_BACKEND", "fernet")  # "keychain": provider keys live in the OS keychain
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
CALENDAR_SYNC_MINUTES = int(os.getenv("CALENDAR_SYNC_MINUTES", "15"))

SEED_KEYS = {
    "openai": os.getenv("OPENAI_API_KEY", ""),
    "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
    "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
    "gemini": os.getenv("GEMINI_API_KEY", ""),
    "groq": os.getenv("GROQ_API_KEY", ""),
    "xai": os.getenv("XAI_API_KEY", ""),
}

if len(APP_SECRET) < 32:
    raise RuntimeError("APP_SECRET must be at least 32 characters. Run ./setup.sh to generate one.")
if len(TOOLS_API_KEY) < 24:
    raise RuntimeError("TOOLS_API_KEY must be at least 24 characters. Run ./setup.sh to generate one.")


CODEX_URL = os.getenv("CODEX_URL", "").rstrip("/")
CODEX_API_KEY = os.getenv("CODEX_API_KEY", "")
