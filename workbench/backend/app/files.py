"""Read any file: text, tables, structure and metadata, with a citation for every passage.

One entry point, extract(path), returns the same shape for every kind of file:

    {"name", "kind", "size", "meta": {...}, "chunks": [{"loc", "text"}], "tables": [{"name", "loc", "columns", "rows",
     "n_rows"}], "images": [paths of rendered pages or embedded pictures], "children": [extracts of files inside],
     "notes": [plain-English sentences about anything that could not be read, and why]}

`loc` is a human citation: "page 3, paragraph 2", "Sheet1!A1:F40", "slide 4", "lines 120-180", "cell 7". Everything runs
on this computer. Only the text the user asks about is sent to their chosen model, by the assistant, not by this module.

Specific readers come first; anything else gets a best-effort path: LibreOffice (when installed) converts old or
unusual office formats, printable text is pulled out of unknown files, and the note says plainly what was missed.
Optional readers live in requirements-files.txt; when one is missing the note names the add-on instead of failing.
"""

from __future__ import annotations

import csv
import email
import email.policy
import io
import json
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MAX_CHARS = 2_000_000           # text kept per file (the assistant reads it in chunks)
CHUNK_CHARS = 1800              # one citation-sized passage
PREVIEW_ROWS = 200              # table rows kept in an extract (stats always cover every row)
ZIP_MAX_FILES = 400
ZIP_MAX_BYTES = 600 * 1024 * 1024
ZIP_MAX_DEPTH = 3
BIG_CSV_BYTES = 40 * 1024 * 1024

TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".log", ".ini", ".cfg", ".conf", ".toml", ".yaml", ".yml", ".env.example",
            ".srt", ".vtt", ".org", ".adoc", ".bib", ".gcode", ".nc", ".tex", ".sty", ".cls"}
CODE_EXT = {".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript (React)",
            ".jsx": "JavaScript (React)", ".java": "Java", ".kt": "Kotlin", ".c": "C", ".h": "C header", ".cpp": "C++",
            ".hpp": "C++ header", ".cs": "C#", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
            ".m": "MATLAB / Objective-C", ".r": "R", ".jl": "Julia", ".scala": "Scala", ".sql": "SQL", ".sh": "Shell",
            ".ps1": "PowerShell", ".bat": "Batch", ".lua": "Lua", ".pl": "Perl", ".f90": "Fortran", ".f": "Fortran",
            ".vb": "Visual Basic", ".dart": "Dart", ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue",
            ".svelte": "Svelte", ".ipynb": "Jupyter notebook", ".lisp": "Lisp", ".hs": "Haskell", ".ex": "Elixir",
            ".erl": "Erlang", ".clj": "Clojure", ".groovy": "Groovy", ".tf": "Terraform", ".dockerfile": "Dockerfile",
            ".cmake": "CMake", ".gradle": "Gradle", ".vhd": "VHDL", ".v": "Verilog", ".sv": "SystemVerilog", ".asm": "Assembly"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".ico", ".psd"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".wmv", ".mpg", ".mpeg"}
OFFICE_CONVERT = {".doc": "docx", ".rtf": "docx", ".odt": "docx", ".wpd": "docx", ".pages": "docx", ".wps": "docx",
                  ".ppt": "pptx", ".odp": "pptx", ".key": "pptx", ".ods": "xlsx", ".numbers": "xlsx", ".xlsb": "xlsx",
                  ".vsd": "pdf", ".vsdx": "pdf", ".pub": "pdf", ".xps": "pdf"}
LEGAL_HINT = re.compile(r"\b(agreement|contract|hereby|whereas|indemnif|liabilit|governing law|termination|clause|"
                        r"party|parties|act \d{4}|section \d+|plaintiff|defendant|court|tribunal|statute|regulation)\b", re.I)
MATH_HINT = re.compile(r"(\$[^$]+\$|\\begin\{(equation|align)|\\frac|\\int|\\sum|=\s*\d)")


class Out:
    def __init__(self, path: Path, name: str | None = None):
        self.path = path
        self.d: dict[str, Any] = {"name": name or path.name, "kind": "", "size": path.stat().st_size if path.exists() else 0,
                                  "meta": {}, "chunks": [], "tables": [], "images": [], "children": [], "notes": []}
        self._chars = 0

    def kind(self, k: str) -> "Out":
        self.d["kind"] = k
        return self

    def meta(self, **kw) -> None:
        self.d["meta"].update({k: v for k, v in kw.items() if v not in (None, "", [], {})})

    def note(self, s: str) -> None:
        if s not in self.d["notes"]:
            self.d["notes"].append(s)

    def text(self, loc: str, text: str) -> None:
        """Add a passage, split into citation-sized chunks that keep the same location."""
        text = (text or "").strip()
        if not text:
            return
        if self._chars >= MAX_CHARS:
            self.note(f"Only the first {MAX_CHARS // 1_000_000} million characters were kept; ask about a part to read it.")
            return
        text = text[: MAX_CHARS - self._chars]
        self._chars += len(text)
        if len(text) <= CHUNK_CHARS:
            self.d["chunks"].append({"loc": loc, "text": text})
            return
        parts, buf = [], ""
        for para in re.split(r"(\n\s*\n)", text):
            if len(buf) + len(para) > CHUNK_CHARS and buf:
                parts.append(buf)
                buf = ""
            while len(para) > CHUNK_CHARS:
                parts.append(para[:CHUNK_CHARS])
                para = para[CHUNK_CHARS:]
            buf += para
        if buf.strip():
            parts.append(buf)
        for i, p in enumerate(parts, 1):
            self.d["chunks"].append({"loc": f"{loc} (part {i})" if len(parts) > 1 else loc, "text": p.strip()})

    def table(self, name: str, loc: str, columns: list, rows: list[list], n_rows: int | None = None, **extra) -> None:
        cols = [str(c) if c not in (None, "") else f"Column {i + 1}" for i, c in enumerate(columns)]
        self.d["tables"].append({"name": name, "loc": loc, "columns": cols, "rows": [[_cell(v) for v in r] for r in rows[:PREVIEW_ROWS]],
                                 "n_rows": len(rows) if n_rows is None else n_rows, **extra})


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def _need(module: str, what: str, out: Out):
    try:
        return __import__(module, fromlist=["_"])
    except Exception:  # noqa: BLE001 - optional reader missing
        out.note(f"Reading {what} needs the files add-on (pip install -r requirements-files.txt); it is not installed here.")
        return None


