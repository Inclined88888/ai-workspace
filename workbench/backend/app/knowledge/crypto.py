"""Encryption at rest for the knowledge layer (feat/memory-core).

One random 32-byte *data key* protects every sensitive column (`*_enc`) and the blind search index:

* Desktop app (WORKBENCH_SECRET_BACKEND=keychain, or any machine with a working OS keychain and
  KNOWLEDGE_KEY_BACKEND=keychain): the data key lives in the OS keychain (macOS Keychain, Windows Credential Manager,
  Secret Service) under service "ai-workspace", name "knowledge-data-key". The database never holds it.
* Docker / server installs (no keychain): the data key is derived from APP_SECRET, like the rest of Workbench.

Which one was chosen is written once to settings (`kn_key_source`) together with a check value, so a later run never
silently switches keys and makes old rows unreadable; if the keychain entry disappears, `status()` says so in plain
English instead of returning garbage.

Search never needs plaintext on disk: words are turned into keyed tokens (HMAC of the stemmed word, "blind index"), so
the FTS5 table can rank with BM25 while a copied database file reveals no words.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading

from cryptography.fernet import Fernet, InvalidToken

from .. import config, db

KEY_NAME = "knowledge-data-key"
SERVICE = "ai-workspace"
_lock = threading.Lock()
_cache: dict = {}


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    try:
        kr = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        return None
    if isinstance(kr, fail.Keyring):
        return None
    return keyring


def _want_keychain() -> bool:
    pref = os.getenv("KNOWLEDGE_KEY_BACKEND", "").strip().lower()
    if pref in ("app_secret", "fernet", "off"):
        return False
    return (pref == "keychain" or config.SECRET_BACKEND == "keychain") and _keyring() is not None


def _derived() -> bytes:
    return hashlib.sha256(b"knowledge:" + config.APP_SECRET.encode()).digest()


def _check_value(key: bytes) -> str:
    return hmac.new(key, b"knowledge-key-check", hashlib.sha256).hexdigest()[:16]


def _load() -> tuple[bytes, str]:
    source = db.get_setting("kn_key_source")
    if source is None:
        source = "keychain" if _want_keychain() else "app_secret"
        if source == "keychain":
            raw = secrets.token_bytes(32)
            _keyring().set_password(SERVICE, KEY_NAME, base64.urlsafe_b64encode(raw).decode())
        else:
            raw = _derived()
        db.set_setting("kn_key_source", source)
        db.set_setting("kn_key_check", _check_value(raw))
        return raw, source
    if source == "keychain":
        kr = _keyring()
        stored = kr.get_password(SERVICE, KEY_NAME) if kr else None
        if not stored:
            raise KeyMissing("Your memory is locked with a key in this computer's keychain, and that key can't be found. "
                             "Restore a backup made on this computer, or sign in to the same user account.")
        raw = base64.urlsafe_b64decode(stored)
    else:
        raw = _derived()
    check = db.get_setting("kn_key_check")
    if check and check != _check_value(raw):
        raise KeyMissing("The key that protects your memory has changed (APP_SECRET or the keychain entry). "
                         "Put the old one back or restore a backup.")
    return raw, source


class KeyMissing(RuntimeError):
    pass


def _state() -> dict:
    k = str(config.DB_PATH)
    with _lock:
        if _cache.get("db") != k:
            raw, source = _load()
            _cache.clear()
            _cache.update(db=k, raw=raw, source=source,
                          fernet=Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"enc:" + raw).digest())),
                          blind=hashlib.sha256(b"blind:" + raw).digest())
        return _cache


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def seal(value) -> str:
    if value is None or value == "":
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return _state()["fernet"].encrypt(text.encode()).decode()


def open_text(value: str) -> str:
    if not value:
        return ""
    try:
        return _state()["fernet"].decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def open_json(value: str, default=None):
    t = open_text(value)
    if not t:
        return default
    try:
        return json.loads(t)
    except ValueError:
        return default


def blind(token: str) -> str:
    """Keyed token for the search index: same word -> same token, but the word can't be read back."""
    return "k" + hmac.new(_state()["blind"], token.encode(), hashlib.sha256).hexdigest()[:12]


def status() -> dict:
    try:
        s = _state()
        where = "this computer's keychain" if s["source"] == "keychain" else "your workspace secret (APP_SECRET)"
        return {"ok": True, "key_source": s["source"], "summary": f"Your memory is encrypted on disk. The key is kept in {where}."}
    except KeyMissing as e:
        return {"ok": False, "key_source": db.get_setting("kn_key_source"), "summary": str(e)}
