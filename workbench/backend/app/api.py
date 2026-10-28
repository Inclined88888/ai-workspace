import asyncio
import json
import os
import re
import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import audit, calendar_sync, config, db, llm, mail, research, services, usage
from .security import (
    COOKIE, check_session, decrypt, encrypt, hash_password, make_session, require_user, throttle, verify_password,
)

public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
SETUP_CODE = os.getenv("SETUP_CODE", "")
_tasks: set[asyncio.Task] = set()


def sse(gen):
    async def body():
        try:
            async for item in gen:
                yield f"data: {json.dumps(item)}\n\n"
        except HTTPException as e:
            yield f"data: {json.dumps({'error': e.detail})}\n\n"
        except Exception as e:  # noqa: BLE001 - report to the client instead of dropping the stream
            yield f"data: {json.dumps({'error': str(e) or e.__class__.__name__})}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _login(response: Response) -> None:
    response.set_cookie(
        COOKIE, make_session(), max_age=config.SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=config.COOKIE_SECURE, path="/",
    )


# Status, setup, auth
@public.get("/status")
def status(request: Request):
    return {
        "setup_complete": db.get_setting("setup_complete") == "1",
        "signed_in": check_session(request.cookies.get(COOKIE)),
        "setup_code_required": bool(SETUP_CODE),
        "workspace_name": config.display_name(db.get_setting("workspace_name", config.WORKSPACE_NAME)),
        "chat_url": config.CHAT_URL,
        "paperclip_url": config.PAPERCLIP_URL,
        "hermes_url": config.HERMES_URL,
        "browser_enabled": bool(config.BROWSER_URL and config.BROWSER_API_KEY),
        "local_models_enabled": bool(config.OLLAMA_URL),
    }


class SetupIn(BaseModel):
    password: str = Field(..., min_length=10, max_length=200)
    setup_code: str = ""
    workspace_name: str = Field("", max_length=80)


@public.post("/setup")
def setup(data: SetupIn, request: Request, response: Response):
    throttle(request.client.host if request.client else "?")
    if db.get_setting("setup_complete") == "1":
        raise HTTPException(409, "Setup already completed")
    if SETUP_CODE and not secrets.compare_digest(data.setup_code.strip(), SETUP_CODE):
        raise HTTPException(403, "Setup code is wrong. It is printed by setup.sh and stored as SETUP_CODE in .env")
    db.set_setting("password_hash", hash_password(data.password))
    if data.workspace_name.strip():
        db.set_setting("workspace_name", data.workspace_name.strip())
    db.set_setting("setup_complete", "1")
    if not db.get_setting("ai_mode"):
        # New workspaces start in Free Cloud AI: nothing that costs money runs until you choose another mode
        db.set_setting("ai_mode", "free")
    audit.record("user", "setup", "workspace")
    services.local_calendar_id()
    _login(response)
    return {"ok": True}


class LoginIn(BaseModel):
    password: str


@public.post("/login")
def login(data: LoginIn, request: Request, response: Response):
    throttle(request.client.host if request.client else "?")
    stored = db.get_setting("password_hash")
    who = request.client.host if request.client else "?"
    if not stored or not verify_password(data.password, stored):
        audit.record("user", "sign in", who, "wrong password", ok=False)
        raise HTTPException(401, "Wrong password")
    audit.record("user", "sign in", who)
    _login(response)
    return {"ok": True}


@public.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


class PasswordIn(BaseModel):
    current: str
    new: str = Field(..., min_length=10, max_length=200)


@api.post("/password")
def change_password(data: PasswordIn, response: Response):
    if not verify_password(data.current, db.get_setting("password_hash", "")):
        audit.record("user", "password change", "", "current password wrong", ok=False)
        raise HTTPException(401, "Current password is wrong")
    db.set_setting("password_hash", hash_password(data.new))
    audit.record("user", "password change")
    db.set_setting("session_generation", secrets.token_hex(8))
    _login(response)
    return {"ok": True}