def sniff(path: Path) -> str:
    """The file's real type from its first bytes, so a wrongly named file is still read correctly."""
    try:
        with path.open("rb") as f:
            head = f.read(4096)
    except OSError:
        return ""
    sigs = [(b"%PDF", ".pdf"), (b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF8", ".gif"), (b"\x89HDF\r\n\x1a\n", ".h5"),
            (b"CDF\x01", ".nc3"), (b"CDF\x02", ".nc3"), (b"SIMPLE  =", ".fits"), (b"PAR1", ".parquet"), (b"8BPS", ".psd"),
            (b"\xd0\xcf\x11\xe0", ".ole"), (b"ID3", ".mp3"), (b"fLaC", ".flac"), (b"OggS", ".ogg"), (b"AC10", ".dwg"),
            (b"RIFF", ".riff"), (b"{\\rtf", ".rtf"), (b"BM", ".bmp")]
    for sig, ext in sigs:
        if head.startswith(sig):
            return ext
    if head[4:8] == b"ftyp":
        return ".mp4"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile:
            return ".zip"
        if "word/document.xml" in names:
            return ".docx"
        if "xl/workbook.xml" in names:
            return ".xlsx"
        if "ppt/presentation.xml" in names:
            return ".pptx"
        if "content.xml" in names and "mimetype" in names:
            return ".odf"
        return ".zip"
    return ""


def extract(path: str | Path, name: str | None = None, depth: int = 0) -> dict:
    """Read one file (or folder). Never raises for a bad file: problems become plain-English notes."""
    path = Path(path)
    out = Out(path, name)
    if path.is_dir():
        return _folder(path, out, depth)
    if not path.exists():
        out.kind("missing").note("The file is not there any more.")
        return out.d
    ext = "".join(path.suffixes[-1:]).lower() if path.suffix else ""
    real = sniff(path)
    if real in (".pdf", ".docx", ".xlsx", ".pptx", ".h5", ".fits", ".parquet", ".nc3", ".dwg", ".psd") and ext != real:
        if not (real == ".h5" and ext in (".nc", ".nc4", ".hdf5", ".hdf", ".he5", ".mat")) and not (real == ".zip"):
            ext = real if ext not in (".xlsm", ".xltx", ".docm", ".dotx", ".pptm", ".potx") else ext
    try:
        _dispatch(path, ext, real, out, depth)
    except Exception as e:  # noqa: BLE001 - a damaged file must not break the batch
        out.note(f"Could not finish reading this file: {str(e)[:200] or e.__class__.__name__}. What was read before the problem is shown.")
    _hints(out)
    return out.d


def _dispatch(path: Path, ext: str, real: str, out: Out, depth: int) -> None:
    if ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        return _xlsx(path, out)
    if ext == ".xls" or (real == ".ole" and ext == ".xls"):
        return _xls(path, out)
    if ext in (".csv", ".tsv", ".tab"):
        return _csv(path, out)
    if ext in (".docx", ".docm", ".dotx"):
        return _docx(path, out)
    if ext in (".pdf", ".ai"):
        return _pdf(path, out)
    if ext in (".pptx", ".pptm", ".potx"):
        return _pptx(path, out)
    if ext in (".json", ".geojson", ".jsonl", ".ndjson"):
        return _json(path, out, lines=ext in (".jsonl", ".ndjson"))
    if ext in (".xml", ".kml", ".gpx", ".xsd", ".rss", ".atom", ".svg", ".plist", ".xaml", ".resx", ".drawio"):
        return _xml(path, out, svg=ext == ".svg")
    if ext == ".ipynb":
        return _notebook(path, out)
    if ext in (".tex", ".ltx", ".latex"):
        return _latex(path, out)
    if ext == ".eml":
        return _eml(path, out, depth)
    if ext == ".msg":
        return _msg(path, out, depth)
    if ext == ".dxf":
        return _dxf(path, out)
    if ext == ".dwg" or real == ".dwg":
        return _dwg(path, out)
    if ext == ".parquet" or real == ".parquet":
        return _parquet(path, out)
    if ext in (".h5", ".hdf5", ".hdf", ".he5", ".nc", ".nc4", ".cdf") or real == ".h5":
        if real == ".nc3":
            return _netcdf3(path, out)
        if real == ".h5":
            return _hdf5(path, out)
    if real == ".nc3":
        return _netcdf3(path, out)
    if ext in (".fits", ".fit", ".fts") or real == ".fits":
        return _fits(path, out)
    if ext in IMAGE_EXT or real in (".png", ".jpg", ".gif", ".bmp", ".psd"):
        return _image(path, out)
    if ext in AUDIO_EXT or ext in VIDEO_EXT or real in (".mp3", ".flac", ".ogg", ".mp4"):
        return _media(path, out, video=ext in VIDEO_EXT or real == ".mp4")
    if ext in (".zip", ".jar", ".sketch", ".fig", ".xd", ".kmz", ".epub", ".3mf", ".twbx", ".idml") or real == ".zip":
        return _zip(path, out, depth)
    if real == ".odf" or ext in (".odt", ".ods", ".odp"):
        if _via_office(path, ext or ".odt", out, depth):
            return
        return _odf(path, out)
    if ext in OFFICE_CONVERT or real in (".ole", ".rtf"):
        if _via_office(path, ext if ext in OFFICE_CONVERT else ".doc", out, depth):
            return
        if real == ".rtf" or ext == ".rtf":
            return _rtf(path, out)
        if real == ".ole" and ext in (".doc", ""):
            out.kind("Word 97-2003 document")
        return _strings(path, out, "LibreOffice is not installed, so this older office file was read as plain text only; "
                                   "formatting and tables were lost. Install LibreOffice (free) or save it as a newer format.")
    if ext in CODE_EXT:
        return _code(path, out, CODE_EXT[ext])
    if ext in TEXT_EXT or ext in (".htm",) or _is_text(path):
        if ext in (".html", ".htm"):
            return _code(path, out, "HTML")
        return _text(path, out)
    return _strings(path, out, f"This kind of file ({ext or 'no extension'}) has no dedicated reader. Readable text inside it "
                               "is shown; anything stored in a binary format was not read.")


# --- helpers ---------------------------------------------------------------------------------------------------------
def _read_text(path: Path, limit: int = MAX_CHARS) -> str:
    raw = path.read_bytes()[: limit * 2]
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)[:limit]
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")[:limit]


def _is_text(path: Path) -> bool:
    try:
        head = path.open("rb").read(8192)
    except OSError:
        return False
    if not head:
        return True
    if b"\x00" in head and not head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return False
    printable = sum(1 for b in head if b in (9, 10, 13) or 32 <= b < 127 or b >= 128)
    return printable / len(head) > 0.95


def _lines_chunks(out: Out, text: str, every: int = 60) -> None:
    lines = text.splitlines()
    for i in range(0, len(lines), every):
        out.text(f"lines {i + 1}-{min(i + every, len(lines))}", "\n".join(lines[i : i + every]))


def _hints(out: Out) -> None:
    """Plain cues for the assistant about how to answer; generic, from the content, not a mode the user picks."""
    sample = " ".join(c["text"] for c in out.d["chunks"][:30])[:60000]
    hints = []
    if len(LEGAL_HINT.findall(sample)) >= 6:
        hints.append("Looks like a legal or contractual text: quote the exact passage with its location for every point, flag "
                     "risks and unusual clauses, and end with: \"This is general information, not legal advice.\"")
    if len(MATH_HINT.findall(sample)) >= 3 or out.d["meta"].get("equations"):
        hints.append("Contains mathematics: show working step by step, write equations in LaTeX ($...$ or $$...$$), and check "
                     "results with run_python (sympy) when possible.")
    if out.d["tables"]:
        hints.append("Has tables: use analyse_spreadsheet or run_python for numbers instead of estimating.")
    if hints:
        out.d["meta"]["how_to_answer"] = hints


