"""Work on files the user added: a library on disk, spreadsheet statistics, comparisons (redlines), passage search with
citations, and new files as output (Excel, Word, PDF). Everything runs locally."""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from . import config, db, files

SCHEMA = """
CREATE TABLE IF NOT EXISTS work_files (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'upload',   -- upload | site:<id> | output | path
    summary TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
"""
_ready: set[str] = set()


def ensure() -> None:
    key = str(config.DB_PATH)
    if key not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(key)


def root() -> Path:
    p = config.DATA_DIR / "files"
    p.mkdir(parents=True, exist_ok=True)
    return p


def outputs_dir() -> Path:
    p = root() / "outputs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip() or "file"
    return re.sub(r"[^\w.\- ()]+", "_", name)[:180]


def add(name: str, data: bytes | None = None, src: Path | None = None, source: str = "upload") -> dict:
    """Store a copy of the file in the library, read it once and cache the extract."""
    ensure()
    folder = root() / uuid.uuid4().hex[:12]
    folder.mkdir()
    dest = folder / _safe_name(name)
    if data is not None:
        dest.write_bytes(data)
    elif src is not None:
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy(src, dest)
    sha = hashlib.sha256(dest.read_bytes()).hexdigest() if dest.is_file() else ""
    d = files.extract(dest, name)
    (folder / "extract.json").write_text(json.dumps(d, default=str))
    fid = db.run("INSERT INTO work_files(name, path, kind, size, sha256, source, summary, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 name[:300], str(dest), d["kind"], d["size"], sha, source, files.summary(d), json.dumps(d["notes"]), db.now())
    return get(fid)


def get(fid: int) -> dict:
    ensure()
    r = db.one("SELECT * FROM work_files WHERE id = ?", int(fid))
    if not r:
        raise HTTPException(404, f"No file #{fid} in the library.")
    r["notes"] = json.loads(r["notes"] or "[]")
    return r


def listing(limit: int = 200) -> list[dict]:
    ensure()
    rows = db.all("SELECT * FROM work_files ORDER BY id DESC LIMIT ?", limit)
    for r in rows:
        r["notes"] = json.loads(r["notes"] or "[]")
    return rows


def remove(fid: int) -> dict:
    f = get(fid)
    p = Path(f["path"])
    if root() in p.parents:
        shutil.rmtree(p.parent, ignore_errors=True)
    db.run("DELETE FROM work_files WHERE id = ?", fid)
    return {"ok": True}


def extract_of(fid: int) -> dict:
    f = get(fid)
    cache = Path(f["path"]).parent / "extract.json"
    if cache.exists():
        return json.loads(cache.read_text())
    d = files.extract(f["path"], f["name"])
    cache.write_text(json.dumps(d, default=str))
    return d


def resolve(ref) -> tuple[Path, str]:
    """A library id, or a path the user typed (desktop only: the app runs on their own computer)."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().lstrip("#").isdigit()):
        f = get(int(str(ref).lstrip("#")))
        return Path(f["path"]), f["name"]
    p = Path(str(ref)).expanduser()
    if not config.DESKTOP:
        raise HTTPException(400, "Use a file id from the Files page (add the file there first).")
    if not p.exists():
        raise HTTPException(404, f"There is no file at {p}.")
    return p, p.name


# --- reading for the assistant --------------------------------------------------------------------------------------
def read(ref, part: int = 1, query: str = "") -> dict:
    """The extract, paged: about 24k characters per part, every passage with its location."""
    path, name = resolve(ref)
    d = extract_of(int(str(ref).lstrip("#"))) if str(ref).lstrip("#").isdigit() else files.extract(path, name)
    chunks = [{**c, "file": x["name"]} for x in files.walk(d) for c in x["chunks"]]
    if query:
        chunks = _rank(chunks, query)[:30] or chunks
    pages, cur, size = [], [], 0
    for c in chunks:
        if size + len(c["text"]) > 24000 and cur:
            pages.append(cur)
            cur, size = [], 0
        cur.append(c)
        size += len(c["text"])
    pages.append(cur)
    part = max(1, min(part, len(pages)))
    tables = [{"file": x["name"], "name": t["name"], "loc": t["loc"], "columns": t["columns"], "n_rows": t["n_rows"],
               "first_rows": t["rows"][:8]} for x in files.walk(d) for t in x["tables"]][:20]
    return {"name": d["name"], "kind": d["kind"], "summary": files.summary(d), "meta": d["meta"], "notes": [n for x in files.walk(d) for n in x["notes"]],
            "part": part, "parts": len(pages), "passages": [f"[{c['file']}, {c['loc']}] {c['text']}" for c in pages[part - 1]],
            "tables": tables, "files_inside": [x["name"] for x in files.walk(d)][1:60],
            "images": [i for x in files.walk(d) for i in x["images"]][:10],
            "cite_as": "Cite every point as [file, location], e.g. [lease.pdf, page 3, paragraph 2]."}


def _rank(chunks: list[dict], query: str) -> list[dict]:
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    if not words:
        return chunks
    scored = []
    for c in chunks:
        t = c["text"].lower()
        s = sum(t.count(w) for w in words) + (5 if query.lower() in t else 0)
        if s:
            scored.append((s, c))
    return [c for _, c in sorted(scored, key=lambda x: -x[0])]


def search(query: str, file_ids: list[int] | None = None, limit: int = 20) -> list[dict]:
    """Passages across the library that match, each with file and location, for quoting."""
    ensure()
    ids = file_ids or [r["id"] for r in db.all("SELECT id FROM work_files WHERE source != 'output' ORDER BY id DESC LIMIT 200")]
    hits = []
    for fid in ids:
        try:
            d = extract_of(fid)
        except HTTPException:
            continue
        hits += [{"file_id": fid, "file": x["name"], "loc": c["loc"], "text": c["text"][:1200]} for x in files.walk(d) for c in x["chunks"]]
    return _rank(hits, query)[:limit]


# --- spreadsheets --------------------------------------------------------------------------------------------------
def _frames(path: Path, sheet: str | None = None) -> dict:
    try:
        import pandas as pd
    except ImportError as e:
        raise HTTPException(400, "Spreadsheet statistics need the files add-on (requirements-files.txt).") from e
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv", ".tab", ".txt"):
        sep = "\t" if ext in (".tsv", ".tab") else None
        if path.stat().st_size > files.BIG_CSV_BYTES:
            return {path.stem: _chunked_csv(path, sep)}
        return {path.stem: pd.read_csv(path, sep=sep, engine="python", encoding_errors="replace")}
    if ext in (".xlsx", ".xlsm", ".xls", ".ods"):
        xl = pd.read_excel(path, sheet_name=sheet or None)
        return xl if isinstance(xl, dict) else {sheet: xl}
    if ext == ".parquet":
        return {path.stem: pd.read_parquet(path)}
    if ext in (".json", ".jsonl", ".ndjson"):
        d = files.extract(path)
        t = d["tables"][0] if d["tables"] else None
        if t:
            return {t["name"]: pd.DataFrame(t["rows"], columns=t["columns"])}
    d = files.extract(path)
    if d["tables"]:
        return {t["name"]: pd.DataFrame(t["rows"], columns=t["columns"]) for t in d["tables"]}
    raise HTTPException(400, f"{path.name} has no table to analyse.")


def _chunked_csv(path: Path, sep):
    """A very large CSV: stats over every row in chunks, then a sample frame for the rest of the analysis."""
    import pandas as pd

    sample, rows = [], 0
    for chunk in pd.read_csv(path, sep=sep or ",", chunksize=200_000, encoding_errors="replace", low_memory=False):
        rows += len(chunk)
        if sum(len(s) for s in sample) < 200_000:
            sample.append(chunk)
    df = pd.concat(sample)
    df.attrs["total_rows"] = rows
    return df


def analyse_spreadsheet(ref, sheet: str = "", chart: bool = True) -> dict:
    path, name = resolve(ref)
    import pandas as pd

    frames = _frames(path, sheet or None)
    formulas = {}
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        d = files.extract(path, name)
        formulas = {t["name"]: {"count": t.get("n_formulas", 0), "examples": t.get("formulas", [])[:10]} for t in d["tables"]}
    report = {"file": name, "sheets": []}
    for sname, df in frames.items():
        df = df.dropna(how="all").dropna(axis=1, how="all")
        cols = []
        for c in df.columns:
            s = df[c]
            info = {"column": str(c), "filled": int(s.notna().sum()), "missing": int(s.isna().sum()), "distinct": int(s.nunique(dropna=True))}
            num = pd.to_numeric(s, errors="coerce")
            if num.notna().sum() >= max(1, 0.8 * s.notna().sum()) and s.notna().any():
                info.update(type="number", sum=_n(num.sum()), mean=_n(num.mean()), median=_n(num.median()), min=_n(num.min()),
                            max=_n(num.max()), std=_n(num.std()))
            else:
                dt = pd.to_datetime(s, errors="coerce", format="mixed") if s.dtype == object else (s if "datetime" in str(s.dtype) else None)
                if dt is not None and dt.notna().sum() >= max(1, 0.8 * s.notna().sum()) and s.notna().any():
                    info.update(type="date", earliest=str(dt.min())[:19], latest=str(dt.max())[:19])
                else:
                    top = s.astype(str).value_counts().head(5)
                    info.update(type="text", most_common={str(k)[:60]: int(v) for k, v in top.items()})
            cols.append(info)
        sheet_rep = {"sheet": str(sname), "rows": int(df.attrs.get("total_rows", len(df))), "columns": len(df.columns),
                     "duplicate_rows": int(df.duplicated().sum()), "column_summary": cols, "formulas": formulas.get(str(sname), {})}
        if df.attrs.get("total_rows"):
            sheet_rep["note"] = f"Statistics are from a sample of {len(df):,} of {df.attrs['total_rows']:,} rows; run_python reads all rows."
        if chart:
            sheet_rep["chart"] = _auto_chart(df, f"{Path(name).stem}-{sname}")
        report["sheets"].append(sheet_rep)
    return report


def _n(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else round(f, 4)
    except (TypeError, ValueError):
        return None


def _auto_chart(df, stem: str) -> dict | None:
    """One sensible chart: a number over dates (line), or a number by category (bar)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
    except ImportError:
        return None
    nums = [c for c in df.columns if pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.8]
    if not nums:
        return None
    y = nums[-1] if len(nums) > 1 else nums[0]
    dates = [c for c in df.columns if c not in nums and df[c].dtype == object and pd.to_datetime(df[c], errors="coerce", format="mixed").notna().mean() > 0.8]
    cats = [c for c in df.columns if c not in nums and 1 < df[c].nunique() <= 30]
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=120)
    try:
        if dates:
            x = pd.to_datetime(df[dates[0]], errors="coerce", format="mixed")
            s = pd.Series(pd.to_numeric(df[y], errors="coerce").values, index=x).dropna().sort_index()
            s.plot(ax=ax, color="#C77700")
            title = f"{y} over {dates[0]}"
        elif cats:
            s = pd.to_numeric(df[y], errors="coerce").groupby(df[cats[0]]).sum().sort_values(ascending=False).head(15)
            s.plot.bar(ax=ax, color="#C77700")
            title = f"{y} by {cats[0]}"
        else:
            pd.to_numeric(df[y], errors="coerce").dropna().plot.hist(ax=ax, bins=20, color="#C77700")
            title = f"Spread of {y}"
        ax.set_title(title)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        p = outputs_dir() / f"{_safe_name(stem)}-{uuid.uuid4().hex[:6]}.png"
        fig.savefig(p)
        return {"title": title, "file": _register_output(p, f"Chart: {title}.png")["id"]}
    finally:
        plt.close(fig)


