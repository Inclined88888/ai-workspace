import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, assistant_api, browser_proxy, company, computer, computer_api, config, db, llm, missions, projects_api, scheduler, telegram
from .mcp_server import mcp, mcp_app
from .security import check_bearer
from .tools import tools


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    llm.seed_providers()
    api.recover_interrupted()
    company.recover()
    missions.recover()
    computer.recover()
    loops = [asyncio.create_task(fn()) for fn in (api.calendar_loop, scheduler.loop, telegram.loop, company.loop, missions.loop)]
    loops.append(asyncio.create_task(llm.check_unchecked()))
    async with mcp.session_manager.run():
        yield
    for t in loops:
        t.cancel()


app = FastAPI(title="ROOKS", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


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


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


app.include_router(api.public)
app.include_router(api.api)
app.include_router(assistant_api.router)
app.include_router(projects_api.router)
app.include_router(computer_api.router)
app.include_router(computer_api.companion)
app.include_router(browser_proxy.router)
app.mount("/tools", tools)
app.mount("/mcp", mcp_app)  # MCP clients use http://workbench:8000/mcp/

if config.STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=config.STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "tools/", "mcp")):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = (config.STATIC_DIR / path).resolve()
        if path and candidate.is_file() and config.STATIC_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
