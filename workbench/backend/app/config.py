import os
import secrets
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _load_or_generate_secret(env_name: str, data_dir: Path, min_len: int, filename: str) -> str:
    """Load secret from env var, or from data dir, or generate a new one and persist it.
    Explicit env var always wins. Generated secret is stored with mode 0o600.
    """
    env_val = os.getenv(env_name, "")
    if env_val:
        if len(env_val) < min_len:  # a weak secret given on purpose is refused, never silently replaced
            raise RuntimeError(f"{env_name} must be at least {min_len} characters. Run ./setup.sh to generate one.")
        return env_val
    secret_path = data_dir / filename
    try:
        stored = secret_path.read_text().strip()
    except OSError:
        stored = ""
    if len(stored) >= min_len:
        return stored
    secret = secrets.token_urlsafe(min_len * 2)[: min_len * 2]
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never readable by others, not even briefly
    with os.fdopen(fd, "w") as f:
        f.write(secret)
    return secret


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "workbench.db"
STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))

APP_SECRET = _load_or_generate_secret("APP_SECRET", DATA_DIR, 32, "app_secret")
TOOLS_API_KEY = _load_or_generate_secret("TOOLS_API_KEY", DATA_DIR, 24, "tools_api_key")
def _system_timezone() -> str:
    """This computer's IANA zone (Australia/Sydney...), so schedules follow the user's clock out of the box.
    TZ when it names a zone, else where /etc/localtime points (macOS, Linux). UTC when neither says (Docker, Windows);
    the browser's zone is then saved at setup (api.setup)."""
    tz = os.getenv("TZ", "").lstrip(":")
    if "/" in tz and not tz.startswith("/"):
        return tz
    try:
        real = os.path.realpath("/etc/localtime")
    except OSError:
        return "UTC"
    return real.split("zoneinfo/", 1)[1] if "zoneinfo/" in real else "UTC"


TIMEZONE = os.getenv("TZ_NAME") or os.getenv("WORKSPACE_TIMEZONE") or _system_timezone()
WORKSPACE_NAME = os.getenv("WORKSPACE_NAME", "Rooks")
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
CODING_BRIDGE_KEY = os.getenv("CODING_BRIDGE_KEY", "")  # lets the Rooks terminal agent use this app's models; filled in on first job if empty
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
    "nvidia": os.getenv("NVIDIA_API_KEY", ""),
    "cerebras": os.getenv("CEREBRAS_API_KEY", ""),
    "together": os.getenv("TOGETHER_API_KEY", ""),
    "fireworks": os.getenv("FIREWORKS_API_KEY", ""),
}


CODEX_URL = os.getenv("CODEX_URL", "").rstrip("/")
CODEX_API_KEY = os.getenv("CODEX_API_KEY", "")
