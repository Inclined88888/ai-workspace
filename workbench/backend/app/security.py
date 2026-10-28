import base64
import hashlib
import hmac
import json
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request

from . import config, db

COOKIE = "wb_session"
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"enc:" + config.APP_SECRET.encode()).digest()))
_sign_key = hashlib.sha256(b"sig:" + config.APP_SECRET.encode()).digest()


# Passwords (scrypt, stdlib)
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(test.hex(), digest)
    except ValueError:
        return False


# Secrets at rest
def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        # APP_SECRET changed since this was stored; the credential must be re-entered.
        return ""


# Sessions: signed, expiring token. Changing the password bumps the generation and logs out all sessions.
def _sign(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(_sign_key, payload, hashlib.sha256).digest()).decode().rstrip("=")


def make_session() -> str:
    gen = db.get_setting("session_generation", "1")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + config.SESSION_DAYS * 86400, "gen": gen}).encode()
    ).decode()
    return f"{payload}.{_sign(payload.encode())}"


def check_session(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload.encode())):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return False
    return data.get("exp", 0) > time.time() and data.get("gen") == db.get_setting("session_generation", "1")


def require_user(request: Request) -> None:
    if not check_session(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Not signed in")


def check_bearer(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    return bool(token) and hmac.compare_digest(token, config.TOOLS_API_KEY)


def require_tool_key(request: Request) -> None:
    if not check_bearer(request):
        raise HTTPException(401, "Invalid tool API key")


# Login throttling (per client IP, in memory)
_attempts: dict[str, list[float]] = {}


def throttle(ip: str) -> None:
    if len(_attempts) > 10_000:  # bound memory: drop clients with no attempt in the window
        for k in [k for k, v in _attempts.items() if not v or v[-1] < time.time() - 300]:
            _attempts.pop(k, None)
    window = [t for t in _attempts.get(ip, []) if t > time.time() - 300]
    _attempts[ip] = window
    if len(window) >= 10:
        raise HTTPException(429, "Too many attempts. Wait a few minutes.")
    window.append(time.time())