# Settings and providers
@api.get("/settings")
def get_settings():
    return {
        "workspace_name": config.display_name(db.get_setting("workspace_name", config.WORKSPACE_NAME)),
        "default_model": db.get_setting("default_model", ""),
        "chat_url": config.CHAT_URL,
        "tools_url": "http://workbench:8000/tools/openapi.json",
    }


class SettingsIn(BaseModel):
    workspace_name: str | None = Field(None, max_length=80)
    default_model: str | None = Field(None, max_length=300)


@api.put("/settings")
def put_settings(data: SettingsIn):
    if data.workspace_name is not None:
        db.set_setting("workspace_name", data.workspace_name.strip() or config.WORKSPACE_NAME)
    if data.default_model is not None:
        if data.default_model:
            llm.split_ref(data.default_model)
        db.set_setting("default_model", data.default_model)
    return get_settings()


def _provider_out(p: dict) -> dict:
    key = decrypt(p["api_key_enc"])
    return {**{k: p[k] for k in ("id", "name", "kind", "base_url", "models", "enabled")},
            "has_key": bool(key), "key_hint": f"…{key[-4:]}" if len(key) > 8 else ""}


@api.get("/providers")
def providers():
    return {"providers": [_provider_out(p) for p in db.all("SELECT * FROM providers ORDER BY id")], "presets": llm.PRESETS}


class ProviderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: Literal["openai", "anthropic", "ollama"] = "openai"
    base_url: str = Field(..., pattern=r"^https?://", max_length=300)
    api_key: str | None = Field(None, max_length=500)
    models: str = Field("", max_length=2000)
    enabled: bool = True


@api.post("/providers")
async def add_provider(data: ProviderIn):
    pid = db.run(
        "INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        data.name, data.kind, data.base_url.rstrip("/"), encrypt(data.api_key or ""), data.models, int(data.enabled), db.now(),
    )
    audit.record("user", "provider added", data.name, data.base_url)
    _check_later(pid)
    return _provider_out(db.one("SELECT * FROM providers WHERE id = ?", pid))


@api.put("/providers/{pid}")
async def edit_provider(pid: int, data: ProviderIn):
    if not db.one("SELECT id FROM providers WHERE id = ?", pid):
        raise HTTPException(404, "Provider not found")
    db.run(
        "UPDATE providers SET name = ?, kind = ?, base_url = ?, models = ?, enabled = ? WHERE id = ?",
        data.name, data.kind, data.base_url.rstrip("/"), data.models, int(data.enabled), pid,
    )
    if data.api_key is not None:
        db.run("UPDATE providers SET api_key_enc = ? WHERE id = ?", encrypt(data.api_key), pid)
    audit.record("user", "provider changed", data.name, "key replaced" if data.api_key is not None else "")
    llm._model_cache.pop(pid, None)
    db.run("DELETE FROM settings WHERE key = ?", f"provider_check:{pid}")  # an old result no longer describes this provider
    _check_later(pid)
    return _provider_out(db.one("SELECT * FROM providers WHERE id = ?", pid))


