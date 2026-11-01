"""Image generation through the providers the user already connected (Phase 4, media).

No model runs in this container: the image comes from a connected provider and is saved to Files like any other output.
Two wire shapes cover the common providers:

* images API (POST {base}/images/generations): OpenAI (gpt-image-1), xAI (grok-2-image), Google Gemini's OpenAI
  compatible endpoint (Imagen), and any OpenAI compatible server that implements it (LocalAI, a ComfyUI bridge...).
* chat with image output (POST {base}/chat/completions, modalities ["image", "text"]): OpenRouter.

Every call is one model_call row in the run log (so it shows in Runs, Insights and the egress audit) and costs money
on most providers, which is why the assistant tool is an "external" effect.
"""

from __future__ import annotations

import base64
import re
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import analysis, db, llm
from .adapters import credential
from .redact import redact
from .security import require_user

# host -> (wire shape, default model, sends size)
KNOWN = {
    "api.openai.com": ("images", "gpt-image-1", True),
    "api.x.ai": ("images", "grok-2-image", False),
    "generativelanguage.googleapis.com": ("images", "imagen-4.0-generate-001", False),
    "openrouter.ai": ("chat", "google/gemini-2.5-flash-image", False),
}
SIZES = {"square": "1024x1024", "portrait": "1024x1536", "landscape": "1536x1024"}
MAX_PROMPT = 4000


def _host(conn: dict) -> str:
    return (urlparse(conn.get("base_url") or "").hostname or "").lower()


def profile(conn: dict) -> dict | None:
    """How this connection makes images, or None. Unknown OpenAI compatible servers qualify once a model is set."""
    override = (db.get_setting(f"image_model:{conn['id']}", "") or "").strip()
    known = KNOWN.get(_host(conn))
    if known:
        shape, model, sizes = known
        return {"shape": shape, "model": override or model, "sizes": sizes}
    if override:  # any OpenAI compatible server the user pointed at an image model
        return {"shape": "images", "model": override, "sizes": True}
    return None


def providers() -> list[dict]:
    out = []
    for c in db.all("SELECT * FROM connections WHERE enabled = 1 ORDER BY id"):
        p = profile(c)
        if p:
            out.append({"connection_id": c["id"], "name": c["name"], "model": p["model"]})
    return out


def _pick(connection_id: int | None) -> tuple[dict, dict]:
    if connection_id:
        c = db.one("SELECT * FROM connections WHERE id = ? AND enabled = 1", connection_id)
        p = profile(c) if c else None
        if not c or not p:
            raise HTTPException(400, "That connection can't make images. Use OpenAI, Gemini, xAI or OpenRouter, "
                                     "or set an image model for an OpenAI compatible server.")
        return c, p
    for c in db.all("SELECT * FROM connections WHERE enabled = 1 ORDER BY id"):
        p = profile(c)
        if p:
            return c, p
    raise HTTPException(400, "No connected provider can make images. Add OpenAI, Google Gemini, xAI or OpenRouter in Models.")


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    if url.startswith("data:"):
        head, _, b64 = url.partition(",")
        return base64.b64decode(b64), (head[5:].split(";")[0] or "image/png")
    if not url.startswith("https://"):
        raise HTTPException(502, "The provider returned an image link that isn't https")
    r = await client.get(url)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "image/png").split(";")[0]


async def _call(conn: dict, p: dict, prompt: str, size: str) -> tuple[bytes, str]:
    base = (conn["base_url"] or "").rstrip("/")
    headers = {"Authorization": f"Bearer {credential(conn)}"}
    async with httpx.AsyncClient(timeout=180) as c:
        if p["shape"] == "chat":
            r = await c.post(f"{base}/chat/completions", headers=headers, json={
                "model": p["model"], "modalities": ["image", "text"],
                "messages": [{"role": "user", "content": f"Create an image: {prompt}"}]})
        else:
            body = {"model": p["model"], "prompt": prompt, "n": 1}
            if p["sizes"]:
                body["size"] = SIZES[size]
            if _host(conn) != "api.openai.com":  # gpt-image-1 always returns base64 and rejects the field
                body["response_format"] = "b64_json"
            r = await c.post(f"{base}/images/generations", headers=headers, json=body)
        if r.status_code >= 400:
            raise HTTPException(502, f"{conn['name']} error {r.status_code}: {redact(r.text[:300])}")
        d = r.json()
        if p["shape"] == "chat":
            msg = ((d.get("choices") or [{}])[0].get("message") or {})
            imgs = msg.get("images") or []
            url = ((imgs[0] if imgs else {}).get("image_url") or {}).get("url", "")
            if not url:
                raise HTTPException(502, f"{p['model']} answered without an image. Pick an image model.")
            return await _fetch(c, url)
        item = (d.get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"]), "image/png"
        if item.get("url"):
            return await _fetch(c, item["url"])
        raise HTTPException(502, "The provider returned no image")


def _name(prompt: str, mime: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:48] or "image"
    ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    return f"{slug}.{ext}"


async def generate(prompt: str, size: str = "square", connection_id: int | None = None, trace: dict | None = None) -> dict:
    """Make one image and save it to Files. Returns the file row plus which provider and model made it."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(422, "Describe the image")
    if len(prompt) > MAX_PROMPT:
        raise HTTPException(422, f"Keep the description under {MAX_PROMPT} characters")
    if size not in SIZES:
        raise HTTPException(422, f"size is one of: {', '.join(SIZES)}")
    conn, p = _pick(connection_id)
    started = time.monotonic()
    trace = trace or {"channel": "media"}
    ref = f"{conn['id']}/{p['model']}"
    try:
        data, mime = await _call(conn, p, prompt, size)
    except HTTPException as e:
        llm._record(conn, ref, trace, started, {}, "error", str(e.detail)[:300])  # one model_call row, like every model call
        raise
    except httpx.HTTPError as e:
        llm._record(conn, ref, trace, started, {}, "error", type(e).__name__)
        raise HTTPException(502, f"{conn['name']} didn't answer ({type(e).__name__})") from e
    llm._record(conn, ref, trace, started, {}, "ok")
    f = analysis.add(_name(prompt, mime), data=data, source="output")
    return {"file_id": f["id"], "name": f["name"], "size_bytes": len(data), "provider": conn["name"], "model": p["model"]}


# HTTP --------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/media", dependencies=[Depends(require_user)])


class ImageIn(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT)
    size: str = "square"
    connection_id: int | None = None


class ImageModelIn(BaseModel):
    connection_id: int
    model: str = Field("", max_length=200)


@router.get("/images/providers")
def image_providers():
    return {"providers": providers(), "sizes": list(SIZES)}


@router.post("/images")
async def make_image(body: ImageIn):
    return await generate(body.prompt, body.size, body.connection_id)


@router.put("/images/model")
def set_image_model(body: ImageModelIn):
    """Pick the image model for a connection (or set one for an OpenAI compatible server). Empty restores the default."""
    if not db.one("SELECT id FROM connections WHERE id = ?", body.connection_id):
        raise HTTPException(404, "No such connection")
    db.set_setting(f"image_model:{body.connection_id}", body.model.strip())
    return image_providers()
