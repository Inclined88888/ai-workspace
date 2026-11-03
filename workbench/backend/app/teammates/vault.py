"""Vault: logins, cards, addresses and secure notes the assistants use without ever seeing the secret (feat/grok-style).

* Secret fields (password, card number, CVC, one-time codes, secure notes) go to the OS keychain when the desktop app runs
  with WORKBENCH_SECRET_BACKEND=keychain; otherwise they are sealed with the workspace key (Fernet under APP_SECRET).
* Listing and the assistant's `vault_list` tool return labels and masked hints only (•••• 4242, j•••@gmail.com).
* An assistant that needs a detail it doesn't have calls `request_secure_form`: the chat shows a secure form card, the
  person types into it, and the values go straight here. The model gets back "saved as vault item #N", never the value.
* `reveal` exists for the person (Vault page, after a fresh sign-in check on the desktop) and `fill_values` for Rooks
  Browser to type into a page; neither is a tool the model can call.
"""

from __future__ import annotations

import json
import re
import secrets

from fastapi import HTTPException

from .. import db, keychain
from ..security import decrypt, encrypt

SCHEMA = """
CREATE TABLE IF NOT EXISTS vault_items (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                 -- login | card | address | note
  label TEXT NOT NULL,
  public TEXT NOT NULL DEFAULT '{}',  -- non-secret fields (site, username, name on card, expiry month, address lines)
  secret_ref TEXT NOT NULL DEFAULT '',-- keychain:<name> or Fernet token of the JSON of secret fields
  teammates TEXT NOT NULL DEFAULT '[]', -- which teammates may use it; [] = all
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS vault_forms (
  id INTEGER PRIMARY KEY,
  token TEXT NOT NULL,
  conversation_id INTEGER,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  fields TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'open',  -- open | filled | cancelled
  item_id INTEGER,
  created_at TEXT NOT NULL
);
"""

KINDS: dict[str, dict] = {
    "login": {"label": "Login", "public": ["site", "username"], "secret": ["password", "totp_secret"]},
    "card": {"label": "Card", "public": ["name_on_card", "expiry", "billing_postcode"], "secret": ["number", "cvc"]},
    "address": {"label": "Address", "public": ["full_name", "line1", "line2", "suburb", "state", "postcode", "country", "phone", "email"],
                "secret": []},
    "note": {"label": "Secure note", "public": [], "secret": ["text"]},
}
FIELD_LABELS = {"site": "Website", "username": "Username or email", "password": "Password", "totp_secret": "2FA secret (optional)",
                "name_on_card": "Name on card", "expiry": "Expiry (MM/YY)", "billing_postcode": "Billing postcode",
                "number": "Card number", "cvc": "Security code", "full_name": "Full name", "line1": "Address line 1",
                "line2": "Address line 2", "suburb": "Suburb or city", "state": "State", "postcode": "Postcode",
                "country": "Country", "phone": "Phone", "email": "Email", "text": "Note"}
SECRET_FIELDS = {f for k in KINDS.values() for f in k["secret"]}

_ready: set[str] = set()


def ensure() -> None:
    from .. import config

    key = str(config.DB_PATH)
    if key not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(key)


def backend() -> str:
    return "os-keychain" if keychain.enabled() else "encrypted-db"


def _seal(item_key: str, values: dict) -> str:
    blob = json.dumps(values)
    if keychain.enabled():
        return keychain.put(f"vault:{item_key}", blob)
    return encrypt(blob)


def _unseal(ref: str) -> dict:
    if not ref:
        return {}
    blob = keychain.get(ref) if keychain.is_ref(ref) else decrypt(ref)
    try:
        return json.loads(blob or "{}")
    except ValueError:
        return {}


def mask(field: str, value: str) -> str:
    v = str(value or "")
    if not v:
        return ""
    if field == "number":
        digits = re.sub(r"\D", "", v)
        return f"•••• {digits[-4:]}" if len(digits) >= 4 else "••••"
    if field in ("email", "username") and "@" in v:
        name, _, dom = v.partition("@")
        return f"{name[:1]}•••@{dom}"
    return "••••••"


