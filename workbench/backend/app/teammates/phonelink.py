"""Encrypted phone link (feat/grok-style): the phone talks to this computer end to end, not through a chat app.

Keys
  Pairing (mobile.py) gives the phone a device token. The phone then calls POST /api/mobile/link/keys with its X25519
  public key; this computer answers with its own public key, a computer id and a random mailbox id. Both sides derive
      k = HKDF-SHA256(X25519(priv, peer_pub), salt=mailbox, info="ai-workspace link v1")
  and seal every message with ChaCha20-Poly1305 (12-byte random nonce, AAD = mailbox + direction "p2c"/"c2p").
Envelope (JSON): {"v": 1, "mb": mailbox, "n": b64 nonce, "c": b64 ciphertext}. Inside, the phone sends
  {"seq", "ts", "token": device token, "method", "path", "body"} - a normal /api/mobile/* request - and gets back
  {"seq", "status", "body"}. Replays are refused: seq must grow and ts must be within 10 minutes.
Transport
  * LAN first: POST /api/mobile/link/sealed directly (the LAN listener already serves /api/mobile/*).
  * Away from home: the ai-workspace-cloud relay keeps a mailbox per pairing and only ever holds ciphertext. This
    computer long-polls its mailbox (relay_loop) and posts sealed answers back. The relay can't read or forge anything.
Also here: computers list for the phone, two-way handoff, read-only browsing of folders you choose, and the offline
queue's idempotent replay (the phone keeps the queue; every queued request carries an Idempotency-Key).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException

from .. import config, db
from ..security import decrypt, encrypt

log = logging.getLogger("teammates.phonelink")
INFO = b"ai-workspace link v1"
MAX_SKEW = 600
SEALED_PATHS = ("/api/mobile/",)
_relay = {"running": False, "error": "", "last": ""}

SCHEMA = """
CREATE TABLE IF NOT EXISTS link_peers (
  device_id INTEGER PRIMARY KEY,
  phone_pub TEXT NOT NULL,
  mailbox TEXT NOT NULL,
  relay_token_enc TEXT NOT NULL DEFAULT '',
  last_seq_in INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS link_handoffs (
  id INTEGER PRIMARY KEY,
  direction TEXT NOT NULL,           -- to_phone | to_computer
  conversation_id INTEGER,
  teammate_id INTEGER,
  draft TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'waiting',  -- waiting | picked_up
  created_at TEXT NOT NULL
);
"""
_ready: set[str] = set()


def ensure() -> None:
    key = str(config.DB_PATH)
    if key not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(key)


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# This computer's identity -----------------------------------------------------------------------------------------
def computer_id() -> str:
    cid = db.get_setting("link_computer_id", "") or ""
    if not cid:
        cid = "c_" + secrets.token_hex(8)
        db.set_setting("link_computer_id", cid)
    return cid


def _private() -> X25519PrivateKey:
    raw = db.get_setting("secret:LINK_PRIVATE_KEY", "") or ""
    if raw:
        return X25519PrivateKey.from_private_bytes(b64d(decrypt(raw)))
    k = X25519PrivateKey.generate()
    db.set_setting("secret:LINK_PRIVATE_KEY", encrypt(b64e(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                                            serialization.NoEncryption()))))
    return k


def public_key() -> str:
    return b64e(_private().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def derive(priv: X25519PrivateKey, peer_pub: str, mailbox: str) -> bytes:
    shared = priv.exchange(X25519PublicKey.from_public_bytes(b64d(peer_pub)))
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=mailbox.encode(), info=INFO).derive(shared)


def seal(key: bytes, mailbox: str, direction: str, obj: dict) -> dict:
    nonce = os.urandom(12)
    ct = ChaCha20Poly1305(key).encrypt(nonce, json.dumps(obj).encode(), f"{mailbox}|{direction}".encode())
    return {"v": 1, "mb": mailbox, "n": b64e(nonce), "c": b64e(ct)}


def open_(key: bytes, mailbox: str, direction: str, env: dict) -> dict:
    if env.get("v") != 1 or env.get("mb") != mailbox:
        raise HTTPException(400, "Unknown envelope")
    try:
        pt = ChaCha20Poly1305(key).decrypt(b64d(env["n"]), b64d(env["c"]), f"{mailbox}|{direction}".encode())
    except Exception as e:  # noqa: BLE001 - tampered, wrong key or wrong direction: all the same to the caller
        raise HTTPException(400, "This message couldn't be opened. Pair the phone again.") from e
    return json.loads(pt)


# Pairing the keys -------------------------------------------------------------------------------------------------
def register(device: dict, phone_pub: str) -> dict:
    ensure()
    if len(b64d(phone_pub)) != 32:
        raise HTTPException(400, "Bad public key")
    mailbox = "mb_" + secrets.token_urlsafe(18)
    relay_token = secrets.token_urlsafe(24)
    db.run("INSERT OR REPLACE INTO link_peers(device_id, phone_pub, mailbox, relay_token_enc, created_at) VALUES (?, ?, ?, ?, ?)",
           device["id"], phone_pub, mailbox, encrypt(relay_token), db.now())
    return {"computer_id": computer_id(), "computer_name": db.get_setting("workspace_name", "") or "My computer",
            "computer_pub": public_key(), "mailbox": mailbox, "relay_url": relay_url(), "relay_token": relay_token,
            "lan": lan_urls()}


def peer_for_mailbox(mailbox: str) -> dict:
    ensure()
    p = db.one("SELECT * FROM link_peers WHERE mailbox = ?", mailbox)
    if not p:
        raise HTTPException(404, "This phone's link isn't set up. Pair it again.")
    return p


def _account_token() -> str:
    from .. import account
    return account._session()


def relay_url() -> str:
    """Explicit setting/env first; otherwise the account's cloud when signed in (the relay lives there)."""
    u = (db.get_setting("link_relay_url", "") or os.getenv("LINK_RELAY_URL", "")).strip().rstrip("/")
    if u:
        return u
    from .. import account
    return account.cloud_url() if account._session() else ""


def lan_urls() -> list[str]:
    try:
        from .. import mobile

        st = mobile.lan_status()
        return [f"http://{a}:{st['port']}" for a in st.get("addresses") or []] if st.get("running") else []
    except Exception:  # noqa: BLE001
        return []


# Sealed requests --------------------------------------------------------------------------------------------------
async def dispatch(env: dict) -> dict:
    """Open a sealed phone request, run it as a normal /api/mobile/* call, and seal the answer."""
    p = peer_for_mailbox(env.get("mb", ""))
    key = derive(_private(), p["phone_pub"], p["mailbox"])
    req = open_(key, p["mailbox"], "p2c", env)
    seq, ts = int(req.get("seq", 0)), float(req.get("ts", 0))
    if seq <= p["last_seq_in"] or abs(time.time() - ts) > MAX_SKEW:
        raise HTTPException(409, "Replayed or stale message refused")
    db.run("UPDATE link_peers SET last_seq_in = ? WHERE device_id = ?", seq, p["device_id"])
    path = str(req.get("path", ""))
    if not path.startswith(SEALED_PATHS) or ".." in path or path.startswith("/api/mobile/link/sealed"):
        return seal(key, p["mailbox"], "c2p", {"seq": seq, "status": 403, "body": {"detail": "Not allowed over the link"}})
    from ..main import app

    headers = {"authorization": f"Bearer {req.get('token', '')}", "x-workbench": "1"}
    if req.get("idempotency_key"):
        headers["idempotency-key"] = str(req["idempotency_key"])[:200]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://link") as c:
        r = await c.request(str(req.get("method", "GET")).upper(), path, json=req.get("body"), headers=headers)
    try:
        body = r.json()
    except ValueError:
        body = {"text": r.text[:200_000]}
    return seal(key, p["mailbox"], "c2p", {"seq": seq, "status": r.status_code, "body": body})


async def relay_once(client: httpx.AsyncClient, base: str) -> int:
    """Pick up sealed requests waiting at the relay for every paired phone and post the sealed answers back."""
    ensure()
    handled = 0
    for p in db.all("SELECT * FROM link_peers"):
        tok = decrypt(p["relay_token_enc"])
        h = {"Authorization": f"Bearer {tok}"}
        r = await client.get(f"{base}/relay/v1/{p['mailbox']}/to-computer", headers=h, params={"wait": 20})
        if r.status_code == 404:
            # The mailbox is created with the signed-in account (the relay checks the plan); then only its token is used.
            await client.post(f"{base}/relay/v1/mailboxes", json={"mailbox": p["mailbox"], "token": tok},
                              headers={"Authorization": f"Bearer {_account_token()}"})
            continue
        if r.status_code >= 400:
            continue
        for env in r.json().get("messages", []):
            try:
                out = await dispatch(env)
            except HTTPException as e:
                log.info("relay message refused: %s", e.detail)
                continue
            await client.post(f"{base}/relay/v1/{p['mailbox']}/to-phone", json=out, headers=h)
            handled += 1
    return handled


async def relay_loop() -> None:
    while True:
        base = relay_url()
        ensure()
        if not base or not db.one("SELECT 1 FROM link_peers LIMIT 1"):
            _relay.update(running=False)
            await asyncio.sleep(15)
            continue
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10)) as c:
                _relay.update(running=True, error="")
                while relay_url() == base:
                    await relay_once(c, base)
                    _relay["last"] = db.now()
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _relay.update(running=False, error=f"{e.__class__.__name__}")
            await asyncio.sleep(20)


