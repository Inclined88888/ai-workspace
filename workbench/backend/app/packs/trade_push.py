"""Optional push of a quote or invoice to ServiceM8 or Xero, with the user's own credentials (feat/v02-packs).

Two steps, every time: preview() shows exactly what would be sent and returns a one-time approval code valid for ten
minutes; push() only runs with that code. Without credentials both say how to connect and nothing leaves the app.
ServiceM8: API key from Settings -> API Keys, sent as X-API-Key (developer.servicem8.com/docs/authentication).
Xero: a Custom Connection's client id and secret, exchanged for a 30-minute token with the client_credentials grant
(developer.xero.com/faq/custom-integration); the token is bound to one organisation, so no tenant id is needed.
"""

from __future__ import annotations

import json
import secrets
import time

import httpx
from fastapi import HTTPException

from .. import db
from ..security import decrypt, encrypt
from . import registry, trade

TARGETS = {
    "servicem8": {"name": "ServiceM8", "fields": ["api_key"], "base": "https://api.servicem8.com/api_1.0",
                  "how": "In ServiceM8 open Settings, then API Keys, create a key with the create_jobs permission and paste it here."},
    "xero": {"name": "Xero", "fields": ["client_id", "client_secret"], "base": "https://api.xero.com/api.xro/2.0",
             "token_url": "https://identity.xero.com/connect/token",
             "how": "In the Xero developer portal create a Custom Connection with the accounting.invoices and "
                    "accounting.contacts scopes, authorise it for your organisation, then paste its client id and secret here."},
}
_pending: dict[str, dict] = {}
TTL = 600


def _key(target: str) -> str:
    return f"packs_trade_{target}_enc"


def _target(target: str) -> dict:
    if target not in TARGETS:
        raise HTTPException(404, "Push to ServiceM8 or Xero")
    return TARGETS[target]


def creds(target: str) -> dict:
    _target(target)
    enc = db.get_setting(_key(target))
    if not enc:
        return {}
    try:
        return json.loads(decrypt(enc))
    except Exception:  # noqa: BLE001
        return {}


def save_creds(target: str, data: dict) -> dict:
    t = _target(target)
    vals = {f: str(data.get(f) or "").strip() for f in t["fields"]}
    if not any(vals.values()):
        db.run("DELETE FROM settings WHERE key = ?", _key(target))
    elif not all(vals.values()):
        raise HTTPException(400, f"{t['name']} needs: {', '.join(t['fields'])}")
    else:
        db.set_setting(_key(target), encrypt(json.dumps(vals)))
    return status()


def status() -> dict:
    return {k: {"name": v["name"], "connected": bool(creds(k)), "fields": v["fields"], "how": v["how"]} for k, v in TARGETS.items()}


def payload(target: str, doc: dict) -> dict:
    s = trade.settings()
    c = doc["customer"]
    if target == "servicem8":
        lines = "\n".join(f"- {x['description']}: {x['qty']:g} {x['unit']} = {trade.fmt(x['amount'], s)}" for x in doc["totals"]["lines"])
        return {"method": "POST", "url": f"{TARGETS['servicem8']['base']}/job.json",
                "json": {"status": "Quote" if doc["kind"] == "quote" else "Work Order", "job_address": c.get("address", ""),
                         "job_description": f"{doc['title']}\n{doc['number']} for {c.get('name') or 'customer'}\n{lines}\n"
                                            f"Total {trade.fmt(doc['totals']['total'], s)}"[:4000]}}
    line_type = "Inclusive" if doc["totals"]["prices_include_tax"] else ("Exclusive" if s.get("tax_registered") else "NoTax")
    return {"method": "POST", "url": f"{TARGETS['xero']['base']}/{'Quotes' if doc['kind'] == 'quote' else 'Invoices'}",
            "json": {("Quotes" if doc["kind"] == "quote" else "Invoices"): [{
                **({"Type": "ACCREC", "InvoiceNumber": doc["number"], "DueDate": doc["due_on"]} if doc["kind"] == "invoice"
                   else {"QuoteNumber": doc["number"], "ExpiryDate": doc["due_on"]}),
                "Contact": {"Name": c.get("name") or "Customer", **({"EmailAddress": c["email"]} if c.get("email") else {})},
                "Date": doc["issued_on"], "LineAmountTypes": line_type, "Status": "DRAFT",
                "LineItems": [{"Description": x["description"], "Quantity": x["qty"], "UnitAmount": x["unit_price"]} for x in doc["totals"]["lines"]],
            }]}}


def preview(target: str, did: int) -> dict:
    registry.require("trade")
    t = _target(target)
    doc = trade.get(did)
    body = payload(target, doc)
    if not creds(target):
        return {"target": t["name"], "connected": False, "how": t["how"], "would_send": body,
                "note": f"Not connected to {t['name']}, so nothing can be sent. Connect it below if you want this."}
    code = secrets.token_urlsafe(12)
    _pending[code] = {"target": target, "doc": did, "body": body, "expires": time.time() + TTL}
    return {"target": t["name"], "connected": True, "would_send": body, "approval_code": code,
            "note": f"Check what will be sent to {t['name']}. Press Send to {t['name']} to approve it. It is created as a draft there."}


async def push(target: str, did: int, approval_code: str) -> dict:
    registry.require("trade")
    t = _target(target)
    p = _pending.pop(approval_code or "", None)
    if not p or p["target"] != target or p["doc"] != did or p["expires"] < time.time():
        raise HTTPException(409, "Approve the push first: open the preview and press Send. Approvals last ten minutes and work once.")
    c = creds(target)
    if not c:
        raise HTTPException(400, f"Connect {t['name']} first.")
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Accept": "application/json"}
        if target == "servicem8":
            headers["X-API-Key"] = c["api_key"]
        else:
            # No scope asked for: the token gets what the connection was authorised with, so this works for both
            # granular (accounting.invoices, connections from 2026) and older broad (accounting.transactions) scopes.
            tok = await client.post(t["token_url"], data={"grant_type": "client_credentials"}, auth=(c["client_id"], c["client_secret"]))
            if tok.status_code != 200:
                raise HTTPException(502, f"Xero did not accept the client id and secret ({tok.status_code}).")
            headers["Authorization"] = f"Bearer {tok.json()['access_token']}"
        r = await client.request(p["body"]["method"], p["body"]["url"], json=p["body"]["json"], headers=headers)
    if r.status_code >= 300:
        raise HTTPException(502, f"{t['name']} said no ({r.status_code}): {r.text[:300]}")
    ref = r.headers.get("x-record-uuid", "")
    if target == "xero":
        try:
            j = r.json()
            item = (j.get("Invoices") or j.get("Quotes") or [{}])[0]
            ref = item.get("InvoiceID") or item.get("QuoteID") or ""
        except ValueError:
            pass
    doc = trade.get(did)
    pushed = {**doc["pushed"], target: {"at": db.now(), "ref": ref}}
    db.run("UPDATE trade_docs SET pushed = ?, updated_at = ? WHERE id = ?", json.dumps(pushed), db.now(), did)
    return {"ok": True, "target": t["name"], "ref": ref, "doc": trade.get(did)}
