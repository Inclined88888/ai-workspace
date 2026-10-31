"""HTTP API for the Files and Sites pages. Mounted by main.py; everything needs the signed-in user."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import analysis, compute, config, files, helpers, sites
from .security import require_user

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
MAX_UPLOAD = 1024 * 1024 * 1024


# --- files ----------------------------------------------------------------------------------------------------------
@router.get("/files")
def list_files():
    return analysis.listing()


@router.post("/files/upload")
async def upload(request: Request, name: str):
    """Raw body upload (one file per request), so any size streams without a form parser."""
    size, chunks = 0, []
    async for c in request.stream():
        size += len(c)
        if size > MAX_UPLOAD:
            raise HTTPException(413, "Files up to 1 GB can be added.")
        chunks.append(c)
    return analysis.add(name, data=b"".join(chunks))


class PathIn(BaseModel):
    path: str


@router.post("/files/from-path")
def from_path(body: PathIn):
    """Desktop app only: add a file or folder from this computer by its path (the app runs on the user's own machine)."""
    if not config.DESKTOP:
        raise HTTPException(400, "Adding by path works in the desktop app. Here, drop the file on the page instead.")
    p = Path(body.path).expanduser()
    if not p.exists():
        raise HTTPException(404, f"Nothing at {p}.")
    return analysis.add(p.name, src=p, source="path")


@router.get("/files/capabilities")
def capabilities():
    have = {}
    for mod, label in (("openpyxl", "Excel"), ("docx", "Word"), ("pdfplumber", "PDF"), ("pptx", "PowerPoint"), ("extract_msg", "Outlook .msg"),
                       ("ezdxf", "DXF drawings"), ("pandas", "Statistics"), ("matplotlib", "Charts"), ("sympy", "Maths (sympy)"),
                       ("pyarrow", "Parquet"), ("h5py", "HDF5 / NetCDF-4"), ("fpdf", "PDF reports"), ("PIL", "Images"), ("mutagen", "Audio / video")):
        try:
            __import__(mod)
            have[label] = True
        except ImportError:
            have[label] = False
    import shutil

    apps = {"LibreOffice (old Office formats)": bool(shutil.which("soffice") or shutil.which("libreoffice") or Path("/Applications/LibreOffice.app").exists()),
            "ODA File Converter (DWG)": bool(files.oda_converter()), "Tesseract (scanned pages)": bool(shutil.which("tesseract")),
            "FFmpeg (video details)": bool(shutil.which("ffprobe"))}
    return {"readers": have, "apps": apps, "sandbox": compute.available(), "helpers": helpers.status()}


@router.get("/files/helpers")
def helpers_status():
    return helpers.status()


@router.post("/files/helpers/install")
def helpers_install():
    """The one Install button on the Files page: quietly installs the missing helper apps (brew / winget)."""
    return helpers.install()


@router.get("/files/{fid}")
def file_detail(fid: int):
    f = analysis.get(fid)
    d = analysis.extract_of(fid)
    return {**f, "extract": {**d, "chunks": d["chunks"][:400]}, "inside": [{"name": x["name"], "kind": x["kind"], "summary": files.summary(x),
                                                                              "notes": x["notes"]} for x in list(files.walk(d))[1:200]]}


@router.get("/files/{fid}/download")
def download(fid: int):
    f = analysis.get(fid)
    p = Path(f["path"])
    if not p.is_file():
        raise HTTPException(404, "The file is gone.")
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    inline = media.startswith("image/") or media == "application/pdf"
    return FileResponse(p, media_type=media, filename=f["name"], content_disposition_type="inline" if inline else "attachment")


@router.delete("/files/{fid}")
def delete_file(fid: int):
    return analysis.remove(fid)


@router.get("/files/{fid}/analyse")
def analyse(fid: int, sheet: str = ""):
    return analysis.analyse_spreadsheet(fid, sheet)


class CompareIn(BaseModel):
    a: int
    b: int
    report: bool = False


@router.post("/files/compare")
def compare(body: CompareIn):
    res = analysis.compare(body.a, body.b)
    if body.report:
        res["report"] = analysis.make_word(f"Changes {res['a']} to {res['b']}", res["summary"], compare_with=res)
    return res


@router.get("/files-search")
def search(q: str):
    return analysis.search(q)


class RunIn(BaseModel):
    code: str = Field(..., max_length=200_000)
    file_ids: list[int] = []


@router.post("/files/run")
def run(body: RunIn):
    return compute.run(body.code, body.file_ids)


class ReportIn(BaseModel):
    title: str
    body: str = ""
    format: str = "docx"
    rows: list[dict] = []


@router.post("/files/report")
def report(body: ReportIn):
    if body.format == "xlsx":
        return analysis.make_excel(body.title, {"Summary": body.rows or [{"Notes": line} for line in body.body.splitlines() if line.strip()]})
    if body.format == "pdf":
        return analysis.make_pdf(body.title, body.body)
    return analysis.make_word(body.title, body.body)


# --- sites ---------------------------------------------------------------------------------------------------------
class SiteIn(BaseModel):
    name: str = ""
    url: str
    mode: str = "auto"
    api_key: str = ""
    max_pages: int = 30


@router.get("/sites")
def list_sites():
    return {"sites": sites.listing(), "recipes": {k: {"label": v["label"], "help": v["help"], "kind": v["kind"]} for k, v in sites.RECIPES.items()},
            "browser": bool(config.BROWSER_URL and config.BROWSER_API_KEY)}


@router.post("/sites")
def add_site(body: SiteIn):
    return sites.public(sites.create(body.name, body.url, body.mode, body.api_key, body.max_pages))


@router.get("/sites/{sid}")
def site(sid: int):
    s = sites.public(sites.get(sid))
    s["checks"] = sites.db.all("SELECT id, at, status, pages, records, digest FROM site_checks WHERE site_id = ? ORDER BY id DESC LIMIT 10", sid)
    s["watches"] = sites.db.all("SELECT * FROM site_watches WHERE site_id = ? ORDER BY id", sid)
    for w in s["watches"]:
        w["in_words"] = sites.describe_cron(w["cron"])
    return s


@router.delete("/sites/{sid}")
def delete_site(sid: int):
    return sites.delete(sid)


class KeyIn(BaseModel):
    api_key: str = Field(..., min_length=8)


@router.post("/sites/{sid}/key")
def site_key(sid: int, body: KeyIn):
    return sites.public(sites.set_key(sid, body.api_key.strip()))


@router.post("/sites/{sid}/sign-in")
async def site_sign_in(sid: int):
    return await sites.sign_in(sid)


@router.post("/sites/{sid}/check")
async def site_check(sid: int):
    return await sites.check(sid)


@router.get("/sites/{sid}/records")
def site_records(sid: int, collection: str = ""):
    return sites.records(sid, collection)


@router.get("/sites/{sid}/timeline")
def site_timeline(sid: int):
    return sites.timeline(sid)


@router.get("/sites/{sid}/calendar.ics")
def site_ics(sid: int):
    s = sites.get(sid)
    return PlainTextResponse(sites.ics(sid), media_type="text/calendar",
                             headers={"Content-Disposition": f'attachment; filename="{s["name"]}.ics"'})


@router.post("/sites/{sid}/to-calendar")
async def site_to_calendar(sid: int):
    return await sites.to_calendar(sid)


class SiteFileIn(BaseModel):
    url: str


@router.post("/sites/{sid}/fetch-file")
async def site_fetch_file(sid: int, body: SiteFileIn):
    return await sites.fetch_file(sid, body.url)


@router.post("/sites/{sid}/export")
def site_export(sid: int):
    s = sites.get(sid)
    by: dict[str, list[dict]] = {}
    for r in sites.records(sid):
        by.setdefault(r["collection"], []).append({"Title": r["title"], "Status": r["status"], "Start": r["start"], "Due": r["due"],
                                                   "End": r["finish"], "Who": r["person"], "Where": r["location"], "Amount": r["amount"],
                                                   "Overdue": "yes" if r["overdue"] else "", **r["fields"], "Link": r["url"]})
    return analysis.make_excel(f"{s['name']} records", by or {"Records": []})


class WatchIn(BaseModel):
    request: str = Field(..., min_length=3, max_length=500)
    site_id: int | None = None


@router.post("/sites/watches")
def add_watch(body: WatchIn):
    return sites.watch_from_text(body.request, body.site_id)


@router.post("/sites/watches/{wid}/run")
async def run_watch(wid: int):
    sites.ensure()  # integration/v0.2: a fresh install has no watches table yet
    w = sites.db.one("SELECT * FROM site_watches WHERE id = ?", wid)
    if not w:
        raise HTTPException(404, "No such watch.")
    return {"result": await sites.run_watch(w)}


@router.delete("/sites/watches/{wid}")
def delete_watch(wid: int):
    sites.ensure()  # integration/v0.2: deleting before any watch exists answered 500
    sites.db.run("DELETE FROM site_watches WHERE id = ?", wid)
    return {"ok": True}


class ProposeIn(BaseModel):
    form: str
    values: dict = {}


@router.post("/sites/{sid}/actions")
def propose(sid: int, body: ProposeIn):
    return sites.propose(sid, body.form, body.values)


@router.get("/site-actions")
def list_actions():
    return sites.actions()


class DecideIn(BaseModel):
    approve: bool


@router.post("/site-actions/{aid}")
async def decide(aid: int, body: DecideIn):
    return await sites.decide(aid, body.approve)