def _row_out(r: dict) -> dict:
    pub = json.loads(r.get("public") or "{}")
    kind = KINDS.get(r["kind"], {"secret": []})
    sec = _unseal(r["secret_ref"])
    hints = {f: mask(f, sec.get(f, "")) for f in kind["secret"] if sec.get(f)}
    return {"id": r["id"], "kind": r["kind"], "label": r["label"], "fields": pub, "secret_hints": hints,
            "teammates": json.loads(r.get("teammates") or "[]"), "updated_at": r["updated_at"], "last_used_at": r.get("last_used_at")}


def listing() -> dict:
    ensure()
    return {"items": [_row_out(r) for r in db.all("SELECT * FROM vault_items ORDER BY kind, label")], "backend": backend(),
            "kinds": {k: {"label": v["label"], "fields": [{"id": f, "label": FIELD_LABELS[f], "secret": f in v["secret"]}
                                                        for f in v["public"] + v["secret"]]} for k, v in KINDS.items()}}


def _split(kind: str, values: dict) -> tuple[dict, dict]:
    if kind not in KINDS:
        raise HTTPException(400, "Pick login, card, address or secure note")
    spec = KINDS[kind]
    pub = {f: str(values.get(f, "")).strip()[:300] for f in spec["public"] if str(values.get(f, "")).strip()}
    sec = {f: str(values.get(f, "")).strip()[:2000] for f in spec["secret"] if str(values.get(f, "")).strip()}
    if kind == "card" and sec.get("number") and not _luhn(sec["number"]):
        raise HTTPException(400, "That card number doesn't look right. Check the digits.")
    return pub, sec


