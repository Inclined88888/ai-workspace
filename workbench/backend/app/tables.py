"""Spreadsheets you upload (CSV, TSV, Excel .xlsx): rows read safely, and figures computed by code, not by a model.

An .xlsx file is a zip of XML parts. It is read with regular expressions over the parts that hold cell values, never
with an XML parser or a spreadsheet engine, so nothing in an uploaded file is expanded, resolved or run (no formulas,
macros, external links or entities). Formulas are not evaluated: the value Excel last saved is used. Size limits guard
against zip bombs.
"""

import csv
import io
import re
import statistics
import zipfile
from html import unescape
from pathlib import Path

from fastapi import HTTPException

from . import db

MAX_ROWS = 50_000
MAX_COLS = 200
MAX_PART = 60 * 1024 * 1024       # uncompressed bytes of one sheet
TABLE_EXT = {".csv", ".tsv", ".xlsx"}


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _part(z: zipfile.ZipFile, name: str) -> str:
    info = z.getinfo(name)
    if info.file_size > MAX_PART:
        raise HTTPException(413, "That sheet is too large to read.")
    return z.read(name).decode("utf-8", "replace")


def xlsx_sheets(data: bytes) -> list[tuple[str, str]]:
    """[(sheet name, part path)] in workbook order."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        wb = _part(z, "xl/workbook.xml")
        rels = _part(z, "xl/_rels/workbook.xml.rels") if "xl/_rels/workbook.xml.rels" in z.namelist() else ""
    targets = {m.group(1): m.group(2) for m in re.finditer(r'<Relationship\b[^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"', rels)}
    targets.update({m.group(2): m.group(1) for m in re.finditer(r'<Relationship\b[^>]*?Target="([^"]+)"[^>]*?Id="([^"]+)"', rels)})
    out = []
    for m in re.finditer(r"<sheet\b([^>]*)/?>", wb):
        attrs = m.group(1)
        name = re.search(r'name="([^"]*)"', attrs)
        rid = re.search(r'r:id="([^"]*)"', attrs)
        target = targets.get(rid.group(1), "") if rid else ""
        if name and target:
            target = target.lstrip("/")
            out.append((unescape(name.group(1)), target if target.startswith("xl/") else "xl/" + target))
    return out


def xlsx_rows(data: bytes, sheet: str = "") -> tuple[str, list[list[str]]]:
    sheets = xlsx_sheets(data)
    if not sheets:
        raise HTTPException(400, "No sheets found in that workbook.")
    name, path = sheets[0]
    if sheet:
        match = [(n, p) for n, p in sheets if n.lower() == sheet.lower()]
        if not match:
            raise HTTPException(400, f"No sheet named {sheet}. Sheets: {', '.join(n for n, _ in sheets)}")
        name, path = match[0]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shared_xml = _part(z, "xl/sharedStrings.xml") if "xl/sharedStrings.xml" in names else ""
        if path not in names:
            raise HTTPException(400, f"The sheet {name} is missing from the file.")
        xml = _part(z, path)
    shared = ["".join(unescape(t) for t in re.findall(r"<t(?: [^>]*)?>(.*?)</t>", si, re.S))
              for si in re.findall(r"<si>(.*?)</si>", shared_xml, re.S)]
    rows: list[list[str]] = []
    for rm in re.finditer(r"<row\b[^>]*>(.*?)</row>", xml, re.S):
        if len(rows) >= MAX_ROWS:
            break
        row: list[str] = []
        for cm in re.finditer(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", rm.group(1), re.S):
            attrs, inner = cm.group(1), cm.group(2) or ""
            ref = re.search(r'r="([A-Z]+)\d+"', attrs)
            i = _col_index(ref.group(1)) if ref else len(row)
            if i >= MAX_COLS:
                continue
            kind = re.search(r't="([^"]+)"', attrs)
            kind = kind.group(1) if kind else ""
            v = re.search(r"<v>(.*?)</v>", inner, re.S)
            if kind == "s" and v:
                idx = int(v.group(1)) if v.group(1).strip().isdigit() else -1
                value = shared[idx] if 0 <= idx < len(shared) else ""
            elif kind == "inlineStr":
                value = "".join(unescape(t) for t in re.findall(r"<t(?: [^>]*)?>(.*?)</t>", inner, re.S))
            elif kind == "b" and v:
                value = "TRUE" if v.group(1) == "1" else "FALSE"
            else:
                value = unescape(v.group(1)) if v else ""
            row.extend([""] * (i - len(row) + 1))
            row[i] = value
        rows.append(row)
    return name, rows


def csv_rows(text: str, tsv: bool = False) -> list[list[str]]:
    sample = text[:20000]
    delim = "\t" if tsv else ","
    if not tsv:
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            pass
    rows = []
    for r in csv.reader(io.StringIO(text), delimiter=delim):
        rows.append(r[:MAX_COLS])
        if len(rows) >= MAX_ROWS:
            break
    return rows


def load(fid: int, sheet: str = "") -> dict:
    from .files import root

    f = db.one("SELECT id, name, path, text FROM files WHERE id = ?", fid)
    if not f:
        raise HTTPException(404, "File not found")
    ext = Path(f["name"]).suffix.lower()
    if ext not in TABLE_EXT:
        raise HTTPException(400, f"{f['name']} is not a spreadsheet. ROOKS reads CSV, TSV and Excel (.xlsx) files.")
    data = (root() / f["path"]).read_bytes()
    try:
        if ext == ".xlsx":
            sheet_name, rows = xlsx_rows(data, sheet)
        else:
            sheet_name, rows = "", csv_rows(data.decode("utf-8-sig", "replace"), ext == ".tsv")
    except (zipfile.BadZipFile, KeyError) as e:
        raise HTTPException(400, f"{f['name']} could not be read as a spreadsheet ({e.__class__.__name__}).") from e
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise HTTPException(400, f"{f['name']} has no rows.")
    width = max(len(r) for r in rows)
    header = [(h.strip() or f"Column {i + 1}") for i, h in enumerate(rows[0] + [""] * (width - len(rows[0])))]
    body = [r + [""] * (width - len(r)) for r in rows[1:]]
    return {"file_id": fid, "name": f["name"], "sheet": sheet_name, "header": header, "rows": body,
            "sheets": [n for n, _ in xlsx_sheets(data)] if ext == ".xlsx" else []}


NUM_RE = re.compile(r"^\(?-?[$€£¥]?\s*-?[\d,]*\.?\d+(?:[eE][-+]?\d+)?\s*%?\)?$")


def number(v: str) -> float | None:
    s = (v or "").strip()
    if not s or not NUM_RE.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[()$€£¥,%\s]", "", s)
    try:
        n = float(s)
    except ValueError:
        return None
    return -abs(n) if neg else n


def _summary(name: str, values: list[str]) -> dict:
    filled = [v for v in values if v.strip()]
    nums = [n for n in (number(v) for v in filled) if n is not None]
    out = {"column": name, "filled": len(filled), "empty": len(values) - len(filled)}
    if filled and len(nums) >= 0.8 * len(filled):
        out.update(type="number", sum=round(sum(nums), 4), mean=round(statistics.fmean(nums), 4) if nums else None,
                   median=round(statistics.median(nums), 4) if nums else None, min=min(nums) if nums else None,
                   max=max(nums) if nums else None, not_numbers=len(filled) - len(nums))
    else:
        counts: dict[str, int] = {}
        for v in filled:
            counts[v.strip()] = counts.get(v.strip(), 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        out.update(type="text", distinct=len(counts), top=[{"value": k[:80], "count": c} for k, c in top])
    return out


def analyze(file_id: int, sheet: str = "", group_by: str = "", value: str = "") -> dict:
    """Column by column figures for a spreadsheet, and optionally the total of one numeric column per group."""
    t = load(file_id, sheet)
    header, rows = t["header"], t["rows"]
    cols = [_summary(h, [r[i] for r in rows]) for i, h in enumerate(header)]
    out = {"file_id": file_id, "name": t["name"], "sheet": t["sheet"], "sheets": t["sheets"], "rows": len(rows), "columns": len(header),
           "summary": cols, "preview": [dict(zip(header, r)) for r in rows[:10]],
           "note": "Figures are computed from the file's cells; formulas are not recalculated (Excel's last saved values are used)."
                   if t["name"].lower().endswith(".xlsx") else "Figures are computed from the file's cells."}
    if len(rows) >= MAX_ROWS:
        out["note"] += f" Only the first {MAX_ROWS} rows were read."

    def find(col: str) -> int:
        low = [h.lower() for h in header]
        if col.lower() in low:
            return low.index(col.lower())
        raise HTTPException(400, f"No column named {col}. Columns: {', '.join(header[:40])}")
    if group_by:
        g = find(group_by)
        v = find(value) if value else None
        if v is not None and cols[v]["type"] != "number":
            raise HTTPException(400, f"{header[v]} is not a numeric column.")
        totals: dict[str, float] = {}
        for r in rows:
            key = r[g].strip() or "(empty)"
            totals[key] = totals.get(key, 0) + ((number(r[v]) or 0) if v is not None else 1)
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])
        out["groups"] = {"by": header[g], "value": header[v] if v is not None else "rows", "count": len(ranked),
                         "items": [{"label": k[:80], "value": round(x, 4)} for k, x in ranked[:25]]}
    return out


def text_of(data: bytes) -> str:
    """Searchable text of a workbook: each sheet's rows, tab separated."""
    parts = []
    for name, _ in xlsx_sheets(data)[:20]:
        _, rows = xlsx_rows(data, name)
        parts.append(f"# {name}\n" + "\n".join("\t".join(r) for r in rows[:5000]))
    return "\n\n".join(parts)
