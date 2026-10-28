"""Where the CLI keeps its server address and token.

The token is a password. It is never taken on the command line (it would land in shell history); it is read from a
hidden prompt, from standard input (`rooks connect --token-stdin`), or from the ROOKS_TOKEN environment variable.
It is kept in the system keychain when the optional `keyring` package is installed, otherwise in a file only you can
read (mode 0600 on macOS and Linux; on Windows, inside your profile, which other users cannot read by default).
"""

import json
import os
import sys
from pathlib import Path

SERVICE = "rooks-cli"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "rooks"


def config_path() -> Path:
    return config_dir() / "config.json"


def _keyring():
    if os.environ.get("ROOKS_NO_KEYRING"):
        return None
    try:
        import keyring  # type: ignore[import-not-found]
        from keyring.backends import fail  # type: ignore[import-not-found]

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:  # noqa: BLE001 - no keychain: fall back to the private file
        return None


def load() -> dict:
    """{"url", "token", "token_store", "conversation_id", "project_id"}; environment variables win."""
    try:
        cfg = json.loads(config_path().read_text())
    except (OSError, ValueError):
        cfg = {}
    if cfg.get("token_store") == "keyring":
        kr = _keyring()
        cfg["token"] = (kr.get_password(SERVICE, cfg.get("url", "")) if kr else None) or ""
    if os.environ.get("ROOKS_URL"):
        cfg["url"] = os.environ["ROOKS_URL"]
    if os.environ.get("ROOKS_TOKEN"):
        cfg["token"] = os.environ["ROOKS_TOKEN"]
    return cfg


def save(cfg: dict) -> str:
    """Writes the settings; returns where the token went ("keyring" or the file path)."""
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in cfg.items() if k not in ("token",)}
    where = str(config_path())
    if cfg.get("token"):
        kr = _keyring()
        if kr:
            kr.set_password(SERVICE, cfg["url"], cfg["token"])
            data["token_store"] = "keyring"
            where = "the system keychain"
        else:
            data["token_store"] = "file"
            data["token"] = cfg["token"]
    _write_private(config_path(), json.dumps(data, indent=1))
    return where


def update(**changes) -> None:
    """Remember the current conversation or project. Only the stored file changes; a token from ROOKS_TOKEN is never written."""
    stored = _stored()
    stored.update(changes)
    config_dir().mkdir(parents=True, exist_ok=True)
    _write_private(config_path(), json.dumps(stored, indent=1))


def _stored() -> dict:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


def forget() -> None:
    cfg = _stored()
    if cfg.get("token_store") == "keyring":
        kr = _keyring()
        if kr:
            try:
                kr.delete_password(SERVICE, cfg.get("url", ""))
            except Exception:  # noqa: BLE001 - already gone
                pass
    try:
        config_path().unlink()
    except OSError:
        pass


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    if sys.platform != "win32":
        os.chmod(path, 0o600)
