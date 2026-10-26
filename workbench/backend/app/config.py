import os
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "workbench.db"
STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))

APP_SECRET = os.getenv("APP_SECRET", "")
TOOLS_API_KEY = os.getenv("TOOLS_API_KEY", "")
WORKSPACE_NAME = os.getenv("WORKSPACE_NAME", "AI Workspace")
CHAT_URL = os.getenv("CHAT_URL", "http://localhost:3000")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080").rstrip("/")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3:4b")
COOKIE_SECURE = _bool("COOKIE_SECURE")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
CALENDAR_SYNC_MINUTES = int(os.getenv("CALENDAR_SYNC_MINUTES", "15"))

SEED_KEYS = {
    "openai": os.getenv("OPENAI_API_KEY", ""),
    "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
    "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
}

if len(APP_SECRET) < 32:
    raise RuntimeError("APP_SECRET must be at least 32 characters. Run ./setup.sh to generate one.")
if len(TOOLS_API_KEY) < 24:
    raise RuntimeError("TOOLS_API_KEY must be at least 24 characters. Run ./setup.sh to generate one.")
