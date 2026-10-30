"""The phone companion app (mobile/): pairing, device tokens, and the small API the phone uses.

Pairing
  1. Signed in on the desktop or web app, Settings -> Phone -> "Pair a phone" calls POST /api/mobile/pair/start.
     It returns an 8 character code (5 minutes, single use; only its hash is stored), the addresses the phone can try,
     and a QR code (SVG) of  aiworkspace://pair?c=<code>&u=<address>&u=<address>&n=<workspace name>.
  2. The phone scans it (or the user types the address and code) and calls POST /api/mobile/pair/claim. The code is
     exchanged once for a long-lived device token, kept in the phone's secure storage (Keychain / Android Keystore).
     Workbench keeps only its sha256. Settings lists paired phones and revokes any of them.
  3. Every other call sends  Authorization: Bearer <device token>  (and X-Workbench: 1 on writes, like the web app).

Transport
  Addresses are tried in order, first answer wins (GET /api/mobile/ping):
    * LAN: the desktop app serves only /api/mobile/* and /healthz on 0.0.0.0:<WORKBENCH_MOBILE_PORT, 8766> when
      "Phone access on this Wi-Fi" is on (lan_start below). The Docker install is reached on the host's address
      (BIND_ADDRESS=0.0.0.0, or list it in MOBILE_URLS).
    * Relay: anything that gives this Workbench a URL the phone can reach away from home, set as `mobile_relay_url`
      (Settings) or MOBILE_RELAY_URL: a Tailscale MagicDNS name (https://<machine>.<tailnet>.ts.net via `tailscale
      serve`), a Cloudflare Tunnel, or a small relay. The phone only needs a base URL; the device token is the same.
  See mobile/README.md -> "Away from home".

The device token is scoped: it reaches only the routes in `device` below, never Settings, keys or the password.
"""

import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import socket
import threading
import time
import uuid
from urllib.parse import quote

import segno
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import actions, assistant, config, db, push
from .security import require_user

CODE_TTL = 300
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
LAN_PORT = int(os.getenv("WORKBENCH_MOBILE_PORT", "8766"))
UPLOADS = config.DATA_DIR / "mobile_uploads"
MAX_UPLOAD = 10 * 1024 * 1024


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalise_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


