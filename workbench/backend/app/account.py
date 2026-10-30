"""AI Workspace account, plan and Pro feature gating (desktop side of ai-workspace-cloud).

* Free never needs an account: the app, your own keys and Ollama always work, signed in or not, online or not.
* Signing in links this install to an account on the cloud service. The cloud hands back a short-lived Ed25519
  signed entitlement token that is verified here, offline, with the cloud's public key. It stays valid for its
  lifetime plus a grace period (7 days by default), so a Pro user who goes offline keeps Pro for a week.
* Plans are only enforced once billing is live: until the cloud URL is set and that server reports billing is on,
  every feature stays unlocked ("free while in beta"), so nothing breaks for anyone testing a build today.
  Once a build has seen billing live it remembers, so blocking the server does not unlock Pro.
* Pro features are gated by `allowed(feature)` / `require_feature(feature)` and by a small path rule table
  (`GATES`) so features from other modules are gated without editing them. A gated call answers 402 with
  {"detail": {"error": "upgrade_required", "feature": ..., "message": ...}}; the app shows an upgrade card.
  Stop/cancel/approve routes and anything that deletes or exports your own data are never gated.
"""

import base64
import json
import os
import platform
import re
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db
from .security import decrypt, encrypt, require_user

# Set at release time (or with the env var) to the deployed ai-workspace-cloud URL, e.g. https://cloud.example.com
DEFAULT_CLOUD_URL = ""
# Public keys (base64 raw Ed25519) the build trusts. Ship the production key here; the env var adds more (rotation).
BUILTIN_PUBLIC_KEYS: list[str] = []

FREE_FEATURES = {"app", "byo_keys", "ollama", "basic_themes"}
PRO_FEATURES = ["hosted_models", "computer_use", "site_watchers", "learning", "phone_sync", "premium_themes"]
LABELS = {
    "hosted_models": "Cloud models with no setup",
    "computer_use": "Computer use (Rooks Desk)",
    "site_watchers": "Website watchers on a schedule",
    "learning": "Learns from one demonstration",
    "phone_sync": "Phone app sync",
    "premium_themes": "Premium themes",
    "team_shared_skills": "Shared team skills",
    "team_admin": "Team admin controls",
    "team_tasks": "Team lead: assign and track your team",  # feat/team-lead
}
PREMIUM_THEMES = {"cherry", "linen", "gallery"}  # the default, Paper, Sunshine, Neon, Dark, High contrast and Auto stay free
# integration/v0.2: Neon is free (the owner wants neon everywhere); only Cherry & Ink and the hidden themes are premium.
# integration/v0.2: every Pro id declared by the v0.2 features (features_v02.FEATURE_TIERS) is a Pro feature here too, so
# account.allowed() gates them (only once billing is live) and "Your plan" lists them.
from . import features_v02  # noqa: E402

V02_PRO_FEATURES = sorted(features_v02.pro_features())
PRO_FEATURES += [f for f in V02_PRO_FEATURES if f not in PRO_FEATURES]
for _f in V02_PRO_FEATURES:
    LABELS.setdefault(_f, features_v02.FEATURE_LABELS.get(_f, _f))
# Paid plans whose signed token predates the v0.2 ids still get them (the token's plan is signed too).
PAID_PLANS = {"pro", "team", "business"}
REFRESH_AFTER_S = 12 * 3600
CLOUD_CONNECTION_NAME = "AI Workspace Cloud"

# (method, path regex, feature). Only starting new Pro work is gated; stopping, approving, reading, deleting and
# exporting stay free so a lapsed plan never traps anyone's data or leaves a job running.
GATES: list[tuple[str, re.Pattern, str]] = [(m, re.compile(p), f) for m, p, f in [
    ("POST", r"^/api/desk/jobs/?$", "computer_use"),
    ("POST", r"^/api/sites/watches/?$", "site_watchers"),
    ("POST", r"^/api/sites/watches/[^/]+/run/?$", "site_watchers"),
    ("POST", r"^/api/learn/recordings/?$", "learning"),
    ("POST", r"^/api/learn/playbooks/[^/]+/run/?$", "learning"),
    ("POST", r"^/api/learn/suggestions/[^/]+/accept/?$", "learning"),
    ("POST", r"^/api/mobile/pair/start/?$", "phone_sync"),
]]

