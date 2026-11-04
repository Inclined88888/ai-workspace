from __future__ import annotations
import asyncio
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import calendar_sync, catalog, config, db, llm, mail, research, services
from .adapters import ALLOWED, validate
from .db import OFFICIAL_HOSTS
from .redact import redact, redact_obj
from .security import (
    COOKIE, check_session, decrypt, encrypt, forget_connection_secret, hash_password, make_session, require_user,
    store_connection_secret, throttle, verify_password,
)

public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
SETUP_CODE = os.getenv("SETUP_CODE", "")
_tasks: set[asyncio.Task] = set()


def sse(gen):
    async def body():
        try:
            async for item in gen:
                if isinstance(item, dict) and "error" in item:  # R7: error text can quote provider keys
                    item = {**item, "error": redact(str(item["error"]))}
                yield f"data: {json.dumps(item)}\n\n"
        except HTTPException as e:
            yield f"data: {json.dumps({'error': redact_obj(e.detail)})}\n\n"
        except Exception as e:  # noqa: BLE001 - report to the client instead of dropping the stream
            yield f"data: {json.dumps({'error': redact(str(e) or e.__class__.__name__)})}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


from .security import set_session_cookie

def _login(response: Response) -> None:
    set_session_cookie(response, make_session())


# Status, setup, auth
@public.get("/status")
def status(request: Request):
    signed_in = check_session(request.cookies.get(COOKIE))
    return {
        "setup_complete": db.get_setting("setup_complete") == "1",
        "signed_in": signed_in,
        # Docker installs get the same first-run "Choose how to think" screen while no model is connected
        "needs_models": bool(signed_in) and not db.one("SELECT id FROM connections WHERE enabled = 1 LIMIT 1"),
        "setup_code_required": bool(SETUP_CODE),
        "workspace_name": db.get_setting("workspace_name", config.WORKSPACE_NAME),
        "chat_url": config.CHAT_URL,
        "paperclip_url": config.PAPERCLIP_URL,
        "hermes_url": config.HERMES_URL,
        "browser_enabled": bool(config.BROWSER_URL and config.BROWSER_API_KEY),
        "desk_enabled": bool(config.DESK_URL and config.DESK_API_KEY),
        "local_models_enabled": bool(config.OLLAMA_URL),
        "desktop": config.DESKTOP,
        "welcome_done": db.get_setting("desktop_welcome_done") == "1",
        "signins_offered": db.get_setting("signins_offered") == "1",
        "welcome_pending": db.get_setting("desktop_welcome_done") == "0",
        "trade": _trade(),
    }


@api.get("/connections/doctor")
def connections_doctor():
    """Plain-language health of models, browser, computer, and mail. Docker is never required for the desktop app."""
    enabled = db.all("SELECT id, name, auth_kind, protocol FROM connections WHERE enabled = 1")
    plans = [c for c in enabled if (c.get("auth_kind") or "") in ("oauth_subscription",) or (c.get("protocol") or "") == "cli_plan"]
    mail_n = (db.one("SELECT COUNT(*) AS n FROM mail_accounts") or {"n": 0})["n"]
    roles = {r["life_role"]: r["n"] for r in db.all("SELECT life_role, COUNT(*) AS n FROM mail_accounts GROUP BY life_role")}
    notes = [
        "The desktop app runs without Docker. Optional add-ons (search, a heavy browser image) are only if you turn them on.",
    ]
    if plans:
        notes.append("A Claude or ChatGPT plan in Chat is a limited chat mode. Full coding with that plan is on the Coding page.")
    if not enabled:
        notes.append("No model is connected yet. Open AI models and add a key, a plan, or a local model.")
    return {
        "models_connected": len(enabled),
        "plan_connections": len(plans),
        "browser_ready": bool(config.BROWSER_URL and config.BROWSER_API_KEY),
        "computer_ready": bool(config.DESK_URL and config.DESK_API_KEY),
        "mail_accounts": mail_n,
        "mail_roles": roles,
        "docker_required": False,
        "notes": notes,
    }


class SetupIn(BaseModel):
    password: str = Field(..., min_length=6, max_length=200)
    setup_code: str = ""
    workspace_name: str = Field("", max_length=80)
    timezone: str = Field("", max_length=64)  # the browser's zone, used when the server can't tell (Docker: UTC)



@public.post("/setup")
def setup(data: SetupIn, request: Request, response: Response):
    throttle(request.client.host if request.client else "?")
    if db.get_setting("setup_complete") == "1":
        raise HTTPException(409, "Setup already completed")
    if SETUP_CODE and not secrets.compare_digest(data.setup_code.strip(), SETUP_CODE):
        raise HTTPException(403, "Setup code is wrong. It is printed by setup.sh and stored as SETUP_CODE in .env")
    db.set_setting("password_hash", hash_password(data.password))
    if db.get_setting("desktop_welcome_done") != "1":
        db.set_setting("desktop_welcome_done", "0")  # a brand new workspace: everyone gets the first-run welcome
    if data.workspace_name.strip():
        db.set_setting("workspace_name", data.workspace_name.strip())
    if data.timezone and config.TIMEZONE == "UTC" and not db.get_setting("timezone", ""):
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(data.timezone)
            db.set_setting("timezone", data.timezone)
        except Exception:  # noqa: BLE001 - a zone this server doesn't know: keep the default
            pass
    db.set_setting("setup_complete", "1")
    services.local_calendar_id()
    _login(response)
    return {"ok": True}