# --- comparison --------------------------------------------------------------------------------------------------------
def _paragraphs(d: dict) -> list[tuple[str, str]]:
    out = []
    for x in files.walk(d):
        for c in x["chunks"]:
            for p in re.split(r"\n\s*\n|(?=¶\d+ )", c["text"]):
                p = p.strip()
                if p:
                    out.append((c["loc"], re.sub(r"^¶\d+ ", "", p)))
    return out


def compare(a_ref, b_ref) -> dict:
    """What changed from A to B. Text: paragraph-level changes with word-level redlines. Tables: cell changes."""
    pa, na = resolve(a_ref)
    pb, nb = resolve(b_ref)
    da, db_ = files.extract(pa, na), files.extract(pb, nb)
    changes = []
    ta = {t["name"]: t for t in da["tables"]}
    tb = {t["name"]: t for t in db_["tables"]}
    cell_changes = []
    for name in ta.keys() & tb.keys():
        A, B = ta[name], tb[name]
        if A["columns"] != B["columns"]:
            cell_changes.append({"table": name, "change": "columns", "before": A["columns"], "after": B["columns"]})
        for i in range(max(len(A["rows"]), len(B["rows"]))):
            ra = A["rows"][i] if i < len(A["rows"]) else None
            rb = B["rows"][i] if i < len(B["rows"]) else None
            if ra == rb:
                continue
            if ra is None or rb is None:
                cell_changes.append({"table": name, "row": i + 2, "change": "added row" if ra is None else "removed row", "values": rb or ra})
                continue
            for j in range(max(len(ra), len(rb))):
                va = ra[j] if j < len(ra) else ""
                vb = rb[j] if j < len(rb) else ""
                if str(va) != str(vb):
                    col = B["columns"][j] if j < len(B["columns"]) else f"Column {j + 1}"
                    cell_changes.append({"table": name, "cell": f"{files._col(j + 1)}{i + 2}", "column": col, "before": va, "after": vb})
    for name in ta.keys() - tb.keys():
        cell_changes.append({"table": name, "change": "table removed"})
    for name in tb.keys() - ta.keys():
        cell_changes.append({"table": name, "change": "table added"})
    pa_, pb_ = _paragraphs(da), _paragraphs(db_)
    sm = difflib.SequenceMatcher(a=[t for _, t in pa_], b=[t for _, t in pb_], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        before = "\n".join(t for _, t in pa_[i1:i2])
        after = "\n".join(t for _, t in pb_[j1:j2])
        changes.append({"change": {"replace": "changed", "delete": "removed", "insert": "added"}[op],
                        "where_before": pa_[i1][0] if i1 < i2 else "", "where_after": pb_[j1][0] if j1 < j2 else "",
                        "before": before[:2000], "after": after[:2000], "redline": redline(before, after)})
    ratio = round(sm.ratio() * 100, 1)
    return {"a": na, "b": nb, "similarity_percent": ratio, "text_changes": changes[:300], "n_text_changes": len(changes),
            "cell_changes": cell_changes[:500], "n_cell_changes": len(cell_changes),
            "summary": f"{len(changes)} text passages and {len(cell_changes)} table cells differ; the documents are {ratio}% alike.",
            "cite_as": "Quote each change with its location in both versions."}


def redline(before: str, after: str) -> str:
    """Word-level redline as safe HTML: <del> for removed words, <ins> for added."""
    a, b = re.findall(r"\S+|\s+", before), re.findall(r"\S+|\s+", after)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(a[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del>{html.escape(''.join(a[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{html.escape(''.join(b[j1:j2]))}</ins>")
    return "".join(out)


# --- outputs -----------------------------------------------------------------------------------------------------------
def _register_output(p: Path, name: str) -> dict:
    ensure()
    fid = db.run("INSERT INTO work_files(name, path, kind, size, sha256, source, summary, notes, created_at) VALUES (?, ?, ?, ?, '', 'output', ?, '[]', ?)",
                 name, str(p), p.suffix.lstrip(".").upper() + " file", p.stat().st_size, "made by the assistant", db.now())
    return get(fid)


def make_excel(title: str, sheets: dict[str, list[dict]] | None = None, source_file=None, edits: list[dict] | None = None) -> dict:
    """A new workbook: sheets of rows ({column: value}), or a copy of an existing workbook with cell edits applied."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
    except ImportError as e:
        raise HTTPException(400, "Making Excel files needs the files add-on.") from e
    if source_file is not None:
        src, _ = resolve(source_file)
        wb = openpyxl.load_workbook(src)
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
    for sname, rows in (sheets or {}).items():
        ws = wb.create_sheet(_safe_name(sname)[:31])
        cols = list(dict.fromkeys(k for r in rows for k in r))
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="FFF3D6")
        for r in rows:
            ws.append([r.get(c) for c in cols])
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[files._col(i)].width = min(50, max(10, len(str(c)) + 2))
    applied = []
    for e in edits or []:
        ws = wb[e.get("sheet")] if e.get("sheet") in wb.sheetnames else wb.worksheets[0]
        ws[e["cell"]] = e.get("value")
        applied.append(f"{ws.title}!{e['cell']}")
    if not wb.worksheets:
        wb.create_sheet("Sheet1")
    p = outputs_dir() / f"{_safe_name(title)}-{uuid.uuid4().hex[:6]}.xlsx"
    wb.save(p)
    out = _register_output(p, f"{title}.xlsx")
    return {**out, "edits_applied": applied, "download": f"/api/files/{out['id']}/download"}


def _blocks(markdown: str):
    for line in markdown.splitlines():
        s = line.rstrip()
        m = re.match(r"^(#{1,3})\s+(.*)", s)
        if m:
            yield ("h", len(m.group(1)), m.group(2))
        elif re.match(r"^\s*[-*]\s+", s):
            yield ("li", 0, re.sub(r"^\s*[-*]\s+", "", s))
        elif s.strip():
            yield ("p", 0, s)


def make_word(title: str, body: str, compare_with: dict | None = None) -> dict:
    """A Word report from Markdown-ish text. With compare_with (a compare() result) it adds a redline section."""
    try:
        import docx
        from docx.shared import RGBColor
    except ImportError as e:
        raise HTTPException(400, "Making Word files needs the files add-on.") from e
    d = docx.Document()
    d.add_heading(title, 0)
    for kind, level, text in _blocks(body):
        if kind == "h":
            d.add_heading(text, level)
        elif kind == "li":
            d.add_paragraph(_strip_md(text), style="List Bullet")
        else:
            d.add_paragraph(_strip_md(text))
    if compare_with:
        d.add_heading(f"Changes from {compare_with['a']} to {compare_with['b']}", 1)
        for ch in compare_with["text_changes"]:
            p = d.add_paragraph()
            p.add_run(f"{ch['change'].capitalize()} ({ch['where_after'] or ch['where_before']}): ").bold = True
            for part in re.split(r"(<del>.*?</del>|<ins>.*?</ins>)", ch["redline"], flags=re.S):
                if part.startswith("<del>"):
                    r = p.add_run(html.unescape(part[5:-6]))
                    r.font.strike = True
                    r.font.color.rgb = RGBColor(0xB0, 0x24, 0x18)
                elif part.startswith("<ins>"):
                    r = p.add_run(html.unescape(part[5:-6]))
                    r.font.underline = True
                    r.font.color.rgb = RGBColor(0x1E, 0x6B, 0x2E)
                elif part:
                    p.add_run(html.unescape(part))
    p = outputs_dir() / f"{_safe_name(title)}-{uuid.uuid4().hex[:6]}.docx"
    d.save(p)
    out = _register_output(p, f"{title}.docx")
    return {**out, "download": f"/api/files/{out['id']}/download"}


def _strip_md(s: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`", lambda m: next(g for g in m.groups() if g), s)


def make_pdf(title: str, body: str, images: list[int] | None = None) -> dict:
    try:
        from fpdf import FPDF
    except ImportError as e:
        raise HTTPException(400, "Making PDF files needs the files add-on.") from e
    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    font = _unicode_font()
    if font:
        pdf.add_font("body", "", font)
        fam = "body"
    else:
        fam = "helvetica"
    enc = (lambda s: s) if font else (lambda s: s.encode("latin-1", "replace").decode("latin-1"))
    pdf.set_font(fam, size=20)
    pdf.multi_cell(0, 10, enc(title))
    pdf.ln(2)
    for kind, level, text in _blocks(body):
        pdf.set_font(fam, size={1: 16, 2: 14, 3: 12}.get(level, 11))
        pdf.multi_cell(0, 6 if kind != "h" else 8, enc(("•  " if kind == "li" else "") + _strip_md(text)))
        pdf.ln(1)
    for fid in images or []:
        try:
            pdf.image(get(fid)["path"], w=170)
        except Exception:  # noqa: BLE001 - a missing chart must not lose the report
            pass
    p = outputs_dir() / f"{_safe_name(title)}-{uuid.uuid4().hex[:6]}.pdf"
    pdf.output(str(p))
    out = _register_output(p, f"{title}.pdf")
    return {**out, "download": f"/api/files/{out['id']}/download"}


def _unicode_font() -> str | None:
    for p in ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf", r"C:\Windows\Fonts\arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if Path(p).exists():
            return p
    return None


async def describe_image(ref, question: str = "", ctx=None) -> dict:
    """What a picture (or a scanned page) shows, read by the conversation's model when it accepts images."""
    import base64

    from . import llm

    path, name = resolve(ref)
    d = files.extract(path, name)
    imgs = [i for x in files.walk(d) for i in x["images"]][:4]
    if not imgs:
        raise HTTPException(400, f"{name} has no picture to look at.")
    parts = [{"type": "text", "text": question or "Describe this image in detail and transcribe any text in it."}]
    for i in imgs:
        mime = "image/png" if i.lower().endswith(".png") else "image/jpeg"
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(Path(i).read_bytes()).decode()}})
    try:
        text = await llm.complete((ctx.model if ctx else "") or None, [{"role": "user", "content": parts}])
    except HTTPException as e:
        raise HTTPException(400, "The current model could not look at the picture (it may not accept images). Pick a "
                                 f"vision-capable model such as GPT-4o, Claude or Gemini. ({e.detail})") from e
    return {"file": name, "pictures": len(imgs), "description": text, "meta": d["meta"]}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