def _folder(path: Path, out: Out, depth: int) -> dict:
    out.kind("folder")
    files = [p for p in sorted(path.rglob("*")) if p.is_file() and not any(part.startswith(".") for part in p.relative_to(path).parts)]
    out.meta(files=len(files))
    out.text("folder listing", "\n".join(str(p.relative_to(path)) for p in files[:2000]))
    for p in files[:ZIP_MAX_FILES]:
        out.d["children"].append(extract(p, str(p.relative_to(path)), depth + 1))
    if len(files) > ZIP_MAX_FILES:
        out.note(f"Only the first {ZIP_MAX_FILES} of {len(files)} files were read.")
    return out.d


# --- spreadsheets and tables -------------------------------------------------------------------------------------------
def _xlsx(path: Path, out: Out) -> None:
    out.kind("Excel workbook")
    openpyxl = _need("openpyxl", "Excel files", out)
    if not openpyxl:
        return _strings(path, out, "")
    wb_f = openpyxl.load_workbook(path, read_only=True, data_only=False)
    wb_v = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets = []
    for ws_f, ws_v in zip(wb_f.worksheets, wb_v.worksheets):
        rows, formulas, n = [], [], 0
        for r_f, r_v in zip(ws_f.iter_rows(), ws_v.iter_rows(values_only=True)):
            n += 1
            if len(rows) < PREVIEW_ROWS + 1:
                rows.append(list(r_v))
            for c in r_f:
                v = getattr(c, "value", None)
                if isinstance(v, str) and v.startswith("=") and len(formulas) < 200:
                    formulas.append({"cell": f"{ws_f.title}!{c.coordinate}", "formula": v})
        while rows and all(v in (None, "") for v in rows[-1]):
            rows.pop()
        width = max((len(r) for r in rows), default=0)
        sheets.append(ws_f.title)
        if not rows:
            out.note(f"Sheet '{ws_f.title}' is empty.")
            continue
        header, body = _header(rows)
        dims = f"{ws_f.title}!A1:{_col(width)}{n}"
        out.table(ws_f.title, dims, header, body, n_rows=max(n - 1, 0), formulas=formulas[:50], n_formulas=len(formulas))
        out.text(f"sheet {ws_f.title}", _rows_text(header, body[:60]))
    out.meta(sheets=sheets)
    if _blank_cached(out):
        out.note("Formula results are the values Excel last saved; this file was made by a program that never "
                 "calculated them, so some results are blank. run_python can recalculate them.")


def _blank_cached(out: Out) -> bool:
    return any(t.get("n_formulas") and t["rows"] and all(v == "" for r in t["rows"][:5] for v in r[-1:]) for t in out.d["tables"])


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s or "A"