# Desktop app: the native shell owns the machine and the window, so there is no password. On each launch it starts
# this backend with a fresh random WORKBENCH_DESKTOP_TOKEN and opens /api/desktop/session?token=... in its own window.
@public.get("/desktop/session")
def desktop_session(token: str, request: Request):
    from fastapi.responses import RedirectResponse

    if not config.DESKTOP or not config.DESKTOP_TOKEN or not secrets.compare_digest(token, config.DESKTOP_TOKEN):
        throttle(request.client.host if request.client else "?")
        raise HTTPException(403, "Desktop session is only available inside the desktop app")
    if db.get_setting("setup_complete") != "1":
        db.set_setting("password_hash", hash_password(secrets.token_urlsafe(32)))  # never typed: the window is the login
        db.set_setting("setup_complete", "1")
        services.local_calendar_id()
    target = "/" if db.get_setting("desktop_welcome_done") == "1" else "/welcome"
    response = RedirectResponse(target, status_code=303)
    _login(response)
    return response


# The launcher (`ai-workspace`) opens the app in a Chrome/Edge window. Putting the launch token on that browser's command
# line would show it to other local users (ps), so the launcher trades it for a one-time code that expires in two
# minutes, and opens /api/desktop/handoff?code=... instead.
HANDOFF_TTL = 120
_handoffs: dict[str, float] = {}


@public.post("/desktop/handoff")
def desktop_handoff_code(request: Request):
    import time

    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not config.DESKTOP or not config.DESKTOP_TOKEN or not secrets.compare_digest(token, config.DESKTOP_TOKEN):
        throttle(request.client.host if request.client else "?")
        raise HTTPException(403, "Only the AI Workspace launcher can ask for a sign-in code")
    now = time.monotonic()
    for c, exp in list(_handoffs.items()):
        if exp < now:
            _handoffs.pop(c, None)
    code = secrets.token_urlsafe(24)
    _handoffs[code] = now + HANDOFF_TTL
    return {"code": code, "expires_in": HANDOFF_TTL}


@public.get("/desktop/handoff")
def desktop_handoff(code: str, request: Request):
    import time

    from fastapi.responses import HTMLResponse

    exp = _handoffs.pop(code, None)
    if exp is None or exp < time.monotonic():
        throttle(request.client.host if request.client else "?")
        return HTMLResponse(
            '<!doctype html><meta charset="utf-8"><title>AI Workspace</title>'
            '<body style="font:16px -apple-system,system-ui,sans-serif;max-width:34em;margin:4em auto;color:#222">'
            "<h2>This sign-in link has already been used or has expired</h2>"
            "<p>Close this window and open AI Workspace again (run <code>ai-workspace</code> in a terminal, "
            "or click AI Workspace in Applications / the Start menu).</p></body>", status_code=403)
    return desktop_session(config.DESKTOP_TOKEN, request)


class LoginIn(BaseModel):
    password: str


@public.post("/login")
def login(data: LoginIn, request: Request, response: Response):
    throttle(request.client.host if request.client else "?")
    stored = db.get_setting("password_hash")
    if not stored or not verify_password(data.password, stored):
        raise HTTPException(401, "That PIN or password isn't the one you created.")
    _login(response)
    return {"ok": True}


@public.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@public.post("/lock/reset")
def reset_lock(request: Request):
    """A forgotten PIN cannot be read back. On this computer, drop the lock so a new one can be set. Work stays."""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        throttle(host or "?")
        raise HTTPException(403, "A new PIN can only be set on this computer.")
    throttle(host)
    db.set_setting("password_hash", "")
    db.set_setting("setup_complete", "0")
    db.set_setting("session_generation", secrets.token_hex(8))
    return {"ok": True}


class PasswordIn(BaseModel):
    current: str
    new: str = Field(..., min_length=6, max_length=200)


@api.post("/password")
def change_password(data: PasswordIn, response: Response):
    if not verify_password(data.current, db.get_setting("password_hash", "")):
        raise HTTPException(401, "Current password is wrong")
    db.set_setting("password_hash", hash_password(data.new))
    db.set_setting("session_generation", secrets.token_hex(8))
    _login(response)
    return {"ok": True}


@api.post("/desktop/welcome-done")
def desktop_welcome_done():
    db.set_setting("desktop_welcome_done", "1")
    db.set_setting("signins_offered", "1")
    return {"ok": True}


@api.post("/desktop/signins-done")
def signins_done():
    """The post-PIN sign-in screen (Gmail, Apple, Microsoft, ChatGPT, Claude, Grok) has been shown."""
    db.set_setting("signins_offered", "1")
    return {"ok": True}


@api.get("/desktop/info")
def desktop_info():
    from . import keychain

    return {"desktop": config.DESKTOP, "data_dir": str(config.DATA_DIR), "secret_backend": keychain.backend_name()}


# Settings and providers
@api.get("/settings")
def get_settings():
    from .assistant import MAX_STEPS, max_steps

    channels = {r["key"][len("max_steps_"):]: int(r["value"]) for r in db.all(
        "SELECT key, value FROM settings WHERE key LIKE 'max_steps\\_%' ESCAPE '\\'") if str(r["value"]).isdigit()}
    return {
        "workspace_name": db.get_setting("workspace_name", config.WORKSPACE_NAME),
        "default_model": db.get_setting("default_model", ""),
        "chat_url": config.CHAT_URL,
        "tools_url": "http://workbench:8000/tools/openapi.json",
        # model calls the assistant may make in one turn: the overall setting, per channel overrides, the default
        "max_steps": max_steps(""), "max_steps_channels": channels, "max_steps_default": MAX_STEPS,
        # daily download of model prices and limits from models.dev; off by default (L4: no unasked outbound call)
        "model_catalog_refresh": catalog.auto_refresh(),
        "model_catalog_env": os.getenv("MODEL_CATALOG_REFRESH", "") in ("0", "1"),
        "app_terminal": db.get_setting("app_terminal", "1") == "1",
    }