router = APIRouter(prefix="/api/account", dependencies=[Depends(require_user)])
_http: httpx.Client | None = None  # tests inject a client bound to the cloud app


# ---------- settings ----------

def cloud_url() -> str:
    return (os.getenv("AIW_CLOUD_URL") or db.get_setting("account_cloud_url") or DEFAULT_CLOUD_URL).rstrip("/")


def public_keys() -> list[str]:
    keys = [k.strip() for k in os.getenv("AIW_CLOUD_PUBLIC_KEY", "").split(",") if k.strip()] + BUILTIN_PUBLIC_KEYS
    pinned = db.get_setting("account_public_key")
    if not keys and pinned:  # dev builds with no baked key pin the first key they saw (trust on first use)
        keys.append(pinned)
    return keys


def device_id() -> str:
    d = db.get_setting("account_device_id")
    if not d:
        d = "dev-" + uuid.uuid4().hex
        db.set_setting("account_device_id", d)
    return d


def _session() -> str:
    return decrypt(db.get_setting("account_session") or "")


def _client() -> httpx.Client:
    return _http or httpx.Client(timeout=8)


def _cloud(method: str, path: str, body: dict | None = None, auth: bool = True) -> dict:
    base = cloud_url()
    if not base:
        raise HTTPException(503, "Accounts aren't switched on in this build yet. Everything is unlocked while in beta.")
    headers = {"Authorization": f"Bearer {_session()}"} if auth and _session() else {}
    try:
        r = _client().request(method, f"{base}{path}", json=body, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(503, "Can't reach your AI Workspace account right now. Check your internet and try again.") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code == 401 and auth:
        _forget_session()
    if r.status_code >= 400:
        d = data.get("detail") if isinstance(data, dict) else None
        msg = d if isinstance(d, str) else (d or {}).get("message") if isinstance(d, dict) else None
        raise HTTPException(r.status_code if r.status_code < 500 else 503, msg or "Your account service had a problem. Try again.")
    return data


# ---------- tokens ----------

def _b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(token: str, keys: list[str] | None = None, now: float | None = None) -> dict | None:
    """The payload of a valid entitlement token for this device, or None. Works offline."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        prefix, body, sig = token.split(".")
        if prefix != "aiw1":
            return None
        ok = False
        for k in keys if keys is not None else public_keys():
            try:
                Ed25519PublicKey.from_public_bytes(base64.b64decode(k)).verify(_b64u(sig), f"{prefix}.{body}".encode())
                ok = True
                break
            except Exception:  # noqa: BLE001 - wrong key or bad signature
                continue
        if not ok:
            return None
        payload = json.loads(_b64u(body))
        t = time.time() if now is None else now
        if t > payload["exp"] + payload.get("grace", 0) or payload.get("device") != device_id():
            return None
        return payload
    except Exception:  # noqa: BLE001 - malformed token: treat as no entitlement
        return None


def _payload() -> dict | None:
    tok = db.get_setting("account_token") or ""
    return verify(tok) if tok else None


def billing_live() -> bool:
    if os.getenv("AIW_ENFORCE_PLANS") in ("0", "1"):
        return os.getenv("AIW_ENFORCE_PLANS") == "1"
    return bool(cloud_url()) and db.get_setting("account_billing_live") == "1"


def features() -> set[str]:
    p = _payload()
    if not p:
        return set(FREE_FEATURES)
    feats = FREE_FEATURES | set(p["features"])
    if p.get("plan") in PAID_PLANS:  # integration/v0.2
        feats |= set(V02_PRO_FEATURES)
    if p.get("plan") in ("team", "business"):  # feat/team-lead: team lead features
        feats |= features_v02.team_features()
    return feats


def allowed(feature: str) -> bool:
    """Use this anywhere a Pro feature starts work (for example a watcher's scheduled run)."""
    return feature in FREE_FEATURES or not billing_live() or feature in features()


def upgrade_detail(feature: str) -> dict:
    return {"error": "upgrade_required", "feature": feature, "label": LABELS.get(feature, feature),
            "message": f"{LABELS.get(feature, 'This')} is part of Pro. Open Your plan to upgrade or sign in."}


def require_feature(feature: str):
    def dep() -> None:
        if not allowed(feature):
            raise HTTPException(402, upgrade_detail(feature))
    return dep


def gate_for(method: str, path: str) -> str | None:
    for m, rx, feature in GATES:
        if m == method and rx.match(path):
            return feature
    return None


def install(app: FastAPI) -> None:
    """Mount the routes and the path gate. One line in main.py."""
    app.include_router(router)

    @app.middleware("http")
    async def plan_gate(request: Request, call_next):
        feature = gate_for(request.method, request.url.path)
        if feature and not allowed(feature):
            return JSONResponse({"detail": upgrade_detail(feature)}, status_code=402)
        return await call_next(request)


# ---------- refresh and hosted models ----------

def _forget_session() -> None:
    for k in ("account_session", "account_token", "account_email", "account_login"):
        db.set_setting(k, "")
    _sync_cloud_connection(False)


def _check_billing(force: bool = False) -> None:
    """Has the owner switched billing on? Checked at most every 12 hours; once seen it stays on."""
    if db.get_setting("account_billing_live") == "1":
        return
    if not force and time.time() - float(db.get_setting("account_plans_checked_at") or 0) < REFRESH_AFTER_S:
        return
    db.set_setting("account_plans_checked_at", str(time.time()))
    try:
        if _cloud("GET", "/v1/plans", auth=False).get("billing_live"):
            db.set_setting("account_billing_live", "1")
    except HTTPException:
        pass


def refresh(force: bool = False) -> dict | None:
    """Fetch a new entitlement token. Network problems keep the current token (and its grace period)."""
    if not cloud_url():
        return None
    _check_billing(force)
    if not _session():
        return None
    last = float(db.get_setting("account_refreshed_at") or 0)
    if not force and time.time() - last < REFRESH_AFTER_S and _payload():
        return _payload()
    try:
        r = _cloud("POST", "/v1/entitlement", {"device_id": device_id(), "device_name": platform.node()[:200],
                                               "platform": platform.system().lower()})
    except HTTPException as e:
        if e.status_code in (401, 403, 409):
            if e.status_code == 409:  # device limit: this computer is no longer entitled
                db.set_setting("account_token", "")
            raise
        return _payload()
    if not public_keys() and r.get("public_key"):
        db.set_setting("account_public_key", r["public_key"])
    db.set_setting("account_token", r["token"])
    db.set_setting("account_refreshed_at", str(time.time()))
    db.set_setting("account_summary", json.dumps({k: r["entitlement"].get(k) for k in ("plan_name", "credits", "status", "renews_at", "team", "source")}))
    _sync_cloud_connection("hosted_models" in (_payload() or {}).get("features", []))
    return _payload()


def _sync_cloud_connection(enabled: bool) -> None:
    """The hosted models show up as a normal model connection, keyed by the account session."""
    from . import llm

    base = cloud_url()
    llm.managed_connection(CLOUD_CONNECTION_NAME, f"{base}/v1", _session(), enabled and bool(base), "auto,fast,smart")


# ---------- routes ----------

def status() -> dict:
    p = _payload()
    summary = json.loads(db.get_setting("account_summary") or "{}") if p else {}
    feats = features()
    live = billing_live()
    return {
        "cloud_configured": bool(cloud_url()), "billing_live": live, "signed_in": bool(_session()),
        "email": db.get_setting("account_email") or "", "plan": p["plan"] if p else "free",
        "plan_name": summary.get("plan_name") or ("Free" if not p else p["plan"].title()),
        "features": sorted(feats), "pro_features": [{"id": f, "label": LABELS[f], "unlocked": allowed(f)} for f in PRO_FEATURES],
        "credits": summary.get("credits"), "status": summary.get("status"), "renews_at": summary.get("renews_at"),
        "team": summary.get("team"), "offline_until": (p["exp"] + p.get("grace", 0)) if p else None,
        "premium_themes": sorted(PREMIUM_THEMES), "setup_prompt": bool(cloud_url()) and not _session() and db.get_setting("account_setup_seen") != "1",
    }


@router.get("")
def get_account():
    try:
        refresh()
    except HTTPException:
        pass
    return status()


@router.get("/providers")
def providers():
    if not cloud_url():
        return {"email": False, "google": False, "apple": False}
    try:
        return _cloud("GET", "/v1/auth/providers", auth=False)
    except HTTPException:
        return {"email": True, "google": False, "apple": False}


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


def _remember_login(r: dict, email: str = "") -> None:
    db.set_setting("account_login", encrypt(json.dumps({"login_id": r["login_id"], "poll_token": r["poll_token"], "email": email})))


def _login() -> dict:
    raw = decrypt(db.get_setting("account_login") or "")
    if not raw:
        raise HTTPException(400, "Start signing in first")
    return json.loads(raw)


def _signed_in(r: dict) -> dict:
    db.set_setting("account_session", encrypt(r["session_token"]))
    db.set_setting("account_email", r["user"]["email"])
    db.set_setting("account_login", "")
    db.set_setting("account_setup_seen", "1")
    refresh(force=True)
    return status()


@router.post("/sign-in/email")
def sign_in_email(body: EmailIn):
    r = _cloud("POST", "/v1/auth/email/start", {"email": body.email.strip(), "device_id": device_id(),
                                                "device_name": platform.node()[:200]}, auth=False)
    _remember_login(r, body.email.strip())
    return {"sent": True, "email": body.email.strip(), "expires_in": r.get("expires_in")}


class CodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)


@router.post("/sign-in/code")
def sign_in_code(body: CodeIn):
    lr = _login()
    return _signed_in(_cloud("POST", "/v1/auth/email/code", {"login_id": lr["login_id"], "code": body.code.strip()}, auth=False))


@router.post("/sign-in/poll")
def sign_in_poll():
    lr = _login()
    r = _cloud("POST", "/v1/auth/poll", {"login_id": lr["login_id"], "poll_token": lr["poll_token"]}, auth=False)
    if r.get("status") != "approved":
        return {"status": r.get("status", "pending")}
    return {"status": "approved", "account": _signed_in(r)}


class OAuthIn(BaseModel):
    provider: str


@router.post("/sign-in/oauth")
def sign_in_oauth(body: OAuthIn):
    r = _cloud("POST", "/v1/auth/oauth/start", {"provider": body.provider, "device_id": device_id(),
                                                "device_name": platform.node()[:200]}, auth=False)
    _remember_login(r)
    return {"url": r["url"]}


@router.post("/sign-out")
def sign_out():
    if _session() and cloud_url():
        try:
            _cloud("POST", "/v1/auth/logout")
        except HTTPException:
            pass
    _forget_session()
    return status()


@router.post("/refresh")
def force_refresh():
    refresh(force=True)
    return status()


@router.post("/skip")
def skip_setup():
    db.set_setting("account_setup_seen", "1")
    return {"ok": True}


class CheckoutIn(BaseModel):
    product: str
    quantity: int = 1
    team_id: str = ""


@router.post("/checkout")
def checkout(body: CheckoutIn):
    if not _session():
        raise HTTPException(400, "Sign in first, then upgrade.")
    return _cloud("POST", "/v1/billing/checkout", body.model_dump())


@router.post("/portal")
def portal():
    if not _session():
        raise HTTPException(400, "Sign in first.")
    return _cloud("POST", "/v1/billing/portal", {})


class InviteIn(BaseModel):
    invite_code: str = Field(min_length=8, max_length=200)


@router.post("/invite")
def accept_invite(body: InviteIn):
    if not _session():
        raise HTTPException(400, "Sign in with the invited email first.")
    _cloud("POST", "/v1/teams/invites/accept", {"invite_code": body.invite_code.strip()})
    refresh(force=True)
    return status()


class TeamCheckoutIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    seats: int = Field(default=3, ge=1, le=500)
    product: str = "team_monthly"


@router.post("/team-checkout")
def team_checkout(body: TeamCheckoutIn):
    """Create a team you own on the cloud, then open checkout for its seats."""
    if not _session():
        raise HTTPException(400, "Sign in first, then set up a team.")
    if body.product not in ("team_monthly", "business_monthly"):
        raise HTTPException(400, "Pick Team or Business")
    team = _cloud("POST", "/v1/teams", {"name": body.name.strip()})
    return _cloud("POST", "/v1/billing/checkout", {"product": body.product, "quantity": body.seats, "team_id": team["id"]})