def _luhn(number: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", number)]
    if not 12 <= len(d) <= 19:
        return False
    s = sum(d[-1::-2]) + sum(sum(divmod(2 * x, 10)) for x in d[-2::-2])
    return s % 10 == 0


def create(kind: str, label: str, values: dict, teammates: list[int] | None = None) -> dict:
    ensure()
    label = (label or "").strip()[:80] or KINDS.get(kind, {}).get("label", "Item")
    pub, sec = _split(kind, values)
    key = secrets.token_hex(8)
    ref = _seal(key, sec) if sec else ""
    vid = db.run("INSERT INTO vault_items(kind, label, public, secret_ref, teammates, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 kind, label, json.dumps(pub), ref, json.dumps([int(t) for t in teammates or []]), db.now(), db.now())
    return _row_out(db.one("SELECT * FROM vault_items WHERE id = ?", vid))


def update(vid: int, label: str | None, values: dict | None, teammates: list[int] | None) -> dict:
    ensure()
    r = db.one("SELECT * FROM vault_items WHERE id = ?", vid)
    if not r:
        raise HTTPException(404, "Vault item not found")
    if label is not None:
        db.run("UPDATE vault_items SET label = ? WHERE id = ?", label.strip()[:80] or r["label"], vid)
    if values is not None:
        old_pub, old_sec = json.loads(r["public"] or "{}"), _unseal(r["secret_ref"])
        pub, sec = _split(r["kind"], {**old_pub, **old_sec, **{k: v for k, v in values.items() if v not in (None,)}})
        if keychain.is_ref(r["secret_ref"]):
            keychain.delete(r["secret_ref"])
        ref = _seal(secrets.token_hex(8), sec) if sec else ""
        db.run("UPDATE vault_items SET public = ?, secret_ref = ? WHERE id = ?", json.dumps(pub), ref, vid)
    if teammates is not None:
        db.run("UPDATE vault_items SET teammates = ? WHERE id = ?", json.dumps([int(t) for t in teammates]), vid)
    db.run("UPDATE vault_items SET updated_at = ? WHERE id = ?", db.now(), vid)
    return _row_out(db.one("SELECT * FROM vault_items WHERE id = ?", vid))


def delete(vid: int) -> dict:
    ensure()
    r = db.one("SELECT secret_ref FROM vault_items WHERE id = ?", vid)
    if r and keychain.is_ref(r["secret_ref"]):
        keychain.delete(r["secret_ref"])
    db.run("DELETE FROM vault_items WHERE id = ?", vid)
    return {"deleted": vid}


def reveal(vid: int) -> dict:
    """For the person only (Vault page). Never exposed as a tool."""
    ensure()
    r = db.one("SELECT * FROM vault_items WHERE id = ?", vid)
    if not r:
        raise HTTPException(404, "Vault item not found")
    return {"id": vid, "values": {**json.loads(r["public"] or "{}"), **_unseal(r["secret_ref"])}}


def fill_values(vid: int, teammate_id: int | None) -> dict:
    """For Rooks Browser filling a page the person approved: the full values, checked against the teammate allowlist."""
    ensure()
    r = db.one("SELECT * FROM vault_items WHERE id = ?", vid)
    if not r:
        raise HTTPException(404, "Vault item not found")
    allowed = json.loads(r["teammates"] or "[]")
    if allowed and teammate_id not in allowed:
        raise HTTPException(403, "This teammate isn't allowed to use that vault item")
    db.run("UPDATE vault_items SET last_used_at = ? WHERE id = ?", db.now(), vid)
    return {**json.loads(r["public"] or "{}"), **_unseal(r["secret_ref"])}


def for_model(teammate_id: int | None) -> list[dict]:
    """What an assistant may know: labels, kinds, the site, and masked hints. No secret, ever."""
    ensure()
    out = []
    for r in db.all("SELECT * FROM vault_items ORDER BY kind, label"):
        allowed = json.loads(r["teammates"] or "[]")
        if allowed and teammate_id not in allowed:
            continue
        o = _row_out(r)
        pub = o["fields"]
        out.append({"id": o["id"], "kind": o["kind"], "label": o["label"], "site": pub.get("site", ""),
                    "username": mask("username", pub.get("username", "")) if pub.get("username") else "",
                    "hints": o["secret_hints"], "suburb": pub.get("suburb", "")})
    return out


# Secure in-chat forms ---------------------------------------------------------------------------------------------
def open_form(kind: str, label: str, reason: str, conversation_id: int | None, fields: list[str] | None = None) -> dict:
    ensure()
    if kind not in KINDS:
        raise HTTPException(400, "Unknown kind of detail")
    spec = KINDS[kind]
    allowed = spec["public"] + spec["secret"]
    chosen = [f for f in (fields or allowed) if f in allowed] or allowed
    token = secrets.token_urlsafe(16)
    fid = db.run("INSERT INTO vault_forms(token, conversation_id, kind, label, reason, fields, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 token, conversation_id, kind, label.strip()[:80] or spec["label"], reason.strip()[:300], json.dumps(chosen), db.now())
    return form(fid)


def form(fid: int) -> dict:
    ensure()
    r = db.one("SELECT * FROM vault_forms WHERE id = ?", fid)
    if not r:
        raise HTTPException(404, "Form not found")
    fields = json.loads(r["fields"])
    return {"id": r["id"], "kind": r["kind"], "label": r["label"], "reason": r["reason"], "status": r["status"], "item_id": r["item_id"],
            "conversation_id": r["conversation_id"],
            "fields": [{"id": f, "label": FIELD_LABELS.get(f, f), "secret": f in SECRET_FIELDS} for f in fields]}


def forms(conversation_id: int | None = None, status: str = "open") -> list[dict]:
    ensure()
    rows = db.all("SELECT id FROM vault_forms WHERE status = ? AND (? IS NULL OR conversation_id = ?) ORDER BY id DESC LIMIT 20",
                  status, conversation_id, conversation_id)
    return [form(r["id"]) for r in rows]


def fill_form(fid: int, values: dict, teammates: list[int] | None = None) -> dict:
    f = form(fid)
    if f["status"] != "open":
        raise HTTPException(409, "That form was already answered")
    item = create(f["kind"], f["label"], values, teammates)
    db.run("UPDATE vault_forms SET status = 'filled', item_id = ? WHERE id = ?", item["id"], fid)
    return {"form": form(fid), "item": item}


def cancel_form(fid: int) -> dict:
    form(fid)
    db.run("UPDATE vault_forms SET status = 'cancelled' WHERE id = ? AND status = 'open'", fid)
    return form(fid)