class SettingsIn(BaseModel):
    workspace_name: str | None = Field(None, max_length=80)
    default_model: str | None = Field(None, max_length=300)
    max_steps: int | None = Field(None, ge=0, le=100)  # 0 restores the default
    max_steps_channels: dict[str, int] | None = None   # e.g. {"schedule": 30}; a value of 0 removes the override
    model_catalog_refresh: bool | None = None
    app_terminal: bool | None = None


@api.put("/settings")
def put_settings(data: SettingsIn):
    if data.workspace_name is not None:
        db.set_setting("workspace_name", data.workspace_name.strip() or config.WORKSPACE_NAME)
    if data.default_model is not None:
        if data.default_model:
            llm.check_ref(data.default_model)
        db.set_setting("default_model", data.default_model)
    if data.max_steps is not None:
        db.set_setting("max_steps", str(data.max_steps) if data.max_steps else "")
    for channel, n in (data.max_steps_channels or {}).items():
        if not re.fullmatch(r"[a-z_]{1,20}", channel) or not 0 <= n <= 100:
            raise HTTPException(422, f"max_steps_channels: {channel!r} needs a lowercase channel name and 0 to 100")
        if n:
            db.set_setting(f"max_steps_{channel}", str(n))
        else:
            db.run("DELETE FROM settings WHERE key = ?", f"max_steps_{channel}")
    if data.app_terminal is not None:
        db.set_setting("app_terminal", "1" if data.app_terminal else "0")
    if data.model_catalog_refresh is not None:
        db.set_setting("model_catalog_refresh", "1" if data.model_catalog_refresh else "0")
        if data.model_catalog_refresh:
            catalog.refresh_in_background()  # at most once a day; the age check still applies
    return get_settings()


def _provider_out(c: dict) -> dict:
    key = decrypt(c["secret_enc"]) if c["auth_kind"] in ("api_key", "openai_compatible") else ""
    return {**{k: c[k] for k in ("id", "name", "auth_kind", "protocol", "base_url", "models", "enabled", "state", "residency", "last_error")},
            "has_key": bool(key), "key_hint": f"…{key[-4:]}" if len(key) > 8 else ""}


@api.get("/providers")
def providers():
    """Model connections (one per provider account or local server). See adapters.py for auth kinds and protocols."""
    return {"providers": [_provider_out(c) for c in db.all("SELECT * FROM connections ORDER BY id")], "presets": llm.PRESETS,
            "key_info": llm.KEY_INFO,
            "auth_kinds": {k: list(v) for k, v in ALLOWED.items() if k != "session_scraping_forbidden"}}


# Older clients send `kind`; it maps onto the auth_kind/protocol pair it always meant
_LEGACY_KIND = {"ollama": ("local_ollama", "ollama_native"), "anthropic": ("api_key", "chat_completions"), "openai": ("api_key", "chat_completions")}


class ProviderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    auth_kind: str | None = Field(None, max_length=40)
    protocol: str | None = Field(None, max_length=40)
    kind: Literal["openai", "anthropic", "ollama"] | None = None
    base_url: str = Field(..., pattern=r"^(https?://|cli://(claude|codex)/?$)", max_length=300)
    api_key: str | None = Field(None, max_length=500)
    models: str = Field("", max_length=2000)
    enabled: bool = True

    def pair(self) -> tuple[str, str]:
        auth, proto = self.auth_kind, self.protocol
        if not auth and not proto:
            auth, proto = _LEGACY_KIND[self.kind or "openai"]
            if auth == "api_key" and self.kind != "anthropic" and not any(h in self.base_url for h in OFFICIAL_HOSTS):
                auth = "openai_compatible"
        validate(auth or "", proto or "")
        if auth == "oauth_subscription":
            raise HTTPException(400, "Subscription connections are created by signing in with the provider (item 101), not by pasting a token.")
        if (auth == "cli_subscription") != self.base_url.startswith("cli://"):
            raise HTTPException(400, "A plan CLI connection uses cli://claude or cli://codex, and only it can")
        return auth, proto


@api.post("/providers")
def add_provider(data: ProviderIn):
    auth, proto = data.pair()
    cid = db.run(
        "INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, models, enabled, residency, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        data.name, auth, proto, data.base_url.rstrip("/"), "", data.models, int(data.enabled),
        "local" if auth == "local_ollama" else "cloud", db.now(),
    )
    store_connection_secret(cid, data.api_key or "")
    return _provider_out(db.one("SELECT * FROM connections WHERE id = ?", cid))


@api.put("/providers/{pid}")
def edit_provider(pid: int, data: ProviderIn):
    old = db.one("SELECT * FROM connections WHERE id = ?", pid)
    if not old:
        raise HTTPException(404, "Provider not found")
    auth, proto = (old["auth_kind"], old["protocol"]) if old["auth_kind"] == "oauth_subscription" else data.pair()
    db.run("UPDATE connections SET name = ?, auth_kind = ?, protocol = ?, base_url = ?, models = ?, enabled = ?, residency = ? WHERE id = ?",
           data.name, auth, proto, data.base_url.rstrip("/"), data.models, int(data.enabled),
           "local" if auth == "local_ollama" else "cloud", pid)
    if data.api_key is not None and auth != "oauth_subscription":
        store_connection_secret(pid, data.api_key)
    llm._model_cache.pop(pid, None)
    return _provider_out(db.one("SELECT * FROM connections WHERE id = ?", pid))


