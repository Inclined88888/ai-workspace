"""File uploads: stored under DATA_DIR/files, text extracted for search, chat and research.

Readable: plain text and code, Markdown, CSV, JSON, HTML, PDF, Word (.docx) and Excel (.xlsx, read by tables.py).
Other files (images, older spreadsheet formats, archives) are stored and can be downloaded, but have no text. Uploaded content is treated as outside content by the
assistant: it may contain planted instructions, so persistent actions after reading it need approval.
"""

import hashlib
import io
import re
import secrets
import zipfile
from html import unescape
from pathlib import Path

from fastapi import HTTPException

from . import config, db

MAX_BYTES = 25 * 1024 * 1024
MAX_TEXT = 500_000
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".yaml", ".yml", ".xml", ".ini", ".toml",
            ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".sql", ".sh", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".rb", ".php"}
ATTACH_MARK = "[Attached file:"


def root() -> Path:
    d = config.DATA_DIR / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def clean_name(name: str) -> str:
    name = Path(name or "file").name  # no directories
    name = re.sub(r"[\x00-\x1f/\\]", "", name).strip()[:180]
    return name or "file"


def _docx_text(data: bytes) -> str:
    # Regex over document.xml rather than an XML parser: nothing in an uploaded file gets expanded or resolved
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    return "\n".join(unescape("".join(re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", p, re.S))) for p in paras).strip()


def extract(name: str, mime: str, data: bytes) -> tuple[str, str]:
    """(text, note). note explains an empty text."""
    ext = Path(name).suffix.lower()
    try:
        if ext == ".pdf" or mime == "application/pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                return "", "This PDF is password protected."
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages[:300]).strip()
            return text, "" if text else "No text found: this PDF may be scanned images (no OCR yet)."
        if ext == ".docx":
            return _docx_text(data), ""
        if ext == ".xlsx":
            from .tables import text_of

            return text_of(data), ""
        if ext in (".html", ".htm") or mime == "text/html":
            import trafilatura

            return (trafilatura.extract(data.decode("utf-8", "replace"), include_tables=True) or "").strip(), ""
        if ext in TEXT_EXT or mime.startswith("text/") or mime == "application/json":
            return data.decode("utf-8", "replace"), ""
    except Exception as e:  # noqa: BLE001 - a damaged file is stored anyway, with a reason
        return "", f"Couldn't read the text ({e.__class__.__name__})."
    if mime.startswith("image/"):
        return "", "Images are stored, but ROOKS can't read pictures yet."
    return "", "Stored. ROOKS can't read text from this file type yet."


def save(name: str, mime: str, data: bytes) -> dict:
    if not data:
        raise HTTPException(400, "The file is empty.")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"Files can be up to {MAX_BYTES // (1024 * 1024)} MB.")
    name = clean_name(name)
    digest = hashlib.sha256(data).hexdigest()
    same = db.one("SELECT id FROM files WHERE sha256 = ? AND name = ?", digest, name)
    if same:  # the same file uploaded again (from chat and from Files, say): reuse it
        return meta(same["id"])
    text, note = extract(name, mime, data)
    stored = f"{secrets.token_hex(12)}{Path(name).suffix.lower()[:10]}"
    (root() / stored).write_bytes(data)
    fid = db.run("INSERT INTO files(name, mime, size, path, text, note, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 name, mime[:100], len(data), stored, text[:MAX_TEXT], note, digest, db.now())
    return meta(fid)


def meta(fid: int) -> dict:
    f = db.one("SELECT id, name, mime, size, note, created_at, length(text) AS chars FROM files WHERE id = ?", fid)
    if not f:
        raise HTTPException(404, "File not found")
    return f


def get_text(fid: int, start: int = 0, length: int = 12000) -> dict:
    f = db.one("SELECT id, name, text, note FROM files WHERE id = ?", fid)
    if not f:
        raise HTTPException(404, "File not found")
    start = max(0, start)
    chunk = f["text"][start:start + length]
    return {"file_id": f["id"], "name": f["name"], "text": chunk, "start": start, "total_chars": len(f["text"]),
            "more": start + length < len(f["text"]), "note": f["note"]}


def delete(fid: int) -> None:
    f = db.one("SELECT path FROM files WHERE id = ?", fid)
    if not f:
        raise HTTPException(404, "File not found")
    (root() / f["path"]).unlink(missing_ok=True)
    db.run("DELETE FROM files WHERE id = ?", fid)


def attachment_block(fid: int, limit: int = 12000) -> str:
    """What the assistant sees for a file attached to a message. Longer files continue via the read_file tool."""
    t = get_text(fid, 0, limit)
    if not t["text"]:
        return f"{ATTACH_MARK} {t['name']} (file #{fid}). No readable text: {t['note']}]"
    more = f"\n[Showing {len(t['text'])} of {t['total_chars']} characters. Use read_file with file_id {fid} and start={len(t['text'])} for more.]" if t["more"] else ""
    return f"{ATTACH_MARK} {t['name']} (file #{fid})]\n{t['text']}{more}\n[End of file {t['name']}]"


def search(query: str, limit: int = 10) -> list[dict]:
    from .assistant import _any_words

    q = _any_words(query)
    if not q:
        return []
    return db.all("SELECT f.id, f.name, f.size, f.created_at, snippet(files_fts, 1, '', '', '…', 16) AS excerpt FROM files f "
                  "JOIN files_fts ON files_fts.rowid = f.id WHERE files_fts MATCH ? ORDER BY bm25(files_fts) LIMIT ?", q, limit)