# Who is calling: a paired phone
def require_device(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token.startswith("wbd_"):
        raise HTTPException(401, "This phone is not paired. Pair it again from Settings -> Phone.")
    row = db.one("SELECT * FROM devices WHERE token_hash = ?", _hash(token))
    if not row or not hmac.compare_digest(row["token_hash"], _hash(token)):
        raise HTTPException(401, "This phone is not paired. Pair it again from Settings -> Phone.")
    db.run("UPDATE devices SET last_seen = ? WHERE id = ?", db.now(), row["id"])
    return row


public = APIRouter(prefix="/api/mobile")
owner = APIRouter(prefix="/api/mobile", dependencies=[Depends(require_user)])
device = APIRouter(prefix="/api/mobile")


# Addresses the phone can try
def lan_addresses() -> list[str]:
    """Private IPv4 addresses of this machine. The UDP connect sends no packet; it only asks the OS for a route."""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        found += [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    out = []
    for a in found:
        ip = ipaddress.ip_address(a)
        if ip.is_private and not ip.is_loopback and a not in out:
            out.append(a)
    return out


def relay_url() -> str:
    return (db.get_setting("mobile_relay_url") or os.getenv("MOBILE_RELAY_URL", "")).strip().rstrip("/")


def addresses(request: Request) -> list[str]:
    """In the order the phone should try them: configured, LAN, the address this page was opened on, relay."""
    urls = [u.strip().rstrip("/") for u in os.getenv("MOBILE_URLS", "").split(",") if u.strip()]
    if config.DESKTOP:
        if lan_running():
            urls += [f"http://{a}:{LAN_PORT}" for a in lan_addresses()]
    else:
        host = request.url.hostname or ""
        if host and host not in ("localhost", "127.0.0.1", "::1"):
            urls.append(str(request.base_url).rstrip("/"))
    if relay_url():
        urls.append(relay_url())
    return list(dict.fromkeys(urls))


def qr_payload(code: str, urls: list[str], name: str) -> str:
    return "aiworkspace://pair?c=" + code + "".join(f"&u={quote(u, safe='')}" for u in urls) + f"&n={quote(name, safe='')}"


def qr_svg(data: str) -> str:
    return segno.make(data, error="m").svg_inline(border=2, dark="#141311", light="#F5F1E8", omitsize=True)


# Owner: pairing and devices (signed in on desktop or web)
@owner.post("/pair/start")
def pair_start(request: Request):
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    now = time.time()
    db.run("DELETE FROM pairing_codes WHERE expires_at < ? OR used = 1", now)
    db.run("INSERT INTO pairing_codes(code_hash, expires_at) VALUES (?, ?)", _hash(code), now + CODE_TTL)
    urls = addresses(request)
    name = db.get_setting("workspace_name", config.WORKSPACE_NAME) or "AI Workspace"
    payload = qr_payload(code, urls, name)
    return {"code": f"{code[:4]}-{code[4:]}", "expires_in": CODE_TTL, "urls": urls, "qr": payload, "qr_svg": qr_svg(payload),
            "lan": lan_status(), "relay_url": relay_url()}


@owner.get("/devices")
def list_devices():
    return db.all("SELECT id, name, platform, created_at, last_seen, push_token != '' AS push FROM devices ORDER BY id DESC")


@owner.delete("/devices/{did}")
def revoke(did: int):
    if not db.run("DELETE FROM devices WHERE id = ?", did):
        raise HTTPException(404, "Device not found")
    return {"ok": True}


class MobileSettings(BaseModel):
    lan_enabled: bool | None = None
    relay_url: str | None = Field(None, max_length=300)
    push_enabled: bool | None = None


@owner.get("/settings")
def get_settings():
    return {"lan": lan_status(), "relay_url": relay_url(), "push_enabled": push.enabled()}


@owner.put("/settings")
def put_settings(data: MobileSettings):
    if data.relay_url is not None:
        url = data.relay_url.strip().rstrip("/")
        if url and not re.match(r"^https?://[^\s/]+", url):
            raise HTTPException(400, "The relay address must start with https:// (or http:// on a private network)")
        db.set_setting("mobile_relay_url", url)
    if data.push_enabled is not None:
        db.set_setting("mobile_push_enabled", "1" if data.push_enabled else "0")
    if data.lan_enabled is not None:
        if data.lan_enabled and not config.DESKTOP:
            raise HTTPException(400, "Phone access on this Wi-Fi is a desktop app switch. For Docker, publish the port on "
                                     "your network (BIND_ADDRESS=0.0.0.0) and list the address in MOBILE_URLS.")
        db.set_setting("mobile_lan_enabled", "1" if data.lan_enabled else "0")
        (lan_start if data.lan_enabled else lan_stop)()
    return get_settings()


# Public: the phone before it has a token
@public.get("/ping")
def ping():
    return {"ok": True, "app": "ai-workspace", "name": db.get_setting("workspace_name", config.WORKSPACE_NAME) or "AI Workspace"}


class ClaimIn(BaseModel):
    code: str = Field(..., min_length=6, max_length=20)
    name: str = Field("Phone", max_length=60)
    platform: str = Field("", pattern="^(ios|android|web|)$")


_failures: dict[str, list[float]] = {}  # wrong codes per client address, in memory


def _check_failures(ip: str) -> None:
    recent = [t for t in _failures.get(ip, []) if t > time.time() - 300]
    _failures[ip] = recent
    if len(recent) >= 10:
        raise HTTPException(429, "Too many wrong codes. Wait a few minutes.")


@public.post("/pair/claim")
def pair_claim(data: ClaimIn, request: Request):
    ip = request.client.host if request.client else "?"
    _check_failures(ip)
    code = normalise_code(data.code)
    with db.tx() as c:  # single use, even when two phones race with the same code
        row = c.execute("SELECT expires_at, used FROM pairing_codes WHERE code_hash = ?", (_hash(code),)).fetchone()
        if not row or row[1] or row[0] < time.time():
            _failures.setdefault(ip, []).append(time.time())
            raise HTTPException(403, "That code is wrong or expired. Show a new one in Settings -> Phone.")
        c.execute("UPDATE pairing_codes SET used = 1 WHERE code_hash = ?", (_hash(code),))
        token = "wbd_" + secrets.token_urlsafe(32)
        cur = c.execute("INSERT INTO devices(name, platform, token_hash, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                        ((data.name.strip() or "Phone")[:60], data.platform, _hash(token), db.now(), db.now()))
        did = cur.lastrowid
    return {"device_token": token, "device_id": did,
            "workspace_name": db.get_setting("workspace_name", config.WORKSPACE_NAME) or "AI Workspace"}


# Device: what the phone may do
@device.get("/me")
def me(d: dict = Depends(require_device)):
    return {"id": d["id"], "name": d["name"], "platform": d["platform"], "push": bool(d["push_token"]),
            "workspace_name": db.get_setting("workspace_name", config.WORKSPACE_NAME) or "AI Workspace",
            "push_enabled_on_desktop": push.enabled()}


@device.delete("/me")
def unpair(d: dict = Depends(require_device)):
    db.run("DELETE FROM devices WHERE id = ?", d["id"])
    return {"ok": True}


class PushIn(BaseModel):
    token: str = Field("", max_length=200)


@device.put("/push-token")
def push_token(data: PushIn, d: dict = Depends(require_device)):
    if data.token and not re.match(r"^(ExponentPushToken|ExpoPushToken)\[[^\]]+\]$", data.token):
        raise HTTPException(400, "Not an Expo push token")
    db.run("UPDATE devices SET push_token = ? WHERE id = ?", data.token, d["id"])
    return {"ok": True, "push_enabled_on_desktop": push.enabled()}


def _run_card(r: dict) -> dict:
    keep = ("id", "ts", "kind", "channel", "outcome", "model", "tool", "conversation_id", "conversation_title", "steps",
            "tool_calls", "failures", "total_usd", "total_tokens_in", "total_tokens_out", "latency_ms", "failure_reason")
    return {k: r.get(k) for k in keep}


@device.get("/home")
def home(_: dict = Depends(require_device)):
    pending = actions.pending()
    return {
        "workspace_name": db.get_setting("workspace_name", config.WORKSPACE_NAME) or "AI Workspace",
        "approvals": pending[:3], "approvals_count": len(pending),
        "running": db.one("SELECT COUNT(*) AS n FROM actions WHERE kind = 'turn' AND outcome = 'running'")["n"],
        "runs": [_run_card(r) for r in actions.runs(5)],
        "tasks": db.all("SELECT id, title, status, priority, due, project FROM tasks WHERE status != 'done' "
                        "ORDER BY priority DESC, COALESCE(due, '9999') ASC, id DESC LIMIT 8"),
        "notes": db.all("SELECT id, title, substr(body, 1, 160) AS excerpt, pinned, updated_at FROM docs "
                        "ORDER BY pinned DESC, updated_at DESC LIMIT 6"),
    }


@device.get("/approvals")
def approvals(_: dict = Depends(require_device)):
    return actions.pending()


class DecisionIn(BaseModel):
    approve: bool


@device.post("/approvals/{aid}")
async def decide(aid: int, data: DecisionIn, _: dict = Depends(require_device)):
    return await assistant.decide(aid, data.approve, channel="mobile")


@device.get("/runs")
def runs(limit: int = 30, before: int | None = None, failed: bool = False, _: dict = Depends(require_device)):
    return {"runs": [_run_card(r) for r in actions.runs(limit, before, "", failed)]}


@device.get("/runs/{aid}")
def run_trace(aid: int, _: dict = Depends(require_device)):
    return actions.trace(aid)


@device.get("/tasks")
def tasks(_: dict = Depends(require_device)):
    return db.all("SELECT id, title, status, priority, due, project FROM tasks ORDER BY status = 'done', priority DESC, id DESC LIMIT 100")


class TaskPatch(BaseModel):
    status: str = Field(..., pattern="^(todo|doing|done)$")


@device.patch("/tasks/{tid}")
def task_status(tid: int, data: TaskPatch, _: dict = Depends(require_device)):
    done_at = db.now() if data.status == "done" else None
    if not db.run("UPDATE tasks SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?", data.status, done_at, db.now(), tid):
        raise HTTPException(404, "Task not found")
    return db.one("SELECT id, title, status, priority, due, project FROM tasks WHERE id = ?", tid)


@device.get("/notes")
def notes(_: dict = Depends(require_device)):
    return db.all("SELECT id, title, substr(body, 1, 400) AS excerpt, pinned, updated_at FROM docs ORDER BY pinned DESC, updated_at DESC LIMIT 50")


@device.get("/conversations")
def conversations(_: dict = Depends(require_device)):
    return db.all("SELECT id, title, channel, updated_at FROM conversations WHERE channel IN ('web', 'cli', 'mobile', 'telegram') "
                  "ORDER BY updated_at DESC LIMIT 50")


@device.get("/conversations/{cid}")
def conversation(cid: int, _: dict = Depends(require_device)):
    from .assistant_api import conversation as full

    return full(cid)


class AttachmentIn(BaseModel):
    kind: str = Field(..., pattern="^(photo|voice)$")
    mime: str = Field(..., pattern=r"^(image|audio)/[a-z0-9.+-]+$")
    name: str = Field("", max_length=120)
    duration_ms: int = Field(0, ge=0)
    data: str = Field(..., min_length=4, max_length=MAX_UPLOAD * 4 // 3 + 8)


EXT = {"image/jpeg": "jpg", "image/png": "png", "image/heic": "heic", "image/webp": "webp", "audio/m4a": "m4a",
       "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac", "audio/webm": "webm", "audio/mpeg": "mp3", "audio/wav": "wav"}


@device.post("/attachments")
def upload(data: AttachmentIn, _: dict = Depends(require_device)):
    """A photo or voice note from the phone, saved under DATA_DIR/mobile_uploads. The chat turn refers to it by id."""
    try:
        raw = base64.b64decode(data.data, validate=True)
    except ValueError as e:
        raise HTTPException(400, "Attachment is not valid base64") from e
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Attachments are limited to 10 MB")
    aid = uuid.uuid4().hex[:16]
    UPLOADS.mkdir(parents=True, exist_ok=True)
    path = UPLOADS / f"{aid}.{EXT.get(data.mime, 'bin')}"
    path.write_bytes(raw)
    return {"id": aid, "kind": data.kind, "file": path.name, "bytes": len(raw), "duration_ms": data.duration_ms}


@device.get("/models")
def models(_: dict = Depends(require_device)):
    """The model picker on the phone: same shape as /api/models, from the cached list (no provider calls)."""
    from .api import models as all_models

    return all_models(False)


class MobileChatIn(BaseModel):
    message: str = Field("", max_length=50_000)
    conversation_id: int | None = None
    model: str | None = Field(None, max_length=200)
    attachments: list[str] = Field(default_factory=list, max_length=6)


def describe_attachments(ids: list[str]) -> str:
    lines = []
    for aid in ids:
        if not re.fullmatch(r"[0-9a-f]{16}", aid):
            raise HTTPException(400, "Unknown attachment")
        found = sorted(UPLOADS.glob(f"{aid}.*")) if UPLOADS.exists() else []
        if not found:
            raise HTTPException(404, "Attachment not found; upload it again")
        f = found[0]
        kind = "Voice note" if f.suffix[1:] in ("m4a", "aac", "webm", "mp3", "wav") else "Photo"
        lines.append(f"[{kind} attached from the phone: {f.name}, {f.stat().st_size // 1024} KB, saved in mobile_uploads. "
                     "Automatic transcription and image reading are not set up yet, so ask me what it shows if you need it.]")
    return "\n".join(lines)


@device.post("/chat")
def chat(data: MobileChatIn, request: Request, _: dict = Depends(require_device)):
    from .assistant_api import ChatIn
    from .assistant_api import chat as run_chat

    text = "\n\n".join(x for x in (data.message.strip(), describe_attachments(data.attachments)) if x)
    if not text:
        raise HTTPException(400, "Say something or attach a photo or voice note")
    return run_chat(ChatIn(message=text, conversation_id=data.conversation_id, model=data.model or None, channel="mobile"),
                    request.headers.get("idempotency-key"))


# LAN listener (desktop app): a second server on 0.0.0.0 that only answers the phone's routes
def lan_gateway(app):
    """Wrap the app so only /api/mobile/* and /healthz are reachable; everything else is 404 on the LAN port."""
    async def gate(scope, receive, send):
        if scope["type"] == "http" and not (scope["path"].startswith("/api/mobile/") or scope["path"] == "/healthz"):
            await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"detail":"Not found"}'})
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await app(scope, receive, send)
    return gate


_lan: dict = {"server": None, "thread": None, "error": ""}


def lan_running() -> bool:
    s = _lan["server"]
    return bool(s and s.started and not s.should_exit)


def lan_status() -> dict:
    return {"available": config.DESKTOP, "enabled": db.get_setting("mobile_lan_enabled") == "1", "running": lan_running(),
            "port": LAN_PORT, "addresses": lan_addresses() if config.DESKTOP else [], "error": _lan["error"]}


def lan_start() -> None:
    if not config.DESKTOP or lan_running():
        return
    import uvicorn

    from .main import app

    server = uvicorn.Server(uvicorn.Config(lan_gateway(app), host="0.0.0.0", port=LAN_PORT, log_level="warning",
                                           lifespan="off", proxy_headers=False))
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    _lan.update(server=server, error="")

    def serve():
        try:
            server.run()
        except (OSError, SystemExit) as e:
            _lan["error"] = f"Port {LAN_PORT} is busy or blocked ({e.__class__.__name__})"

    t = threading.Thread(target=serve, daemon=True, name="mobile-lan")
    _lan["thread"] = t
    t.start()
    for _ in range(30):
        if server.started or not t.is_alive():
            break
        time.sleep(0.05)


def lan_stop() -> None:
    s = _lan["server"]
    if s:
        s.should_exit = True
    _lan.update(server=None, thread=None)


def boot() -> None:
    """At startup: bring the LAN listener back if the owner left it on."""
    if config.DESKTOP and db.get_setting("mobile_lan_enabled") == "1":
        lan_start()