@api.delete("/providers/{pid}")
def delete_provider(pid: int):
    old = db.one("SELECT secret_enc FROM connections WHERE id = ?", pid)
    if old:
        forget_connection_secret(old["secret_enc"])
    db.run("DELETE FROM connections WHERE id = ?", pid)
    db.run("DELETE FROM settings WHERE key = ?", f"image_model:{pid}")  # ids are reused: never hand it to the next connection
    llm._model_cache.pop(pid, None)
    return {"ok": True}


class KeyIn(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=500)
    preset: str | None = Field(None, max_length=40)
    base_url: str | None = Field(None, pattern=r"^https?://", max_length=300)
    save: bool = False


def _key_connection(data: KeyIn) -> tuple[str, dict]:
    preset = data.preset or llm.detect_key(data.api_key)
    if not preset or preset not in llm.PRESETS or llm.PRESETS[preset]["auth_kind"] == "local_ollama":
        raise HTTPException(400, "That doesn't look like a key we know. Pick the provider, then paste it again.")
    p = llm.PRESETS[preset]
    return preset, {"id": 0, "name": p["name"], "auth_kind": p["auth_kind"], "protocol": p["protocol"],
                    "base_url": (data.base_url or p["base_url"]).rstrip("/"), "secret_enc": encrypt(data.api_key.strip()),
                    "models": "", "state": "connected", "enabled": 1}


@api.post("/providers/detect")
def detect_key(data: KeyIn):
    """Which provider a pasted key is for, from its shape. The key is not sent anywhere."""
    preset = llm.detect_key(data.api_key)
    return {"preset": preset, "name": llm.PRESETS[preset]["name"] if preset else None}


@api.post("/providers/validate")
def validate_key(data: KeyIn):
    """Checks a pasted key against the provider's model list before anything is saved; with save, connects it.

    Only the outcome is returned, never the provider's error text (some quote the key back)."""
    from .adapters import Adapter

    preset, conn = _key_connection(data)
    try:
        health, detail = Adapter(conn).probe(deep=True)
    except HTTPException as e:
        health, detail = "down", str(e.detail)
    out = {"preset": preset, "name": conn["name"], "ok": health != "down", "health": health, "detail": detail}
    if out["ok"] and data.save:
        same = db.one("SELECT id FROM connections WHERE base_url = ? AND auth_kind = ?", conn["base_url"], conn["auth_kind"])
        cid = same["id"] if same else db.run(
            "INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, residency, created_at) VALUES (?, ?, ?, ?, '', 'cloud', ?)",
            conn["name"], conn["auth_kind"], conn["protocol"], conn["base_url"], db.now())
        db.run("UPDATE connections SET enabled = 1, state = 'connected', last_error = '' WHERE id = ?", cid)
        store_connection_secret(cid, data.api_key.strip())
        llm._model_cache.pop(cid, None)
        out["provider"] = _provider_out(db.one("SELECT * FROM connections WHERE id = ?", cid))
    return out


@api.get("/router")
def router_status():
    """The smart router: routing order, the fast and strong model each provider would use, quota and cool-downs."""
    from . import router

    return router.status()


class RouterIn(BaseModel):
    order: list[int] | None = None
    disabled: list[int] | None = None
    models: dict[str, dict[str, str]] | None = None
    local_fallback: bool | None = None


@api.put("/router")
def router_save(data: RouterIn):
    from . import router

    router.save_settings(data.model_dump())
    return router.status()


@api.post("/router/reset/{cid}")
def router_reset(cid: int):
    """Try a provider again now, even though it said to wait."""
    from . import router

    router.reset(cid)
    return router.status()


class ChatGPTStartIn(BaseModel):
    account: str | None = Field(None, max_length=200)


@api.post("/auth/chatgpt/start")
async def chatgpt_start(data: ChatGPTStartIn | None = None):
    """Sign in with ChatGPT (OpenAI's official flow for open-source apps). Open the URL in the system browser."""
    from . import siwc

    return await siwc.start((data.account if data else None), listen=True)


@api.get("/auth/chatgpt/status/{state}")
def chatgpt_status(state: str):
    from . import siwc

    return siwc.status(state)


class CallbackIn(BaseModel):
    url: str = Field(..., min_length=10, max_length=8000)


@api.post("/auth/chatgpt/complete")
async def chatgpt_complete(data: CallbackIn):
    """Finish a sign-in from a pasted callback URL (when the browser can't reach this computer's 127.0.0.1)."""
    from . import siwc

    res = await siwc.complete(data.url)
    if res.get("connection_id"):
        llm._model_cache.pop(res["connection_id"], None)
    return res


@api.post("/auth/chatgpt/signout/{cid}")
def chatgpt_signout(cid: int):
    from . import siwc

    conn = db.one("SELECT * FROM connections WHERE id = ? AND auth_kind = 'oauth_subscription'", cid)
    if not conn:
        raise HTTPException(404, "No ChatGPT sign-in with that id")
    llm._model_cache.pop(cid, None)
    return siwc.sign_out(conn)


@api.get("/auth/subscriptions")
def subscriptions():
    """Which plan sign-ins exist, and an honest reason for each one that isn't offered."""
    return {"options": SUBSCRIPTIONS}