def status() -> dict:
    ensure()
    return {"computer_id": computer_id(), "computer_pub": public_key(), "relay_url": relay_url(), "relay": dict(_relay),
            "linked_phones": (db.one("SELECT COUNT(*) AS n FROM link_peers") or {"n": 0})["n"], "lan": lan_urls(),
            "shared_folders": shared_folders()}


# Handoff ----------------------------------------------------------------------------------------------------------
def handoff(direction: str, conversation_id: int | None, teammate_id: int | None, draft: str = "", path: str = "") -> dict:
    ensure()
    if direction not in ("to_phone", "to_computer"):
        raise HTTPException(400, "Hand off to the phone or the computer")
    hid = db.run("INSERT INTO link_handoffs(direction, conversation_id, teammate_id, draft, path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 direction, conversation_id, teammate_id, draft[:20000], path[:300], db.now())
    if direction == "to_phone":
        from .. import push

        push.notify("Continue on your phone", draft[:120] or "Pick up where you left off", kind="handoff", id=hid,
                    conversation_id=conversation_id)
    return db.one("SELECT * FROM link_handoffs WHERE id = ?", hid)


def pending_handoffs(direction: str) -> list[dict]:
    ensure()
    return db.all("SELECT * FROM link_handoffs WHERE direction = ? AND status = 'waiting' AND created_at > datetime('now', '-1 day') "
                  "ORDER BY id DESC LIMIT 5", direction)


def pick_up(hid: int) -> dict:
    ensure()
    db.run("UPDATE link_handoffs SET status = 'picked_up' WHERE id = ?", hid)
    r = db.one("SELECT * FROM link_handoffs WHERE id = ?", hid)
    if not r:
        raise HTTPException(404, "Nothing to pick up")
    return r


# Your computer's files, read-only, from the phone -----------------------------------------------------------------
def shared_folders() -> list[str]:
    raw = db.get_setting("phone_shared_folders", "")
    if raw is None or raw == "":
        from .computer import root

        defaults = [str(root())]
        if config.DESKTOP:
            defaults.insert(0, str(Path.home() / "Documents"))
        return defaults
    return [x for x in json.loads(raw) if isinstance(x, str)]


def set_shared_folders(folders: list[str]) -> list[str]:
    clean = []
    for f in folders[:20]:
        p = Path(f).expanduser()
        if not p.is_absolute() or not p.is_dir():
            raise HTTPException(400, f"{f} isn't a folder on this computer")
        clean.append(str(p.resolve()))
    db.set_setting("phone_shared_folders", json.dumps(clean))
    return clean


def _inside(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    for root in shared_folders():
        r = Path(root).resolve()
        if p == r or r in p.parents:
            return p
    raise HTTPException(403, "That folder isn't shared with your phone. Choose shared folders in Settings, Phone.")


def browse(path: str = "") -> dict:
    if not path:
        return {"path": "", "folders": [{"name": Path(r).name or r, "path": r} for r in shared_folders() if Path(r).is_dir()], "files": []}
    p = _inside(path)
    if not p.is_dir():
        raise HTTPException(404, "Folder not found")
    folders, files = [], []
    for c in sorted(p.iterdir(), key=lambda x: x.name.lower())[:500]:
        if c.name.startswith("."):
            continue
        try:
            if c.is_symlink():
                continue
            if c.is_dir():
                folders.append({"name": c.name, "path": str(c)})
            elif c.is_file():
                st = c.stat()
                files.append({"name": c.name, "path": str(c), "size": st.st_size, "modified": int(st.st_mtime)})
        except OSError:
            continue
    return {"path": str(p), "folders": folders, "files": files}


def read_file(path: str, max_bytes: int = 8 * 1024 * 1024) -> tuple[Path, bytes]:
    p = _inside(path)
    if not p.is_file():
        raise HTTPException(404, "File not found")
    if p.stat().st_size > max_bytes:
        raise HTTPException(413, "That file is too big to send to the phone (8 MB max). Ask a teammate to summarise it instead.")
    return p, p.read_bytes()
