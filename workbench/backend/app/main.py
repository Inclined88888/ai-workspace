import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, browser_proxy, config, db, llm
from .tools import tools


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    llm.seed_providers()
    api.recover_interrupted()
    loop_task = asyncio.create_task(api.calendar_loop())
    yield
    loop_task.cancel()


app = FastAPI(title="Workbench", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    # CSRF: state changing API calls must carry a custom header, which cross site pages cannot send without CORS.
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
app.include_router(browser_proxy.router)
app.mount("/tools", tools)

if config.STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=config.STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "tools/")):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = (config.STATIC_DIR / path).resolve()
        if path and candidate.is_file() and config.STATIC_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