SUBSCRIPTIONS = [
    {"id": "chatgpt", "name": "ChatGPT", "plans": "Free, Go, Plus, Pro, Business", "available": True,
     "detail": "OpenAI lets open-source apps use your ChatGPT plan through its official sign-in. Usage counts against your "
               "plan's limits, shared with your other apps.",
     "source": "https://developers.openai.com/siwc/token-sharing-open-source"},
    {"id": "claude", "name": "Claude", "plans": "Pro, Max, Team, Enterprise", "available": True, "via": "claude_code",
     "detail": "Through Claude Code, Anthropic's own app: one click sets it up and opens Anthropic's sign-in. Chat and coding "
               "then run on your plan. Your password never passes through this app.",
     "source": "https://code.claude.com/docs/en/legal-and-compliance"},
    {"id": "copilot", "name": "GitHub Copilot", "plans": "Pro, Pro+, Business, Enterprise", "available": True, "via": "copilot_cli",
     "detail": "Through GitHub's own Copilot CLI: one click sets it up and opens GitHub's sign-in. Each answer uses your "
               "plan's monthly premium requests.",
     "source": "https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli"},
    {"id": "grok", "name": "Grok", "plans": "SuperGrok", "available": False,
     "detail": "Not available yet. xAI only offers SuperGrok sign-in to partner apps so far, with no public sign-up for "
               "other apps. Use an xAI API key for now.",
     "source": "https://x.ai/news/grok-hermes"},
    {"id": "gemini", "name": "Gemini", "plans": "Google AI Pro, Ultra", "available": False,
     "detail": "Not available. Google doesn't allow other apps to use Gemini CLI or Antigravity sign-ins. A free AI Studio "
               "key gives you Gemini anyway.",
     "source": "https://github.com/google-gemini/gemini-cli/discussions/20632"},
]


@api.get("/models")
def models(fresh: bool = False, all: bool = False):  # noqa: A002 - the query parameter's name
    try:
        default = llm.default_model_ref()
    except HTTPException:
        default = ""
    from .receipts import reliability

    stats = reliability()
    every = llm.list_models(fresh)
    shown = every if all else [m for m in every if m.get("chat", True) or m["ref"] == default]
    plans = {c["id"] for c in db.all("SELECT id FROM connections WHERE base_url LIKE 'cli://%'")}  # take a thinking level
    return {"models": [m | {"reliability": stats.get(m["ref"]), "effort": m.get("provider_id") in plans} for m in shown],
            "default": default, "hidden": len(every) - len(shown)}


@api.get("/ollama/status")
async def ollama_status():
    import httpx

    if not config.OLLAMA_URL:
        return {"online": False, "models": [], "configured": False}
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{config.OLLAMA_URL}/api/tags")
        return {"online": True, "models": [m["name"] for m in r.json().get("models", [])]}
    except (httpx.HTTPError, ValueError):
        return {"online": False, "models": []}


class PullIn(BaseModel):
    model: str = Field(..., pattern=r"^[\w.\-/:]+$", max_length=200)


@api.post("/ollama/pull")
async def ollama_pull(data: PullIn):
    import httpx

    if not config.OLLAMA_URL:
        raise HTTPException(400, "Local models are not enabled. Run setup with --with local-models, or set OLLAMA_URL.")

    async def gen():
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10)) as c:
            async with c.stream("POST", f"{config.OLLAMA_URL}/api/pull", json={"model": data.model, "stream": True}) as r:
                async for line in r.aiter_lines():
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
        for p in db.all("SELECT id FROM connections WHERE auth_kind = 'local_ollama'"):
            llm._model_cache.pop(p["id"], None)
        yield {"done": True}

    return sse(gen())


def _trade() -> str:
    from . import projects

    return projects.current_trade()


class TradeIn(BaseModel):
    trade: str = ""


@api.get("/trade")
def get_trade():
    return {"trade": _trade()}


@api.put("/trade")
def put_trade(data: TradeIn):
    from . import projects

    return {"trade": projects.set_trade(data.trade)}


# Dashboard
@api.get("/dashboard")
def dashboard():
    from . import projects

    try:
        customers = projects.today_lines()
    except Exception:  # noqa: BLE001 - home still loads if a project table is mid-migration
        customers = []
    return {
        **services.agenda(7),
        "recent_docs": services.list_docs(limit=6),
        "research": db.all("SELECT id, question, status, created_at FROM research ORDER BY id DESC LIMIT 5"),
        "customers": customers,
        "trade": _trade(),
    }


# Docs
@api.get("/docs")
def list_docs(kind: str | None = None, q: str = "", tag: str = ""):
    return services.list_docs(kind, q, tag)


@api.post("/docs")
def create_doc(data: services.DocIn):
    return services.create_doc(data)


@api.get("/docs/{did}")
def get_doc(did: int):
    return services.get_doc(did)


@api.patch("/docs/{did}")
def update_doc(did: int, data: services.DocPatch):
    return services.update_doc(did, data)


@api.delete("/docs/{did}")
def delete_doc(did: int):
    services.delete_doc(did)
    return {"ok": True}


ASSIST = {
    "improve": "Improve the clarity, flow and word choice of the text. Keep the meaning, facts and voice.",
    "shorten": "Make the text substantially shorter while keeping every important point.",
    "expand": "Expand the text with more useful detail and explanation. Do not invent facts, names or numbers.",
    "summarize": "Summarize the text as a few tight bullet points.",
    "fix": "Fix spelling, grammar and punctuation only. Change nothing else.",
    "continue": "Continue writing from where the text ends, matching its style. Output only the continuation.",
    "outline": "Turn the topic or notes into a clear Markdown outline for a document.",
    "custom": "Follow the user's instruction for the text.",
}


class AssistIn(BaseModel):
    action: Literal["improve", "shorten", "expand", "summarize", "fix", "continue", "outline", "custom"]
    text: str = Field(..., max_length=200_000)
    instruction: str = Field("", max_length=4000)
    model: str | None = None


