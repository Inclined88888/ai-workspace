import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

from . import redact

redact.install()  # before anything logs: no secret reaches a log sink (R7)

from . import egress  # noqa: E402

egress.install()  # before anything connects: every outbound request is in the audit log (R8, item 75)

from . import actions, api, assistant_api, bridge, browser_proxy, company, config, connect, db, health, llm, mobile, scheduler, skills_hub, telegram  # noqa: E402
from .mcp_server import mcp, mcp_app
from .security import check_bearer
from .tools import tools


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    llm.seed_connections()
    api.recover_interrupted()
    actions.recover()
    company.recover()
    mobile.boot()
    loops = [asyncio.create_task(fn()) for fn in (api.calendar_loop, scheduler.loop, telegram.loop, company.loop)]
    health.LOOPS.update(zip(("Calendar sync", "Scheduler", "Telegram", "Company"), loops))
    async with mcp.session_manager.run():
        yield
    mobile.lan_stop()
    for t in loops:
        t.cancel()


app = FastAPI(title="Workbench", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    # CSRF: state changing API calls must carry a custom header, which cross site pages cannot send without CORS.
    if request.url.path.startswith("/mcp") and not check_bearer(request):
        return JSONResponse({"detail": "Invalid or missing bearer token"}, status_code=401)
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-workbench") != "1":
            return JSONResponse({"detail": "Missing X-Workbench header"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if not request.url.path.startswith("/tools"):
        response.headers.setdefault("X-Frame-Options", "DENY")
    return response


# R7: error messages reach clients redacted. Provider errors can quote keys; 422 errors echo the rejected input.
@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    return JSONResponse({"detail": redact.redact_obj(exc.detail)}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse({"detail": redact.redact_obj(jsonable_encoder(exc.errors()))}, status_code=422)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Container liveness: the process answers and the database is readable. Integrations are /api/health (item 25)."""
    d = health.database()
    return JSONResponse({"ok": d["status"] != "down", "database": d["status"]}, status_code=503 if d["status"] == "down" else 200)


from . import openapi  # registers authenticated schema before including the router

app.include_router(api.public)
app.include_router(api.api)
app.include_router(assistant_api.router)
app.include_router(connect.router)  # Connect Google / Connect Microsoft: one sign-in for mail and calendar
app.include_router(mobile.public)  # phone companion app: pairing, then device-token routes (mobile/)
app.include_router(mobile.owner)
app.include_router(mobile.device)
app.include_router(browser_proxy.router)
app.include_router(skills_hub.router)  # Skills from GitHub behind the security gate
app.include_router(bridge.router)  # /v1: OpenAI-shaped access to every connection, for the browser agent (item 103)
app.mount("/tools", tools)
app.mount("/mcp", mcp_app)  # MCP clients use http://workbench:8000/mcp/

if config.STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=config.STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "tools/", "mcp", "v1/")):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = (config.STATIC_DIR / path).resolve()
        if path and candidate.is_file() and config.STATIC_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