def _check_later(pid: int) -> None:
    """Check a provider in the background after it is added or changed; until then it is not used automatically."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    async def go():
        try:
            await llm.check_provider(pid)
        except Exception:  # noqa: BLE001 - recorded on the provider when possible; never raised to the caller
            pass
    t = loop.create_task(go())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


@api.delete("/providers/{pid}")
def delete_provider(pid: int):
    p = db.one("SELECT name FROM providers WHERE id = ?", pid)
    db.run("DELETE FROM providers WHERE id = ?", pid)
    if p:
        audit.record("user", "provider removed", p["name"])
    db.run("DELETE FROM settings WHERE key = ?", f"provider_check:{pid}")
    llm._model_cache.pop(pid, None)
    return {"ok": True}


class ConnectIn(BaseModel):
    allow_paid_check: bool = False  # explicit user opt-in; connection alone never authorizes billing
    confirm_free: bool = False  # Gemini or a local server: the person confirmed it cannot bill them
    preset: str = Field("", max_length=40)
    api_key: str = Field("", max_length=500)
    # custom endpoints only
    name: str = Field("", max_length=80)
    base_url: str = Field("", max_length=300)
    kind: Literal["openai", "anthropic", "ollama"] = "openai"
    models: str = Field("", max_length=2000)


@api.post("/providers/connect")
async def connect_provider(data: ConnectIn):
    """Paste a key, get a working model: creates the provider (or updates its key), then checks it for real."""
    if data.preset:
        preset = llm.PRESETS.get(data.preset)
        if not preset:
            raise HTTPException(400, "Unknown provider")
        name, base, kind = preset["name"], preset["base_url"], ("anthropic" if data.preset == "anthropic" else "ollama" if data.preset == "ollama" else "openai")
    else:
        if not re.match(r"^https?://", data.base_url.strip()):
            raise HTTPException(400, "Enter the endpoint's base URL, starting with http:// or https://")
        name, base, kind = data.name.strip() or "Custom", data.base_url.strip().rstrip("/"), data.kind
    if kind != "ollama" and data.preset != "llamacpp" and not data.api_key.strip() and not data.base_url:
        raise HTTPException(400, "Paste the API key first.")
    existing = db.one("SELECT * FROM providers WHERE base_url = ?", base)
    if existing:  # pasting a key again replaces it instead of adding a duplicate provider
        pid = existing["id"]
        if data.api_key.strip():
            db.run("UPDATE providers SET api_key_enc = ? WHERE id = ?", encrypt(data.api_key.strip()), pid)
        db.run("UPDATE providers SET enabled = 1 WHERE id = ?", pid)
        if data.models:
            db.run("UPDATE providers SET models = ? WHERE id = ?", data.models, pid)
    else:
        pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                     name, kind, base, encrypt(data.api_key.strip()), data.models, db.now())
    audit.record("user", "provider connected", name, base)
    if data.confirm_free and llm.can_confirm_free(llm._provider(pid)):
        db.set_setting(f"provider_free_confirmed:{pid}", "1")
        audit.record("user", "provider free confirmation", name, "confirmed: cannot bill")
    report = await llm.check_provider(pid, allow_paid=data.allow_paid_check)
    return {**report, "provider_id": pid, "status": models_status()}


@api.post("/providers/{pid}/check")
async def check_provider(pid: int, allow_paid: bool = False):
    report = await llm.check_provider(pid, allow_paid=allow_paid)
    return {**report, "provider_id": pid, "status": models_status()}


@api.get("/models/status")
def models_status():
    """What the assistant will use right now, its backups, and the health of each provider."""
    try:
        ref, source = llm.effective_default()
        default = {"ref": ref, "source": source, "error": ""}
    except HTTPException as e:
        default = {"ref": "", "source": "", "error": str(e.detail)}
    providers = []
    for p in db.all("SELECT * FROM providers ORDER BY id"):
        info = llm.check_info(p["id"])
        providers.append({**_provider_out(p), "checked": bool(info), "ok": bool(info.get("ok")), "error": info.get("error", ""),
                          "recommended": info.get("recommended", ""), "free_tier": info.get("free_tier"),
                          "models_count": info.get("models", 0), "free_models": info.get("free_models", 0),
                          "free_tool_models": info.get("free_tool_models"), "checked_at": info.get("checked_at", ""),
                          "local": llm._local_endpoint(p["base_url"]), "can_confirm_free": llm.can_confirm_free(p),
                          "free_confirmed": llm.confirmed_free(p)})
    saved = db.get_setting("default_model") or ""
    if default["ref"]:
        try:
            p, m = llm.split_ref(default["ref"])
            default["free_basis"] = llm.free_basis(p, m)
            default["local"] = llm.is_local(default["ref"])
            listed = next((x for x in llm._model_cache.get(p["id"], (0, []))[1] if x["model"] == m), {})
            default["price_in"], default["price_out"] = listed.get("price_in"), listed.get("price_out")
        except HTTPException:
            pass
    saved_blocked = llm.mode_allows(saved) if saved and llm._ref_ok(saved) else ""
    return {"default": default, "fallbacks": llm.fallback_refs(default["ref"]) if default["ref"] else [],
            "fallbacks_custom": db.get_setting("fallback_models") not in (None, ""), "saved_default": saved,
            "saved_default_broken": bool(saved) and default["source"] != "chosen" and not saved_blocked,
            "saved_default_blocked": saved_blocked, "mode": llm.ai_mode(), "providers": providers}


# AI mode: how ROOKS may use models. Changing it never removes a provider, a key or your saved default model.
MODE_INFO = {
    "free": "Only models verified as free: OpenRouter models whose listed price is zero, Gemini keys you confirmed have no "
            "billing, and models on your own computer. No paid fallback, no paid connection check.",
    "premium": "Paid models are allowed, within the monthly spending limit you set.",
    "local": "Only models on this computer or your local network. Nothing is sent to a cloud model.",
    "custom": "Your own compatible endpoint. ROOKS cannot see its prices, so set a spending limit if it can bill you.",
}


@api.get("/ai-mode")
def get_ai_mode():
    return {"mode": llm.ai_mode(), "modes": MODE_INFO, "monthly_limit_cents": float(db.get_setting("monthly_limit_cents", "0") or 0),
            "providers": db.one("SELECT COUNT(*) AS n FROM providers WHERE enabled = 1")["n"]}


class ModeIn(BaseModel):
    mode: Literal["free", "premium", "local", "custom"]
    monthly_limit_cents: float | None = Field(None, ge=0, le=10_000_000)


@api.put("/ai-mode")
def set_ai_mode(data: ModeIn):
    if data.monthly_limit_cents is not None:
        db.set_setting("monthly_limit_cents", str(int(data.monthly_limit_cents)))
    limit = float(db.get_setting("monthly_limit_cents", "0") or 0)
    if data.mode == "premium" and limit <= 0:
        raise HTTPException(400, "Premium Cloud AI needs a monthly spending limit. Enter one, for example $10.")
    before = llm.ai_mode()
    db.set_setting("ai_mode", data.mode)
    audit.record("user", "AI mode", data.mode, f"was {before or 'not chosen'}; monthly limit ${limit / 100:.2f}" if limit else f"was {before or 'not chosen'}")
    if data.mode in ("free", "local") and before != data.mode:
        # Find a model this mode allows. These checks send only free or local requests (check_provider enforces it).
        for p in db.all("SELECT id FROM providers WHERE enabled = 1"):
            _check_later(p["id"])
    return {**get_ai_mode(), "status": models_status()}


class FreeConfirmIn(BaseModel):
    confirmed: bool


@api.put("/providers/{pid}/free-confirm")
async def confirm_free(pid: int, data: FreeConfirmIn):
    """You confirm a provider cannot bill you (a Gemini key from a project without billing, or a local server with no
    paid API behind it). No API reports this, so it is your statement, recorded in the activity log."""
    p = llm._provider(pid)
    if not llm.can_confirm_free(p):
        raise HTTPException(400, f"{p['name']} cannot be confirmed as free here: ROOKS checks its prices itself, or it is a paid provider.")
    db.set_setting(f"provider_free_confirmed:{pid}", "1" if data.confirmed else "0")
    audit.record("user", "provider free confirmation", p["name"], "confirmed: cannot bill" if data.confirmed else "withdrawn")
    if data.confirmed:
        report = await llm.check_provider(pid)
        return {**report, "provider_id": pid, "status": models_status()}
    return {"ok": False, "provider_id": pid, "status": models_status()}


class FallbacksIn(BaseModel):
    refs: list[str] | None = Field(None, max_length=5)  # None = automatic


@api.put("/models/fallbacks")
def set_fallbacks(data: FallbacksIn):
    db.set_setting("fallback_models", "" if data.refs is None else json.dumps([r for r in data.refs if r]))
    return models_status()


@api.get("/models/roles")
def model_roles():
    from . import roles

    return roles.describe()


class RolesIn(BaseModel):
    roles: dict[str, str]


@api.put("/models/roles")
def set_model_roles(data: RolesIn):
    from . import roles

    return roles.set_roles(data.roles)


@api.get("/models/capabilities")
def model_capabilities(ref: str = ""):
    """Capabilities with their evidence (tested, reported by the provider, or unknown) for one model or all listed ones."""
    from . import roles

    refs = [ref] if ref else [m["ref"] for m in llm.list_models()]
    return [roles.capabilities(r) for r in refs[:300]]


class RefIn(BaseModel):
    ref: str = Field(..., max_length=300)


@api.post("/models/json-check")
async def model_json_check(data: RefIn):
    from . import roles

    llm.split_ref(data.ref)
    return await roles.json_check(data.ref)


@api.get("/models")
def models(fresh: bool = False):
    try:
        default = llm.default_model_ref()
    except HTTPException:
        default = ""
    from . import verify

    mode = llm.ai_mode()
    out = [dict(m) for m in llm.list_models(fresh)]  # copies: the list is cached, and flags must follow the current mode
    if mode in ("free", "local"):
        # Which models this AI mode allows, so pickers can put those first and mark the rest as unavailable
        for m in out:
            why = llm.mode_allows(m["ref"])
            m["allowed"], m["blocked"] = not why, why[:200]
    recommended = {f"{p['id']}/{llm.check_info(p['id']).get('recommended')}" for p in db.all("SELECT id FROM providers WHERE enabled = 1")
                   if llm.check_info(p["id"]).get("ok")}
    for m in out:
        # verified: a real request with a tool attached succeeded (the connection check, or the capability test)
        m["verified"] = m["ref"] in recommended or bool(verify.results(m["ref"]).get("tools", {}).get("ok"))
        m["status"] = "blocked" if m.get("allowed") is False else "ready" if m["verified"] else "untested"
    return {"models": out, "default": default, "mode": mode}


@api.get("/connection-directory")
def connections_directory():
    """Every way ROOKS can reach a model, grouped by what it is (connections.py)."""
    from . import connections

    return connections.directory()


class CapTestIn(BaseModel):
    ref: str = Field(..., max_length=300)
    caps: list[str] = Field(default_factory=lambda: ["chat", "tools", "tool_roundtrip", "streaming", "json"], max_length=8)
    allow_paid: bool = False


@api.post("/connection-directory/test")
async def connections_test(data: CapTestIn):
    """Test chosen capabilities of one model, each separately; recorded with time and what happened."""
    from . import verify

    return await verify.run(data.ref, data.caps, data.allow_paid)


class EngineIn(BaseModel):
    engine: Literal["", "any-llm"]


@api.put("/providers/{pid}/engine")
def provider_engine(pid: int, data: EngineIn):
    """Anthropic connections only: use the optional any-llm engine (native Messages API) or the compatibility endpoint."""
    from . import anyllm_engine

    p = db.one("SELECT * FROM providers WHERE id = ?", pid)
    if not p:
        raise HTTPException(404, "Provider not found")
    if data.engine and p["kind"] != "anthropic":
        raise HTTPException(400, "The any-llm engine is offered for Anthropic connections only.")
    if data.engine and not anyllm_engine.available():
        raise HTTPException(400, "any-llm is not installed. Install it with: pip install any-llm-sdk==1.40.2")
    db.set_setting(f"provider_engine:{pid}", data.engine)
    for k in [r["key"] for r in db.all("SELECT key FROM settings WHERE key LIKE ?", f"cap_check:{pid}/%")]:
        db.run("DELETE FROM settings WHERE key = ?", k)  # results were for the other engine: test again
    audit.record("user", "provider engine", p["name"], data.engine or "compatibility")
    return {"ok": True, "engine": llm.engine(p) or "compatibility"}


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
    consent: bool = False  # the person saw the size, memory and licence and agreed to the download


# Models offered for download, with what a download costs you. Sizes are Ollama's listed download sizes; memory is a
# rough guide (about the download size plus working room), not a measurement on your computer.
LOCAL_CATALOG = [
    {"model": "qwen3:4b", "size_gb": 2.5, "memory_gb": 4, "licence": "Apache 2.0", "licence_url": "https://huggingface.co/Qwen/Qwen3-4B",
     "note": "Fast all rounder. Runs without a graphics card, slowly."},
    {"model": "qwen3:8b", "size_gb": 5.2, "memory_gb": 8, "licence": "Apache 2.0", "licence_url": "https://huggingface.co/Qwen/Qwen3-8B",
     "note": "Better reasoning. Best with a graphics card or Apple silicon."},
    {"model": "gemma3:4b", "size_gb": 3.4, "memory_gb": 5, "licence": "Gemma Terms of Use", "licence_url": "https://ai.google.dev/gemma/terms",
     "note": "Google's small model. Its licence has use restrictions: read it first."},
]


@api.get("/local/catalog")
def local_catalog():
    return {"models": LOCAL_CATALOG, "ollama_configured": bool(config.OLLAMA_URL)}


@api.post("/ollama/pull")
async def ollama_pull(data: PullIn):
    import httpx

    if not config.OLLAMA_URL:
        raise HTTPException(400, "Local models are not enabled. Run setup with --with local-models, or set OLLAMA_URL.")
    if not data.consent:
        raise HTTPException(400, "A download needs your agreement first: check its size, memory and licence, then agree.")
    item = next((m for m in LOCAL_CATALOG if m["model"] == data.model), None)
    audit.record("user", "local model download", data.model, f"{item['size_gb']} GB, {item['licence']}" if item else "size not known")

    async def gen():
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10)) as c:
            async with c.stream("POST", f"{config.OLLAMA_URL}/api/pull", json={"model": data.model, "stream": True}) as r:
                async for line in r.aiter_lines():
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
        for p in db.all("SELECT id FROM providers WHERE kind = 'ollama'"):
            llm._model_cache.pop(p["id"], None)
        yield {"done": True}

    return sse(gen())


# Dashboard
@api.get("/dashboard")
def dashboard():
    return {
        **services.agenda(7),
        "recent_docs": services.list_docs(limit=6),
        "research": db.all("SELECT id, question, status, created_at FROM research ORDER BY id DESC LIMIT 5"),
    }


# Appearance (theme, font, size, density...): stored per workspace so it follows you to every device
@api.get("/appearance")
def get_appearance():
    try:
        return json.loads(db.get_setting("appearance", "") or "{}")
    except ValueError:
        return {}


@api.put("/appearance")
async def put_appearance(request: Request):
    raw = await request.body()
    if len(raw) > 32_000:
        raise HTTPException(413, "Appearance settings are too large")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise HTTPException(400, "Appearance must be JSON") from e
    if not isinstance(data, dict):
        raise HTTPException(400, "Appearance must be an object")
    db.set_setting("appearance", json.dumps(data))  # the browser validates and normalises; stored as given
    return data


# Getting started: real counts only, so the checklist never claims something is set up when it is not
@api.get("/getting-started")
def getting_started():
    from . import search

    n = lambda sql: db.one(f"SELECT COUNT(*) AS n FROM {sql}")["n"]  # noqa: E731
    return {
        "dismissed": db.get_setting("getting_started_dismissed") == "1",
        "providers": n("providers WHERE enabled = 1"),
        "conversations": n("conversations WHERE channel IN ('web', 'cli', 'telegram')"),
        "tasks": n("tasks"),
        "notes": n("docs WHERE kind = 'note'"),
        "documents": n("docs WHERE kind = 'document'"),
        "research": n("research"),
        "calendars": n("calendars WHERE kind != 'local'"),
        "mail_accounts": n("mail_accounts"),
        "schedules": n("schedules"),
        "agents": n("agents"),
        "memories": n("memories"),
        "search_provider": search.provider(),
        "telegram": bool(db.get_setting("secret:TELEGRAM_BOT_TOKEN", "")),
    }


class Dismiss(BaseModel):
    dismissed: bool


@api.put("/getting-started")
def set_getting_started(data: Dismiss):
    db.set_setting("getting_started_dismissed", "1" if data.dismissed else "0")
    return getting_started()


class ModelTestIn(BaseModel):
    model: str = Field("", max_length=300)


@api.post("/models/test")
async def test_model(data: ModelTestIn):
    """Send one tiny real request to a model, so a connection is only reported as working after it answered."""
    started = time.monotonic()
    try:
        res = await llm.chat(data.model or None, [{"role": "user", "content": "Reply with only the word OK."}])
    except HTTPException as e:
        return {"ok": False, "model": data.model, "error": str(e.detail)}
    usage.record(res, channel="test")
    reply = (res["message"].get("content") or "").strip()
    return {"ok": True, "model": res["ref"], "reply": reply[:80], "ms": int((time.monotonic() - started) * 1000),
            "fallback_from": res.get("fallback_from", [])}


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
    return sse(llm.stream(data.model, [{"role": "system", "content": system}, {"role": "user", "content": user}]))


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
    ])
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
    kind: Literal["ics", "caldav", "local"]
    url: str = Field("", max_length=1000)
    username: str = Field("", max_length=200)
    password: str | None = Field(None, max_length=500)
    color: str = Field("#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True


@api.post("/calendars")
async def add_calendar(data: CalendarIn):
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


@api.post("/mail/accounts")
async def add_mail_account(data: MailAccountIn):
    row = data.model_dump(exclude={"password"}) | {"password_enc": encrypt(data.password)}
    await asyncio.to_thread(mail.test, row)
    aid = db.run(
        "INSERT INTO mail_accounts(name, email, imap_host, imap_port, smtp_host, smtp_port, smtp_security, username, password_enc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        row["name"], row["email"], row["imap_host"], row["imap_port"], row["smtp_host"], row["smtp_port"],
        row["smtp_security"], row["username"], row["password_enc"],
    )
    return {"id": aid}


@api.delete("/mail/accounts/{aid}")
def delete_mail_account(aid: int):
    db.run("DELETE FROM mail_accounts WHERE id = ?", aid)
    return {"ok": True}


@api.get("/mail/{aid}/messages")
async def mail_list(aid: int, folder: str = "INBOX", q: str = ""):
    return await asyncio.to_thread(mail.list_messages, _account(aid), folder, 50, q)


@api.get("/mail/{aid}/messages/{uid}")
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
    ]))


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
    project_id: int | None = None


@api.post("/research")
async def start_research(data: ResearchIn):
    return research.start(data.question, data.depth, data.project_id, data.model)


def _start_research(rid: int, question: str, model: str, depth: int) -> None:
    research.spawn(rid, question, model, depth)


@api.post("/research/{rid}/retry")
async def retry_research(rid: int):
    """Run a failed or interrupted research job again, with the same question, model and depth."""
    r = db.one("SELECT * FROM research WHERE id = ?", rid)
    if not r:
        raise HTTPException(404, "Not found")
    if not db.changed("UPDATE research SET status = 'running', error = '', progress = 'Starting again', report = '', sources = '[]', "
                      "plan = '[]', stats = '{}', skipped = '[]', rounds = '[]', cancel = 0, updated_at = ? WHERE id = ? AND status IN ('error', 'cancelled')",
                      db.now(), rid):
        raise HTTPException(409, "Only failed research can be retried")
    db.run("DELETE FROM evidence WHERE research_id = ?", rid)
    _start_research(rid, r["question"], r["model"], r["depth"])
    return db.one("SELECT * FROM research WHERE id = ?", rid)


@api.get("/research")
def list_research():
    return db.all("SELECT id, question, model, status, progress, created_at FROM research ORDER BY id DESC LIMIT 100")


@api.get("/research/{rid}")
def get_research(rid: int):
    r = db.one("SELECT * FROM research WHERE id = ?", rid)
    if not r:
        raise HTTPException(404, "Not found")
    out = r | {k: json.loads(r[k] or d) for k, d in (("sources", "[]"), ("plan", "[]"), ("stats", "{}"), ("skipped", "[]"), ("rounds", "[]"))}
    out["evidence"] = research.ledger(rid)
    topic = db.one("SELECT id, cron, enabled, last_check_at FROM topics WHERE id = ?", r["monitor_topic_id"]) if r.get("monitor_topic_id") else None
    out["monitor"] = topic
    return out


@api.post("/research/{rid}/cancel")
def cancel_research(rid: int):
    """Stop a running research job at its next checkpoint (between searches, page reads and writing)."""
    if not db.changed("UPDATE research SET cancel = 1, progress = 'Cancelling…' WHERE id = ? AND status = 'running'", rid):
        raise HTTPException(409, "Only running research can be cancelled")
    return get_research(rid)


@api.delete("/research/{rid}")
def delete_research(rid: int):
    r = db.one("SELECT monitor_topic_id FROM research WHERE id = ?", rid)
    if r and r["monitor_topic_id"] and db.one("SELECT id FROM topics WHERE id = ?", r["monitor_topic_id"]):
        from . import checkins

        checkins.delete(r["monitor_topic_id"])
    db.run("DELETE FROM evidence WHERE research_id = ?", rid)
    db.run("DELETE FROM research WHERE id = ?", rid)
    return {"ok": True}


class SaveIn(BaseModel):
    project_id: int | None = None


@api.post("/research/{rid}/save")
def save_research(rid: int, data: SaveIn | None = None):
    """Save the report as a document, with its evidence table; in a project, the document and the evidence belong to it."""
    from . import projects

    r = get_research(rid)
    if r["status"] != "done":
        raise HTTPException(400, "Research is not finished")
    pid = (data.project_id if data else None) or r.get("project_id")
    if pid:
        p = projects.get(pid)
        if not p:
            raise HTTPException(404, "Project not found")
        if p["local_only"] and not llm.is_local(r["model"]):
            raise HTTPException(409, f"{p['name']} is local only, and this research used {r['model']}, which is not local.")
    rows = "\n".join(f"| {e['basis']} | {e['claim'].replace('|', '/')[:200]} | {('[' + str(e['source_n']) + ']') if e['source_n'] else ''} | "
                     f"{e['verification'].replace('_', ' ')} | {e['confidence']:.2f} |" for e in r["evidence"])
    body = r["report"] + (f"\n\n## Evidence\n\n| Basis | Claim | Source | Independent check | Confidence |\n|---|---|---|---|---|\n{rows}" if rows else "")
    doc = services.create_doc(services.DocIn(kind="document", title=r["question"][:300], body=body, tags="research"))
    if pid:
        db.run("UPDATE docs SET project_id = ? WHERE id = ?", pid, doc["id"])
        db.run("UPDATE evidence SET project_id = ? WHERE research_id = ?", pid, rid)
        db.run("UPDATE research SET project_id = COALESCE(project_id, ?) WHERE id = ?", pid, rid)
        doc["project_id"] = pid
    audit.record("user", "research saved", f"#{rid}", f"document #{doc['id']}" + (f" in project #{pid}" if pid else ""))
    return doc


class MonitorIn(BaseModel):
    cron: str = Field("0 8 * * 1", max_length=100)
    notify: str = Field("inbox", pattern="^(inbox|telegram)$")


@api.post("/research/{rid}/monitor")
def monitor_research(rid: int, data: MonitorIn):
    """Opt in to follow this question: a check-in that searches it on a schedule and tells you only about new results."""
    from . import checkins

    r = get_research(rid)
    if r.get("monitor") and r["monitor"]:
        raise HTTPException(409, "This research is already being followed.")
    t = checkins.create(f"Research: {r['question'][:80]}", data.cron, [f"search:{r['question'][:400]}"],
                        description=f"New results for research #{rid}.", project_id=r.get("project_id"), importance="low", notify=data.notify)
    db.run("UPDATE research SET monitor_topic_id = ? WHERE id = ?", t["id"], rid)
    return get_research(rid)


@api.delete("/research/{rid}/monitor")
def stop_monitor(rid: int):
    from . import checkins

    r = get_research(rid)
    if r.get("monitor"):
        checkins.delete(r["monitor"]["id"])
    db.run("UPDATE research SET monitor_topic_id = NULL WHERE id = ?", rid)
    return get_research(rid)


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
    return sse(llm.stream(data.model, msgs))


async def calendar_loop():
    while True:
        await asyncio.sleep(config.CALENDAR_SYNC_MINUTES * 60)
        await asyncio.to_thread(calendar_sync.sync_all)


def recover_interrupted():
    db.run("UPDATE research SET status = 'error', error = 'Interrupted by a restart. Press Retry to run it again.', progress = 'Failed' WHERE status = 'running'")