@api.post("/ai/assist")
def assist(data: AssistIn):
    system = (
        "You are a precise writing assistant inside a document editor. " + ASSIST[data.action] +
        " Output only the resulting Markdown text with no preamble, no explanation and no code fences."
    )
    user = data.text if data.action != "custom" else f"Instruction: {data.instruction}\n\nText:\n{data.text}"
    return sse(llm.stream(data.model, [{"role": "system", "content": system}, {"role": "user", "content": user}], {"channel": "assist"}))


# Tasks
@api.get("/tasks")
def list_tasks(status: str | None = None, project: str | None = None):
    return services.list_tasks(status, project)


@api.post("/tasks")
def create_task(data: services.TaskIn):
    return services.create_task(data)


@api.patch("/tasks/{tid}")
def update_task(tid: int, data: services.TaskPatch):
    return services.update_task(tid, data)


@api.delete("/tasks/{tid}")
def delete_task(tid: int):
    services.delete_task(tid)
    return {"ok": True}


class BreakdownIn(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    project: str = Field("", max_length=120)
    model: str | None = None


@api.post("/tasks/breakdown")
async def breakdown(data: BreakdownIn):
    out = await llm.complete(data.model, [
        {"role": "system", "content": "Break goals into concrete next actions. Reply with only a JSON array of short task titles (max 10)."},
        {"role": "user", "content": data.goal},
    ], {"channel": "tasks"})
    titles = [t for t in (llm.parse_json(out) or []) if isinstance(t, str) and t.strip()][:10]
    if not titles:
        raise HTTPException(502, "The model did not return a task list. Try again or pick another model.")
    return [services.create_task(services.TaskIn(title=t[:500], project=data.project)) for t in titles]


# Calendar
def _calendar_out(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "password_enc"} | {"has_password": bool(c["password_enc"])}


@api.get("/calendars")
def calendars():
    services.local_calendar_id()
    return [_calendar_out(c) for c in db.all("SELECT * FROM calendars ORDER BY id")]


class CalendarIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: Literal["ics", "caldav", "local", "google", "microsoft", "mac"]  # google/microsoft/mac come from Connect, edits only
    url: str = Field("", max_length=1000)
    username: str = Field("", max_length=200)
    password: str | None = Field(None, max_length=500)
    color: str = Field("#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True


@api.post("/calendars")
async def add_calendar(data: CalendarIn):
    if data.kind in ("google", "microsoft", "mac"):
        raise HTTPException(400, "Use Connect Google or Connect Microsoft in Settings to add that calendar")
    if data.kind != "local" and not data.url.startswith(("http://", "https://", "webcal://")):
        raise HTTPException(400, "Calendar URL must start with https://, http:// or webcal://")
    cid = db.run(
        "INSERT INTO calendars(name, kind, url, username, password_enc, color, enabled) VALUES (?, ?, ?, ?, ?, ?, ?)",
        data.name, data.kind, data.url, data.username, encrypt(data.password or ""), data.color, int(data.enabled),
    )
    result = {"synced": 0}
    if data.kind != "local":
        try:
            result = await asyncio.to_thread(calendar_sync.sync, cid)
        except HTTPException as e:
            result = {"error": e.detail}
    return {**_calendar_out(db.one("SELECT * FROM calendars WHERE id = ?", cid)), **result}


@api.put("/calendars/{cid}")
def edit_calendar(cid: int, data: CalendarIn):
    if not db.one("SELECT id FROM calendars WHERE id = ?", cid):
        raise HTTPException(404, "Calendar not found")
    db.run("UPDATE calendars SET name = ?, url = ?, username = ?, color = ?, enabled = ? WHERE id = ?",
           data.name, data.url, data.username, data.color, int(data.enabled), cid)
    if data.password is not None:
        db.run("UPDATE calendars SET password_enc = ? WHERE id = ?", encrypt(data.password), cid)
    return _calendar_out(db.one("SELECT * FROM calendars WHERE id = ?", cid))


@api.delete("/calendars/{cid}")
def delete_calendar(cid: int):
    c = db.one("SELECT kind FROM calendars WHERE id = ?", cid)
    if c and c["kind"] in ("google", "microsoft"):
        raise HTTPException(400, "This calendar comes from a connected account. Turn it off, or disconnect the account.")
    db.run("DELETE FROM calendars WHERE id = ?", cid)
    return {"ok": True}


@api.post("/calendars/{cid}/sync")
async def sync_calendar(cid: int):
    return await asyncio.to_thread(calendar_sync.sync, cid)


class DiscoverIn(BaseModel):
    url: str = Field(..., pattern=r"^https?://")
    username: str = ""
    password: str = ""


@api.post("/calendars/discover")
async def discover(data: DiscoverIn):
    return await asyncio.to_thread(calendar_sync.discover, data.url, data.username, data.password)


@api.get("/events")
def events(start: str, end: str):
    return services.list_events(start, end)


@api.post("/events")
async def create_event(data: services.EventIn):
    return await asyncio.to_thread(services.create_event, data)


@api.delete("/events/{eid}")
def delete_event(eid: int):
    services.delete_event(eid)
    return {"ok": True}


# Mail
def _account(aid: int) -> dict:
    a = db.one("SELECT * FROM mail_accounts WHERE id = ?", aid)
    if not a:
        raise HTTPException(404, "Mail account not found")
    return a


@api.get("/mail/accounts")
def mail_accounts():
    return [{k: v for k, v in a.items() if k != "password_enc"} for a in db.all("SELECT * FROM mail_accounts ORDER BY id")]


class MacMailIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)   # the account's name in Apple Mail
    email: str = Field(..., min_length=3, max_length=320)


@api.get("/mail/mac")
async def mac_mail_accounts():
    """Mail accounts signed in on this Mac (Internet Accounts), for "Use the sign-in on this Mac"."""
    from . import mac_mail

    if not mac_mail.available():
        return {"available": False, "accounts": []}
    try:
        found = await asyncio.to_thread(mac_mail.accounts)
    except HTTPException as e:
        return {"available": True, "accounts": [], "error": e.detail}
    have = {(a["username"], a["email"].lower()) for a in db.all("SELECT username, email FROM mail_accounts WHERE kind = 'mac'")}
    return {"available": True, "accounts": [a | {"connected": any((a["name"], e.lower()) in have for e in a["emails"])} for a in found]}


@api.post("/mail/mac")
async def mac_mail_connect(data: MacMailIn):
    from . import mac_mail

    found = next((a for a in await asyncio.to_thread(mac_mail.accounts) if a["name"] == data.name), None)
    if not found or data.email.lower() not in {e.lower() for e in found["emails"]}:
        raise HTTPException(404, f"{data.email} isn't signed in on this Mac. Add it in System Settings > Internet Accounts.")
    if not found["enabled"]:
        raise HTTPException(400, f"Mail is switched off for {data.email} on this Mac. In System Settings > Internet Accounts, "
                                 "open the account and turn on Mail, then try again.")
    await asyncio.to_thread(mac_mail.list_messages, {"username": data.name}, "INBOX", 1)  # proves access (macOS asks once)
    row = db.one("SELECT id FROM mail_accounts WHERE kind = 'mac' AND lower(email) = lower(?)", data.email)
    if row:
        return {"id": row["id"]}
    aid = db.run("INSERT INTO mail_accounts(name, email, imap_host, imap_port, smtp_host, smtp_port, smtp_security, username, "
                 "password_enc, kind) VALUES (?, ?, 'apple-mail', 0, 'apple-mail', 0, 'ssl', ?, '', 'mac')",
                 data.email.split("@")[0], data.email, data.name)
    return {"id": aid}


@api.post("/mail/mac/settings")
def mac_mail_settings():
    """Open System Settings > Internet Accounts, where the user signs in with the provider's normal page."""
    import subprocess

    from . import mac_mail

    if not mac_mail.available():
        raise HTTPException(400, "The sign-in window isn't available here. Use a password instead.")
    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.Internet-Accounts-Settings.extension"])
    return {"ok": True}


@api.get("/mail/autodetect")
async def mail_autodetect(email: str):
    from . import mail_autodetect as autodetect

    return await asyncio.to_thread(autodetect.detect, email)


class MailAccountIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+$")
    imap_host: str = Field(..., min_length=3)
    imap_port: int = 993
    smtp_host: str = Field(..., min_length=3)
    smtp_port: int = 465
    smtp_security: Literal["ssl", "starttls"] = "ssl"
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    life_role: Literal["work", "personal", "other"] = "other"


@api.post("/mail/accounts")
async def add_mail_account(data: MailAccountIn):
    row = data.model_dump(exclude={"password"}) | {"password_enc": encrypt(data.password)}
    try:
        await asyncio.to_thread(mail.test, row)
    except HTTPException:
        # iCloud accepts the bare name before the @ when the full address is refused; try that once before giving up
        local = row["username"].split("@", 1)[0]
        if "@" not in row["username"] or row["imap_host"] != "imap.mail.me.com":
            raise
        row["username"] = local
        await asyncio.to_thread(mail.test, row)
    role = row.get("life_role") or "other"
    if role not in ("work", "personal", "other"):
        role = "other"
    aid = db.run(
        "INSERT INTO mail_accounts(name, email, imap_host, imap_port, smtp_host, smtp_port, smtp_security, username, "
        "password_enc, life_role) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        row["name"], row["email"], row["imap_host"], row["imap_port"], row["smtp_host"], row["smtp_port"],
        row["smtp_security"], row["username"], row["password_enc"], role,
    )
    return {"id": aid}


class MailRoleIn(BaseModel):
    life_role: Literal["work", "personal", "other"]


@api.patch("/mail/accounts/{aid}")
def patch_mail_account(aid: int, data: MailRoleIn):
    """Set whether this mailbox is work, personal, or other — so Chat can pick the right one."""
    if not db.one("SELECT id FROM mail_accounts WHERE id = ?", aid):
        raise HTTPException(404, "No mail account with that id")
    db.run("UPDATE mail_accounts SET life_role = ? WHERE id = ?", data.life_role, aid)
    return {"id": aid, "life_role": data.life_role}


@api.delete("/mail/accounts/{aid}")
def delete_mail_account(aid: int):
    a = db.one("SELECT kind FROM mail_accounts WHERE id = ?", aid)
    if a and a["kind"] in ("google", "microsoft"):
        raise HTTPException(400, "This inbox comes from a connected account. Disconnect it in Settings under Connect your mail & calendar.")
    db.run("DELETE FROM mail_accounts WHERE id = ?", aid)
    return {"ok": True}


@api.get("/mail/{aid}/messages")
async def mail_list(aid: int, folder: str = "INBOX", q: str = ""):
    rows = await asyncio.to_thread(mail.list_messages, _account(aid), folder, 50, q)
    try:
        from . import projects

        projects.file_mail(aid, rows, folder)
    except Exception:  # noqa: BLE001 - listing mail must not fail because filing did
        pass
    return rows


@api.get("/mail/{aid}/messages/{uid:path}")
async def mail_get(aid: int, uid: str, folder: str = "INBOX"):
    return await asyncio.to_thread(mail.get_message, _account(aid), uid, folder)


class SendIn(BaseModel):
    to: str = Field(..., min_length=3, max_length=2000)
    subject: str = Field("", max_length=500)
    body: str = Field(..., max_length=500_000)
    in_reply_to: str = ""
    draft_id: int | None = None


@api.post("/mail/{aid}/send")
async def mail_send(aid: int, data: SendIn):
    mid = await asyncio.to_thread(mail.send, _account(aid), data.to, data.subject, data.body, data.in_reply_to)
    if data.draft_id:
        db.run("DELETE FROM mail_drafts WHERE id = ?", data.draft_id)
    return {"ok": True, "message_id": mid}


class ReplyIn(BaseModel):
    message: str = Field(..., max_length=100_000)
    intent: str = Field("", max_length=2000, description="What the reply should say")
    model: str | None = None


@api.post("/mail/draft-reply")
def draft_reply(data: ReplyIn):
    return sse(llm.stream(data.model, [
        {"role": "system", "content": (
            "Draft an email reply. Be clear, direct and polite. Match the formality of the original. "
            "Never invent commitments, dates, prices or facts that the user did not provide; use [placeholders] instead. "
            "Output only the reply body, no subject line."
        )},
        {"role": "user", "content": f"Original email:\n{data.message}\n\nWhat my reply should say: {data.intent or 'a suitable reply'}"},
    ], {"channel": "mail"}))


@api.get("/mail/drafts")
def drafts():
    return db.all("SELECT * FROM mail_drafts ORDER BY updated_at DESC")


@api.delete("/mail/drafts/{did}")
def delete_draft(did: int):
    db.run("DELETE FROM mail_drafts WHERE id = ?", did)
    return {"ok": True}


# Research
class ResearchIn(BaseModel):
    question: str = Field(..., min_length=5, max_length=4000)
    model: str | None = None
    depth: int = Field(3, ge=1, le=6, description="Number of follow up search queries")


@api.post("/research")
def start_research(data: ResearchIn):
    model = data.model or llm.default_model_ref()
    llm.check_ref(model)
    now = db.now()
    rid = db.run("INSERT INTO research(question, model, created_at, updated_at) VALUES (?, ?, ?, ?)", data.question, model, now, now)
    task = asyncio.get_running_loop().create_task(research.run(rid, data.question, model, data.depth))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return db.one("SELECT * FROM research WHERE id = ?", rid)


@api.get("/research")
def list_research():
    return db.all("SELECT id, question, model, status, progress, created_at FROM research ORDER BY id DESC LIMIT 100")


@api.get("/research/{rid}")
def get_research(rid: int):
    r = db.one("SELECT * FROM research WHERE id = ?", rid)
    if not r:
        raise HTTPException(404, "Not found")
    return r | {"sources": json.loads(r["sources"])}


@api.delete("/research/{rid}")
def delete_research(rid: int):
    db.run("DELETE FROM research WHERE id = ?", rid)
    return {"ok": True}


@api.post("/research/{rid}/save")
def save_research(rid: int):
    r = get_research(rid)
    if r["status"] != "done":
        raise HTTPException(400, "Research is not finished")
    return services.create_doc(services.DocIn(kind="document", title=r["question"][:300], body=r["report"], tags="research"))


# Compare
class CompareIn(BaseModel):
    model: str
    messages: list[dict] = Field(..., min_length=1)
    system: str = Field("", max_length=20_000)


@api.post("/compare/stream")
def compare(data: CompareIn):
    msgs = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in data.messages
            if m.get("role") in ("user", "assistant")]
    if data.system.strip():
        msgs.insert(0, {"role": "system", "content": data.system})
    return sse(llm.stream(data.model, msgs, {"channel": "compare"}))