def _header(rows: list[list]) -> tuple[list, list[list]]:
    """First mostly-text row is the header; otherwise columns are named Column 1, 2..."""
    for i, r in enumerate(rows[:5]):
        filled = [v for v in r if v not in (None, "")]
        if len(filled) >= max(1, len(r) // 2) and all(isinstance(v, str) for v in filled):
            return list(r), rows[i + 1 :]
    width = max(len(r) for r in rows)
    return [f"Column {i + 1}" for i in range(width)], rows


def _rows_text(header: list, rows: list[list]) -> str:
    lines = [" | ".join(str(h) for h in header)]
    lines += [" | ".join("" if v is None else str(v) for v in r) for r in rows]
    return "\n".join(lines)


def _xls(path: Path, out: Out) -> None:
    out.kind("Excel 97-2003 workbook")
    xlrd = _need("xlrd", "old Excel (.xls) files", out)
    if not xlrd:
        return
    book = xlrd.open_workbook(str(path))
    for sh in book.sheets():
        rows = [sh.row_values(i) for i in range(min(sh.nrows, PREVIEW_ROWS + 1))]
        if not rows:
            continue
        header, body = _header(rows)
        out.table(sh.name, f"{sh.name}!A1:{_col(sh.ncols)}{sh.nrows}", header, body, n_rows=max(sh.nrows - 1, 0))
        out.text(f"sheet {sh.name}", _rows_text(header, body[:60]))
    out.meta(sheets=book.sheet_names())
    out.note("Formulas are not kept in old .xls files by the reader; values are shown.")


def _csv(path: Path, out: Out) -> None:
    out.kind("CSV table")
    sample = _read_text(path, 64000)
    try:
        dialect = csv.Sniffer().sniff(sample[:20000], delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = "\t" if path.suffix.lower() in (".tsv", ".tab") else ","
    big = path.stat().st_size > BIG_CSV_BYTES
    rows: list[list] = []
    n = 0
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        for r in csv.reader(f, delimiter=delim):
            n += 1
            if len(rows) <= PREVIEW_ROWS:
                rows.append(r)
            elif big:
                continue
    if not rows:
        out.note("The file is empty.")
        return
    header, body = _header(rows)
    out.table(path.stem, f"rows 1-{n}", header, body, n_rows=max(n - 1, 0), delimiter=delim)
    out.text("first rows", _rows_text(header, body[:60]))
    out.meta(rows=n - 1, columns=len(header), delimiter={",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(delim, delim))
    if big:
        out.note(f"Large file ({path.stat().st_size // 1_000_000} MB, {n:,} rows): the preview shows the first {PREVIEW_ROWS} "
                 "rows; statistics and charts (analyse_spreadsheet, run_python) read every row in chunks.")


def _parquet(path: Path, out: Out) -> None:
    out.kind("Parquet table")
    pq = _need("pyarrow.parquet", "Parquet files", out)
    if not pq:
        return
    f = pq.ParquetFile(path)
    schema = f.schema_arrow
    first = next(f.iter_batches(batch_size=PREVIEW_ROWS), None)
    rows = first.to_pylist() if first is not None else []
    cols = schema.names
    out.table(path.stem, f"{f.metadata.num_rows:,} rows", cols, [[r.get(c) for c in cols] for r in rows], n_rows=f.metadata.num_rows)
    out.meta(rows=f.metadata.num_rows, columns=len(cols), row_groups=f.metadata.num_row_groups,
             schema={n: str(t) for n, t in zip(schema.names, schema.types)})
    out.text("schema", "\n".join(f"{n}: {t}" for n, t in zip(schema.names, schema.types)))


# --- documents ---------------------------------------------------------------------------------------------------------
def _docx(path: Path, out: Out) -> None:
    out.kind("Word document")
    docx = _need("docx", "Word documents", out)
    if not docx:
        return _odf(path, out)
    d = docx.Document(str(path))
    heading, para_no, buf, buf_start = "", 0, [], 1
    sections = []

    def flush():
        nonlocal buf, buf_start
        if buf:
            where = f"{heading}, " if heading else ""
            out.text(f"{where}paragraphs {buf_start}-{para_no}" if para_no > buf_start else f"{where}paragraph {buf_start}", "\n\n".join(buf))
        buf = []

    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        para_no += 1
        style = (p.style.name if p.style is not None else "") or ""
        if style.lower().startswith(("heading", "title")):
            flush()
            heading = t[:80]
            sections.append(t[:120])
            buf_start = para_no
        if not buf:
            buf_start = para_no
        buf.append(f"¶{para_no} {t}")
        if sum(len(x) for x in buf) > CHUNK_CHARS:
            flush()
    flush()
    for i, t in enumerate(d.tables, 1):
        rows = [[c.text.strip() for c in r.cells] for r in t.rows]
        if rows:
            header, body = _header(rows)
            out.table(f"Table {i}", f"table {i}", header, body)
            out.text(f"table {i}", _rows_text(header, body))
    cp = d.core_properties
    out.meta(paragraphs=para_no, headings=sections[:60], tables=len(d.tables), author=cp.author, title=cp.title,
             modified=str(cp.modified or ""), revision=cp.revision)
    # Tracked changes and comments live in the XML; count them so the user knows they exist
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
        ins, dels = xml.count("<w:ins "), xml.count("<w:del ")
        if ins or dels:
            out.meta(tracked_changes={"insertions": ins, "deletions": dels})
            out.note(f"The document has tracked changes ({ins} insertions, {dels} deletions); the text shown includes insertions.")
        if "word/comments.xml" in z.namelist():
            comments = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", z.read("word/comments.xml").decode("utf-8", "replace"))
            if comments:
                out.text("comments", " ".join(comments))
        for n in z.namelist():
            if n.startswith("word/media/") and len(out.d["images"]) < 10:
                out.d["meta"].setdefault("embedded_images", []).append(n.split("/")[-1])


def _pdf(path: Path, out: Out) -> None:
    out.kind("PDF document")
    pdfplumber = _need("pdfplumber", "PDF files", out)
    pages_without_text = []
    if pdfplumber:
        with pdfplumber.open(str(path)) as pdf:
            out.meta(pages=len(pdf.pages), **{k.lower(): str(v)[:200] for k, v in (pdf.metadata or {}).items()
                                              if k in ("Title", "Author", "Subject", "Creator", "CreationDate", "ModDate")})
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                if text.strip():
                    paras = [p for p in re.split(r"\n\s*\n|(?<=[.:;])\n(?=[A-Z0-9(])", text) if p.strip()]
                    for j, p in enumerate(paras, 1):
                        out.text(f"page {i}, paragraph {j}", p)
                else:
                    pages_without_text.append(i)
                try:
                    for k, t in enumerate(page.extract_tables() or [], 1):
                        if t and len(t) > 1:
                            header, body = _header(t)
                            out.table(f"Page {i} table {k}", f"page {i}, table {k}", header, body)
                except Exception:  # noqa: BLE001 - tables are a bonus
                    pass
                if i >= 2000:
                    out.note("Only the first 2000 pages were read.")
                    break
    else:
        pypdf = _need("pypdf", "PDF files", out)
        if not pypdf:
            return
        r = pypdf.PdfReader(str(path))
        out.meta(pages=len(r.pages))
        for i, page in enumerate(r.pages, 1):
            t = page.extract_text() or ""
            if t.strip():
                out.text(f"page {i}", t)
            else:
                pages_without_text.append(i)
    if pages_without_text:
        _ocr_pages(path, pages_without_text, out)


def _ocr_pages(path: Path, pages: list[int], out: Out) -> None:
    """Scanned pages: Tesseract when installed, otherwise rendered pictures for a vision model to read."""
    pdfium = _need("pypdfium2", "scanned PDF pages", out)
    if not pdfium:
        out.note(f"Pages {_ranges(pages)} have no text layer (scanned) and could not be rendered.")
        return
    doc = pdfium.PdfDocument(str(path))
    tess = shutil.which("tesseract")
    tmp = Path(tempfile.mkdtemp(prefix="wb-ocr-"))
    read = []
    for i in pages[:40]:
        img = doc[i - 1].render(scale=2).to_pil()
        p = tmp / f"page-{i}.png"
        img.save(p)
        if tess:
            r = subprocess.run([tess, str(p), "-", "--psm", "3"], capture_output=True, text=True, timeout=120)
            if r.stdout.strip():
                out.text(f"page {i} (OCR)", r.stdout)
                read.append(i)
                continue
        out.d["images"].append(str(p))
    rest = [i for i in pages if i not in read]
    if rest:
        out.note(f"Pages {_ranges(rest)} are scanned images with no text layer. "
                 + ("They were rendered as pictures for a vision-capable model to read (describe_image)." if out.d["images"] else "")
                 + ("" if tess else " Installing Tesseract (free) lets them be read on this computer."))


def _ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev != start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if prev != start else str(start))
    return ", ".join(out)


def _pptx(path: Path, out: Out) -> None:
    out.kind("PowerPoint presentation")
    pptx = _need("pptx", "PowerPoint files", out)
    if not pptx:
        return _odf(path, out)
    prs = pptx.Presentation(str(path))
    titles = []
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        for sh in slide.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                texts.append(sh.text_frame.text.strip())
            if getattr(sh, "has_table", False) and sh.has_table:
                rows = [[c.text for c in r.cells] for r in sh.table.rows]
                header, body = _header(rows)
                out.table(f"Slide {i} table", f"slide {i}", header, body)
        if slide.shapes.title is not None and slide.shapes.title.text:
            titles.append(slide.shapes.title.text.strip())
        notes = slide.notes_slide.notes_text_frame.text.strip() if slide.has_notes_slide and slide.notes_slide.notes_text_frame else ""
        out.text(f"slide {i}", "\n".join(texts) + (f"\n[Speaker notes] {notes}" if notes else ""))
    out.meta(slides=len(prs.slides), titles=titles[:80])


def _odf(path: Path, out: Out) -> None:
    out.kind(out.d["kind"] or "OpenDocument file")
    try:
        with zipfile.ZipFile(path) as z:
            name = "content.xml" if "content.xml" in z.namelist() else next((n for n in z.namelist() if n.endswith(".xml")), "")
            xml = z.read(name).decode("utf-8", "replace") if name else ""
    except zipfile.BadZipFile:
        return _strings(path, out, "")
    text = re.sub(r"<(text:p|text:h|table:table-row)[^>]*>", "\n", xml)
    text = re.sub(r"<[^>]+>", " ", text)
    out.text("document", re.sub(r"[ \t]+", " ", text))
    out.note("Read as plain text (formatting and tables simplified).")


def _rtf(path: Path, out: Out) -> None:
    out.kind("Rich Text document")
    raw = _read_text(path)
    text = re.sub(r"\\par[d]?", "\n", raw)
    text = re.sub(r"\{\\\*[^{}]*\}|\\[a-z]+-?\d* ?|[{}]", "", text)
    out.text("document", text)
    out.note("Read as plain text; install LibreOffice for tables and layout.")


def _via_office(path: Path, ext: str, out: Out, depth: int) -> bool:
    """Convert with LibreOffice when it is installed (free), then read the converted file."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice") or next(
        (p for p in ("/Applications/LibreOffice.app/Contents/MacOS/soffice", r"C:\Program Files\LibreOffice\program\soffice.exe")
         if Path(p).exists()), None)
    target = OFFICE_CONVERT.get(ext, "docx" if ext in (".odt",) else "xlsx" if ext == ".ods" else "pptx" if ext == ".odp" else "pdf")
    if not soffice:
        return False
    tmp = Path(tempfile.mkdtemp(prefix="wb-office-"))
    try:
        subprocess.run([soffice, "--headless", "--convert-to", target, "--outdir", str(tmp), str(path)],
                       capture_output=True, timeout=180, env={**os.environ, "HOME": str(tmp)})
        conv = next(tmp.glob(f"*.{target}"), None)
        if not conv:
            return False
        inner = extract(conv, out.d["name"], depth)
        out.d.update({k: inner[k] for k in ("chunks", "tables", "images", "meta")})
        out.kind(f"{ext.lstrip('.').upper()} file (converted with LibreOffice)")
        out.d["notes"] += inner["notes"]
        return True
    except (subprocess.SubprocessError, OSError):
        return False


# --- structured text, code, science ------------------------------------------------------------------------------------
def _text(path: Path, out: Out) -> None:
    out.kind("Text file")
    text = _read_text(path)
    if path.suffix.lower() in (".md", ".markdown", ".rst", ".adoc", ".org"):
        out.kind("Markdown document")
        sections = re.split(r"(?m)^(?=#{1,6} )", text)
        for i, s in enumerate(sections, 1):
            head = s.splitlines()[0].lstrip("# ").strip() if s.strip() else ""
            out.text(f"section '{head[:60]}'" if s.startswith("#") else f"part {i}", s)
    else:
        _lines_chunks(out, text)
    out.meta(lines=text.count("\n") + 1, characters=len(text))


def _code(path: Path, out: Out, lang: str) -> None:
    out.kind(f"{lang} source code")
    text = _read_text(path)
    defs = re.findall(r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|func|fn|public|private|protected|static|interface|struct|impl|module)\s+([A-Za-z_][\w]*)", text)
    imports = re.findall(r"(?m)^\s*(?:import|from|#include|using|require\(|use)\s+([\w./<>\"'@:-]+)", text)
    _lines_chunks(out, text, 80)
    out.meta(language=lang, lines=text.count("\n") + 1, definitions=list(dict.fromkeys(defs))[:150], imports=list(dict.fromkeys(imports))[:80])


def _json(path: Path, out: Out, lines: bool = False) -> None:
    out.kind("JSON data" if not lines else "JSON Lines data")
    text = _read_text(path)
    try:
        data = [json.loads(line) for line in text.splitlines() if line.strip()] if lines else json.loads(text)
    except ValueError as e:
        out.note(f"Not valid JSON ({e}); shown as text.")
        return _lines_chunks(out, text)
    out.meta(structure=_shape(data))
    records = _records(data)
    if records:
        cols = list(dict.fromkeys(k for r in records[:500] for k in r))[:60]
        out.table(path.stem, "records", cols, [[_flat(r.get(c)) for c in cols] for r in records], n_rows=len(records))
    _lines_chunks(out, json.dumps(data, indent=1, ensure_ascii=False)[:MAX_CHARS] if not lines else text, 80)


def _records(data) -> list[dict]:
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data[:50]):
        return data
    if isinstance(data, dict):
        for k in ("features", "data", "items", "results", "records", "rows", "value"):
            v = data.get(k)
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v[:50]):
                return [x.get("properties", x) if k == "features" else x for x in v]
    return []


def _flat(v):
    return json.dumps(v, ensure_ascii=False)[:300] if isinstance(v, (dict, list)) else v


def _shape(v, depth: int = 0):
    if depth > 4:
        return "…"
    if isinstance(v, dict):
        return {k: _shape(x, depth + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, list):
        return [f"{len(v)} items", _shape(v[0], depth + 1)] if v else []
    return type(v).__name__


def _xml(path: Path, out: Out, svg: bool = False) -> None:
    from xml.etree import ElementTree as ET

    out.kind("SVG drawing" if svg else "XML data")
    text = _read_text(path)
    try:
        # no external entities or DTD expansion: ElementTree does not fetch them, and the size is bounded above
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        out.note(f"Not well-formed XML ({e}); shown as text.")
        return _lines_chunks(out, text)
    tag = lambda e: e.tag.split("}")[-1]  # noqa: E731
    counts: dict[str, int] = {}
    for e in root.iter():
        counts[tag(e)] = counts.get(tag(e), 0) + 1
    out.meta(root=tag(root), elements=dict(sorted(counts.items(), key=lambda x: -x[1])[:30]))
    kids = list(root)
    groups: dict[str, list] = {}
    for k in kids:
        groups.setdefault(tag(k), []).append(k)
    for name, items in groups.items():
        if len(items) >= 3:
            recs = []
            for it in items:
                r = {**{f"@{a}": v for a, v in it.attrib.items()}}
                for c in it:
                    r[tag(c)] = (c.text or "").strip() or json.dumps(c.attrib)[:200]
                if not list(it) and (it.text or "").strip():
                    r["text"] = it.text.strip()
                recs.append(r)
            cols = list(dict.fromkeys(k for r in recs[:300] for k in r))[:50]
            out.table(name, f"<{name}> elements", cols, [[r.get(c, "") for c in cols] for r in recs], n_rows=len(recs))
    texts = [t.strip() for t in root.itertext() if t.strip()]
    out.text("text content", "\n".join(texts))


def _notebook(path: Path, out: Out) -> None:
    out.kind("Jupyter notebook")
    nb = json.loads(_read_text(path))
    cells = nb.get("cells", [])
    kinds = {"code": 0, "markdown": 0, "raw": 0}
    for i, c in enumerate(cells, 1):
        src = "".join(c.get("source", [])) if isinstance(c.get("source"), list) else str(c.get("source", ""))
        kinds[c.get("cell_type", "raw")] = kinds.get(c.get("cell_type", "raw"), 0) + 1
        outputs = []
        for o in c.get("outputs", []) or []:
            if "text" in o:
                outputs.append("".join(o["text"]) if isinstance(o["text"], list) else str(o["text"]))
            elif "data" in o and "text/plain" in o["data"]:
                v = o["data"]["text/plain"]
                outputs.append("".join(v) if isinstance(v, list) else str(v))
            elif o.get("output_type") == "error":
                outputs.append(f"{o.get('ename')}: {o.get('evalue')}")
        body = src + (("\n[Output]\n" + "\n".join(outputs)[:3000]) if outputs else "")
        out.text(f"cell {i} ({c.get('cell_type', '?')})", body)
    lang = ((nb.get("metadata") or {}).get("kernelspec") or {}).get("language") or ((nb.get("metadata") or {}).get("language_info") or {}).get("name")
    out.meta(cells=len(cells), code_cells=kinds.get("code"), markdown_cells=kinds.get("markdown"), language=lang)


def _latex(path: Path, out: Out) -> None:
    out.kind("LaTeX document")
    text = _read_text(path)
    sections = re.findall(r"\\(?:sub)*section\*?\{([^}]*)\}", text)
    eqs = re.findall(r"\\begin\{(equation|align|gather|multline)\*?\}(.*?)\\end\{\1\*?\}", text, re.S)
    eqs += [("display", m) for m in re.findall(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", text, re.S) for m in m if m]
    title = re.search(r"\\title\{([^}]*)\}", text)
    parts = re.split(r"(?=\\(?:sub)*section\*?\{)", text)
    for i, p in enumerate(parts, 1):
        head = re.match(r"\\(?:sub)*section\*?\{([^}]*)\}", p)
        out.text(f"section '{head.group(1)[:60]}'" if head else ("preamble" if i == 1 else f"part {i}"), p)
    out.meta(title=title.group(1) if title else "", sections=sections[:80], equations=len(eqs),
             equation_samples=[e[1].strip()[:200] for e in eqs[:10]],
             citations=len(re.findall(r"\\cite[pt]?\{", text)), figures=len(re.findall(r"\\begin\{figure", text)))


def _hdf5(path: Path, out: Out) -> None:
    is_nc = path.suffix.lower() in (".nc", ".nc4", ".cdf")
    out.kind("NetCDF-4 dataset" if is_nc else "HDF5 dataset")
    h5py = _need("h5py", "HDF5 and NetCDF-4 files", out)
    if not h5py:
        return
    np = _need("numpy", "numeric data", out)
    rows, lines = [], []
    with h5py.File(path, "r") as f:
        out.meta(attributes={k: _attr(v) for k, v in list(f.attrs.items())[:40]})

        def visit(name, obj):
            if isinstance(obj, h5py.Dataset):
                stats = ""
                try:
                    if obj.size and obj.size <= 5_000_000 and obj.dtype.kind in "iuf" and np is not None:
                        a = obj[()]
                        stats = f"min {np.nanmin(a):.6g}, max {np.nanmax(a):.6g}, mean {np.nanmean(a):.6g}"
                except Exception:  # noqa: BLE001
                    stats = ""
                attrs = {k: _attr(v) for k, v in list(obj.attrs.items())[:10] if not k.startswith("_Netcdf") and k not in ("DIMENSION_LIST", "REFERENCE_LIST")}
                rows.append([name, str(obj.shape), str(obj.dtype), attrs.get("units", ""), attrs.get("long_name", ""), stats])
                lines.append(f"{name}: shape {obj.shape}, {obj.dtype}, {json.dumps(attrs, default=str)[:300]} {stats}")
            elif len(rows) < 2000:
                lines.append(f"[group] {name}")
        f.visititems(visit)
    out.table("Datasets", "dataset index", ["path", "shape", "type", "units", "description", "statistics"], rows)
    out.text("structure", "\n".join(lines))
    out.meta(datasets=len(rows))


def _attr(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")[:300]
    try:
        return v.tolist() if hasattr(v, "tolist") else v
    except Exception:  # noqa: BLE001
        return str(v)[:300]


def _netcdf3(path: Path, out: Out) -> None:
    """Classic NetCDF (CDF-1/2): the header is a documented binary layout, read here without extra packages."""
    out.kind("NetCDF-3 dataset")
    b = path.read_bytes()[: 4 * 1024 * 1024]
    pos = [4]
    off64 = b[3] == 2

    def u32():
        v = struct.unpack(">I", b[pos[0] : pos[0] + 4])[0]
        pos[0] += 4
        return v

    def name():
        n = u32()
        s = b[pos[0] : pos[0] + n].decode("utf-8", "replace")
        pos[0] += (n + 3) // 4 * 4
        return s

    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 4, 6: 8}
    types = {1: "byte", 2: "char", 3: "short", 4: "int", 5: "float", 6: "double"}

    def attrs():
        tag, n = u32(), u32()
        res = {}
        for _ in range(n if tag == 12 else 0):
            k, t, cnt = name(), u32(), u32()
            raw = b[pos[0] : pos[0] + cnt * sizes.get(t, 1)]
            pos[0] += (cnt * sizes.get(t, 1) + 3) // 4 * 4
            if t == 2:
                res[k] = raw.decode("utf-8", "replace").rstrip("\x00")
            else:
                fmt = {1: "b", 3: "h", 4: "i", 5: "f", 6: "d"}[t]
                vals = struct.unpack(f">{cnt}{fmt}", raw)
                res[k] = vals[0] if cnt == 1 else list(vals)[:20]
        return res

    try:
        numrecs = u32()
        tag, n = u32(), u32()
        dims = []
        for _ in range(n if tag == 10 else 0):
            dims.append((name(), u32()))
        gatts = attrs()
        tag, n = u32(), u32()
        rows = []
        for _ in range(n if tag == 11 else 0):
            vname = name()
            nd = u32()
            ids = [u32() for _ in range(nd)]
            vatts = attrs()
            t = u32()
            u32()  # vsize
            pos[0] += 8 if off64 else 4
            shape = tuple(numrecs if dims[i][1] == 0 else dims[i][1] for i in ids)
            rows.append([vname, str(shape), types.get(t, "?"), str(vatts.get("units", "")), str(vatts.get("long_name", ""))])
        out.table("Variables", "variable index", ["name", "shape", "type", "units", "description"], rows)
        out.meta(dimensions={d: (s or numrecs) for d, s in dims}, attributes=gatts, records=numrecs)
        out.text("structure", "\n".join(" | ".join(r) for r in rows) + "\n" + json.dumps(gatts, default=str)[:4000])
        out.note("Variable values are not loaded here; ask for a calculation and run_python reads them.")
    except (struct.error, KeyError, IndexError) as e:
        out.note(f"The NetCDF header could not be fully read ({e}).")


def _fits(path: Path, out: Out) -> None:
    """FITS (astronomy): headers are 80-character cards in 2880-byte blocks; read without astropy."""
    out.kind("FITS file")
    hdus, rows = [], []
    with path.open("rb") as f:
        while len(hdus) < 50:
            cards, ended = {}, False
            raw_cards = []
            while not ended:
                block = f.read(2880)
                if len(block) < 2880:
                    ended = True
                    break
                for i in range(0, 2880, 80):
                    card = block[i : i + 80].decode("ascii", "replace")
                    key = card[:8].strip()
                    if key == "END":
                        ended = True
                        break
                    if card[8:10] == "= ":
                        val = card[10:].split(" /")[0].strip().strip("'").strip()
                        cards[key] = val
                    raw_cards.append(card.rstrip())
            if not cards:
                break
            naxis = int(cards.get("NAXIS", 0) or 0)
            shape = [int(cards.get(f"NAXIS{i}", 0) or 0) for i in range(1, naxis + 1)]
            bitpix = abs(int(cards.get("BITPIX", 8) or 8))
            size = 1
            for s in shape:
                size *= s
            size = (size * bitpix // 8 + int(cards.get("PCOUNT", 0) or 0)) if shape else 0
            hdus.append(cards)
            rows.append([len(hdus) - 1, cards.get("XTENSION", "PRIMARY"), cards.get("EXTNAME", ""), "x".join(map(str, shape)) or "-",
                         cards.get("BITPIX", ""), cards.get("OBJECT", ""), cards.get("TELESCOP", ""), cards.get("DATE-OBS", "")])
            out.text(f"HDU {len(hdus) - 1} header", "\n".join(raw_cards[:400]))
            f.seek((size + 2879) // 2880 * 2880, 1)
    out.table("HDUs", "header data units", ["hdu", "type", "name", "shape", "bitpix", "object", "telescope", "date obs"], rows)
    out.meta(hdus=len(hdus))
    out.note("Headers were read; pixel or table values are loaded on request with run_python (numpy).")


# --- media, drawings, archives, email -----------------------------------------------------------------------------------
def _image(path: Path, out: Out) -> None:
    out.kind("Image" if path.suffix.lower() != ".psd" else "Photoshop document")
    PIL = _need("PIL.Image", "images", out)
    out.d["images"].append(str(path))
    if not PIL:
        return
    try:
        with PIL.open(path) as im:
            exif = {}
            try:
                from PIL.ExifTags import TAGS

                exif = {TAGS.get(k, str(k)): str(v)[:120] for k, v in (im.getexif() or {}).items() if k in TAGS}
            except Exception:  # noqa: BLE001
                pass
            out.meta(width=im.width, height=im.height, mode=im.mode, format=im.format, frames=getattr(im, "n_frames", 1),
                     dpi=str(im.info.get("dpi", "")), exif={k: v for k, v in exif.items() if k in (
                         "Make", "Model", "DateTime", "DateTimeOriginal", "Software", "Artist", "Copyright", "LensModel", "GPSInfo")})
            if path.suffix.lower() in (".psd", ".tif", ".tiff", ".heic", ".bmp"):
                tmp = Path(tempfile.mkdtemp(prefix="wb-img-")) / (path.stem + ".png")
                im.convert("RGB").save(tmp)
                out.d["images"] = [str(tmp)]
    except Exception as e:  # noqa: BLE001
        out.note(f"The picture could not be opened ({str(e)[:120]}).")
    out.note("What the picture shows is read by a vision-capable model (describe_image); text in it can be read the same way.")


def _media(path: Path, out: Out, video: bool) -> None:
    out.kind("Video" if video else "Audio")
    mutagen = _need("mutagen", "audio and video details", out)
    if mutagen:
        try:
            m = mutagen.File(str(path))
            if m is not None:
                info = m.info
                tags = {}
                for k, v in (m.tags or {}).items() if hasattr(m.tags or {}, "items") else []:
                    tags[str(k)[:40]] = str(v)[:200]
                out.meta(duration_seconds=round(getattr(info, "length", 0) or 0, 2), bitrate=getattr(info, "bitrate", None),
                         sample_rate=getattr(info, "sample_rate", None), channels=getattr(info, "channels", None),
                         codec=getattr(info, "codec", None) or type(info).__name__, tags=dict(list(tags.items())[:30]))
        except Exception:  # noqa: BLE001
            pass
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            info = json.loads(r.stdout or "{}")
            streams = [{k: s.get(k) for k in ("codec_type", "codec_name", "width", "height", "r_frame_rate", "sample_rate", "channels")
                        if s.get(k)} for s in info.get("streams", [])]
            out.meta(streams=streams, duration_seconds=float((info.get("format") or {}).get("duration") or 0) or None)
    out.text("details", json.dumps(out.d["meta"], default=str, indent=1))
    out.note("Speech and pictures inside the recording are not transcribed here; details (length, format, tags) are shown."
             + ("" if ffprobe else " Installing FFmpeg (free) adds stream details."))


def _dxf(path: Path, out: Out) -> None:
    out.kind("DXF drawing (CAD)")
    ezdxf = _need("ezdxf", "DXF drawings", out)
    if not ezdxf:
        return _text(path, out)
    from ezdxf import recover

    doc, _ = recover.readfile(str(path))
    msp = doc.modelspace()
    counts: dict[str, int] = {}
    texts = []
    for e in msp:
        counts[e.dxftype()] = counts.get(e.dxftype(), 0) + 1
        if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB") and len(texts) < 3000:
            t = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.get("text", "")
            texts.append(f"[{e.dxf.layer}] {t}")
        if e.dxftype() == "INSERT":
            for a in getattr(e, "attribs", []):
                texts.append(f"[{e.dxf.name}] {a.dxf.tag}: {a.dxf.text}")
    layers = [[l.dxf.name, l.dxf.get("color", ""), "off" if l.is_off() else "on", "frozen" if l.is_frozen() else ""] for l in doc.layers]
    out.table("Layers", "layer table", ["layer", "colour", "on/off", "frozen"], layers)
    out.table("Entities", "model space", ["type", "count"], sorted(([k, v] for k, v in counts.items()), key=lambda r: -r[1]))
    units = {0: "unitless", 1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres", 6: "metres"}.get(doc.header.get("$INSUNITS", 0), "other")
    try:
        from ezdxf import bbox

        ext = bbox.extents(msp)
        size = f"{ext.size.x:.4g} x {ext.size.y:.4g} {units}" if ext.has_data else ""
    except Exception:  # noqa: BLE001
        size = ""
    out.meta(version=doc.dxfversion, units=units, layers=len(layers), entities=sum(counts.values()), drawing_size=size,
             blocks=[b.name for b in doc.blocks if not b.name.startswith("*")][:80], layouts=[l.name for l in doc.layouts])
    out.text("text and labels", "\n".join(texts))


def oda_converter() -> str | None:
    names = ("ODAFileConverter", "ODAFileConverter.exe")
    for n in names:
        if shutil.which(n):
            return shutil.which(n)
    for p in ("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter", "/usr/bin/ODAFileConverter"):
        if Path(p).exists():
            return p
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
        for d in Path(base, "ODA").glob("ODAFileConverter*/ODAFileConverter.exe") if Path(base, "ODA").exists() else []:
            return str(d)
    return None


def _dwg(path: Path, out: Out) -> None:
    out.kind("DWG drawing (AutoCAD)")
    head = path.open("rb").read(6).decode("ascii", "replace")
    versions = {"AC1015": "AutoCAD 2000", "AC1018": "AutoCAD 2004", "AC1021": "AutoCAD 2007", "AC1024": "AutoCAD 2010",
                "AC1027": "AutoCAD 2013", "AC1032": "AutoCAD 2018 or later"}
    out.meta(version=versions.get(head, head))
    oda = oda_converter()
    if oda:
        src, dst = Path(tempfile.mkdtemp(prefix="wb-dwg-")), Path(tempfile.mkdtemp(prefix="wb-dxf-"))
        shutil.copy(path, src / path.name)
        subprocess.run([oda, str(src), str(dst), "ACAD2018", "DXF", "0", "1", path.name], capture_output=True, timeout=300)
        conv = next(dst.glob("*.dxf"), None)
        if conv:
            inner = extract(conv, out.d["name"])
            out.d.update({k: inner[k] for k in ("chunks", "tables", "meta")})
            out.kind("DWG drawing (AutoCAD, converted with ODA File Converter)")
            return
    out.note("DWG is AutoCAD's closed format. To read layers, text and sizes, install the free ODA File Converter "
             "(opendesign.com/guestfiles/oda_file_converter) and try again, or save the drawing as DXF or PDF from your CAD app.")
    _strings(path, out, "")


def _zip(path: Path, out: Out, depth: int) -> None:
    out.kind({".epub": "E-book (EPUB)", ".sketch": "Sketch design file", ".fig": "Figma file", ".xd": "Adobe XD file",
              ".kmz": "Google Earth file", ".3mf": "3D print file"}.get(path.suffix.lower(), "Zip archive"))
    if depth >= ZIP_MAX_DEPTH:
        out.note("Archives nested this deep are not opened.")
        return
    with zipfile.ZipFile(path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        total = sum(i.file_size for i in infos)
        out.meta(files=len(infos), uncompressed_bytes=total)
        out.text("contents", "\n".join(f"{i.filename} ({i.file_size:,} bytes)" for i in infos[:3000]))
        if total > ZIP_MAX_BYTES or any(i.compress_size and i.file_size / i.compress_size > 200 for i in infos):
            out.note("The archive unpacks to a suspicious or very large size, so only its list of contents was read.")
            return
        tmp = Path(tempfile.mkdtemp(prefix="wb-zip-"))
        for i in infos[:ZIP_MAX_FILES]:
            name = Path(i.filename)
            if name.is_absolute() or ".." in name.parts or name.name.startswith("._") or "__MACOSX" in name.parts:
                continue
            dest = tmp / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            out.d["children"].append(extract(dest, i.filename, depth + 1))
        if len(infos) > ZIP_MAX_FILES:
            out.note(f"Only the first {ZIP_MAX_FILES} of {len(infos)} files inside were read.")


def _eml(path: Path, out: Out, depth: int) -> None:
    out.kind("Email message")
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    _email(msg, out, depth)


def _email(msg, out: Out, depth: int) -> None:
    out.meta(**{"from": str(msg.get("From", "")), "to": str(msg.get("To", "")), "cc": str(msg.get("Cc", "")),
                "subject": str(msg.get("Subject", "")), "date": str(msg.get("Date", ""))})
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", "", text))
    out.text("message body", f"From: {msg.get('From')}\nTo: {msg.get('To')}\nDate: {msg.get('Date')}\nSubject: {msg.get('Subject')}\n\n{text}")
    tmp = None
    for part in msg.iter_attachments():
        fname = part.get_filename() or "attachment"
        out.d["meta"].setdefault("attachments", []).append(fname)
        if depth < ZIP_MAX_DEPTH:
            tmp = tmp or Path(tempfile.mkdtemp(prefix="wb-mail-"))
            p = tmp / Path(fname).name
            payload = part.get_payload(decode=True) or b""
            p.write_bytes(payload)
            out.d["children"].append(extract(p, fname, depth + 1))


def _msg(path: Path, out: Out, depth: int) -> None:
    out.kind("Outlook message")
    extract_msg = _need("extract_msg", "Outlook .msg files", out)
    if not extract_msg:
        return _strings(path, out, "")
    m = extract_msg.Message(str(path))
    try:
        out.meta(**{"from": m.sender or "", "to": m.to or "", "cc": m.cc or "", "subject": m.subject or "", "date": str(m.date or "")})
        out.text("message body", f"From: {m.sender}\nTo: {m.to}\nDate: {m.date}\nSubject: {m.subject}\n\n{m.body or ''}")
        tmp = Path(tempfile.mkdtemp(prefix="wb-msg-"))
        for a in m.attachments:
            fname = getattr(a, "longFilename", None) or getattr(a, "shortFilename", None) or "attachment"
            out.d["meta"].setdefault("attachments", []).append(fname)
            data = getattr(a, "data", None)
            if isinstance(data, bytes) and depth < ZIP_MAX_DEPTH:
                p = tmp / Path(fname).name
                p.write_bytes(data)
                out.d["children"].append(extract(p, fname, depth + 1))
    finally:
        m.close()


def _strings(path: Path, out: Out, why: str) -> None:
    """Last resort: printable runs of text, like the Unix `strings` tool."""
    out.kind(out.d["kind"] or (mimetypes.guess_type(path.name)[0] or "Unknown file"))
    raw = path.read_bytes()[:20_000_000]
    runs = re.findall(rb"[\x20-\x7e\t\r\n]{6,}", raw)
    wide = re.findall(rb"(?:[\x20-\x7e]\x00){6,}", raw)
    text = "\n".join(r.decode("ascii", "replace") for r in runs[:20000])
    text += "\n".join(w.decode("utf-16-le", "replace") for w in wide[:5000])
    if text.strip():
        _lines_chunks(out, text, 80)
    if why:
        out.note(why)
    if not text.strip():
        out.note("No readable text was found inside.")


# --- one-line summaries for the assistant and the UI ---------------------------------------------------------------------
def summary(d: dict) -> str:
    m = d.get("meta", {})
    bits = [d.get("kind", "")]
    for k, label in (("pages", "pages"), ("slides", "slides"), ("paragraphs", "paragraphs"), ("rows", "rows"), ("cells", "cells"),
                     ("datasets", "datasets"), ("hdus", "HDUs"), ("entities", "entities"), ("files", "files"), ("lines", "lines")):
        if m.get(k):
            bits.append(f"{m[k]:,} {label}" if isinstance(m[k], int) else f"{m[k]} {label}")
    if m.get("sheets"):
        bits.append(f"{len(m['sheets'])} sheets ({', '.join(m['sheets'][:6])})")
    if d.get("tables") and not m.get("sheets"):
        bits.append(f"{len(d['tables'])} tables")
    if m.get("duration_seconds"):
        bits.append(f"{m['duration_seconds']:.0f} s long")
    if m.get("width"):
        bits.append(f"{m['width']}x{m['height']} px")
    return ", ".join(b for b in bits if b)


def walk(d: dict):
    """The extract and every file inside it (zip, email attachments, folders)."""
    yield d
    for c in d.get("children", []):
        yield from walk(c)
