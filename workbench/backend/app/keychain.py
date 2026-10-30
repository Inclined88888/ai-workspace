"""Provider keys in the operating system keychain (desktop app only).

When WORKBENCH_SECRET_BACKEND=keychain (set by the desktop shell), an API key is written to the OS keychain
(macOS Keychain, Windows Credential Manager, Secret Service on Linux) and the database stores only a reference,
"keychain:<name>". The Docker install never sets it and keeps Fernet encryption under APP_SECRET.

The `keyring` package is imported lazily so the Docker image does not need it.
"""

import logging

from . import config

SERVICE = "ai-workspace"
PREFIX = "keychain:"
log = logging.getLogger("workbench.keychain")


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    kr = keyring.get_keyring()
    if isinstance(kr, fail.Keyring):
        return None
    return keyring


def enabled() -> bool:
    return config.SECRET_BACKEND == "keychain" and _keyring() is not None


def backend_name() -> str:
    """What protects provider keys, for the status page: "os-keychain" or "encrypted-db"."""
    return "os-keychain" if enabled() else "encrypted-db"


def is_ref(value: str) -> bool:
    return bool(value) and value.startswith(PREFIX)


def put(name: str, secret: str) -> str:
    """Store a secret and return the reference to keep in the database."""
    kr = _keyring()
    if kr is None:
        raise RuntimeError("No OS keychain is available")
    kr.set_password(SERVICE, name, secret)
    return PREFIX + name


def get(ref: str) -> str:
    kr = _keyring()
    if kr is None or not is_ref(ref):
        return ""
    try:
        return kr.get_password(SERVICE, ref[len(PREFIX):]) or ""
    except Exception:  # noqa: BLE001 - a locked or missing keychain reads as "key must be re-entered"
        log.warning("Keychain read failed for %s", ref)
        return ""


def delete(ref: str) -> None:
    kr = _keyring()
    if kr is None or not is_ref(ref):
        return
    try:
        kr.delete_password(SERVICE, ref[len(PREFIX):])
    except Exception:  # noqa: BLE001 - already gone
        pass