async def calendar_loop():
    while True:
        await asyncio.sleep(config.CALENDAR_SYNC_MINUTES * 60)
        await asyncio.to_thread(calendar_sync.sync_all)


def recover_interrupted():
    db.run("UPDATE research SET status = 'error', error = 'Interrupted by a restart', progress = 'Failed' WHERE status = 'running'")


# Reliability status. Fallback, parking and limits live in the smart router (router.py); these read its real state.
@api.get("/compression/status")
def compression_status():
    """Compression totals, and model call failures the router recovered from or reported, by reason (last 7 days)."""
    since = datetime.fromtimestamp(time.time() - 7 * 86400, timezone.utc).isoformat(timespec="seconds")
    return {
        "compressions": db.one("SELECT COUNT(*) as c, SUM(original_tokens) as orig, SUM(compressed_tokens) as comp FROM compressions"),
        "recoveries": db.all("SELECT failure_reason AS error_type, COUNT(*) AS count FROM actions WHERE kind = 'model_call' "
                             "AND failure_reason != '' AND ts >= ? GROUP BY failure_reason ORDER BY count DESC", since),
    }


@api.get("/credentials/status")
def credentials_status():
    """Every connection's health, never its key. Several keys for one provider are several connections in the router."""
    from . import router

    rows = []
    for c in db.all("SELECT * FROM connections WHERE auth_kind != 'session_scraping_forbidden' ORDER BY id"):
        q = router.quota(c)
        rows.append({"id": c["id"], "name": c["name"], "provider": router.profile(c).get("label", c["name"]), "enabled": bool(c["enabled"]),
                     "state": c["state"], "last_error": c["last_error"], "cooling_until": q.get("cooling_until"),
                     "cooling_reason": q.get("cooling_reason")})
    return {"connections": rows}


@api.get("/ratelimit/status")
def ratelimit_status():
    """Requests used, limits the providers reported, and cool-downs, per connection (the router's own counters)."""
    from . import router

    return {"connections": [{"id": c["id"], "name": c["name"], **router.quota(c)} for c in router.ordered_connections()]}
