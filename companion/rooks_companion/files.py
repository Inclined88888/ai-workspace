"""File work on this computer, only inside folders you allowed, with a version kept before every change.

* Before ROOKS changes or replaces a file, the original is copied to the versions folder (~/.rooks-companion/versions).
  Moves, copies, new files and new folders are recorded too. Nothing is ever deleted: "undoing" a new file moves it
  into the versions folder.
* Every write is read back and checked. Writing identical content again does nothing (no duplicate writes).
* Undo only happens if the file is still exactly as ROOKS left it; otherwise it is refused as a conflict.
* Spreadsheets: CSV with the standard library; XLSX when openpyxl is installed. Documents: text and Markdown; DOCX
  reading with the standard library, editing when python-docx is installed. Results go to a new file by default.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import time
import uuid
import zipfile

from .policy import Refused, resolve

MAX_FILE = 5_000_000
MAX_ROWS = 50_000


def sha(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class FileOps:
    def __init__(self, folders: list[str], versions_dir: str):
        self.folders, self.vdir = folders, versions_dir
        os.makedirs(versions_dir, exist_ok=True)
        self.log = os.path.join(versions_dir, "changes.jsonl")

    # Paths
    def path(self, p: str, must_exist: bool = True) -> str:
        return resolve(str(p or ""), self.folders, must_exist=must_exist)

    # Change log
    def _record(self, entry: dict) -> str:
        entry = {"id": "c" + uuid.uuid4().hex[:12], "at": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
        with open(self.log, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry["id"]

    def _changes(self) -> list[dict]:
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f if line.strip()]

    def _keep(self, path: str) -> dict | None:
        """Copy a file's current content into the versions folder before it is changed."""
        if not os.path.isfile(path):
            return None
        digest = sha(path)
        dst = os.path.join(self.vdir, digest[:2], digest)
        if not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(path, dst)
        return {"sha": digest, "copy": dst}

    def history(self, path: str = "") -> dict:
        real = self.path(path, must_exist=False) if path else ""
        out = [c for c in self._changes() if not real or real in (c.get("path"), c.get("src"), c.get("dst"))]
        return {"entries": [{k: c.get(k) for k in ("id", "at", "kind", "path", "src", "dst", "summary", "undone")} for c in out[-100:]]}

    def _write_bytes(self, path: str, data: bytes) -> None:
        tmp = path + ".rooks-tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        if sha(path) != hashlib.sha256(data).hexdigest():
            raise RuntimeError(f"Writing {path} did not stick (the content read back is different).")

    # Basic operations
    def mkdir(self, p: str) -> dict:
        path = self.path(p, must_exist=False)
        if os.path.isdir(path):
            return {"path": path, "note": "already exists"}
        os.makedirs(path)
        return {"path": path, "change": self._record({"kind": "mkdir", "path": path, "summary": f"created folder {os.path.basename(path)}"})}

    def write(self, p: str, text: str, overwrite: bool, approved: bool) -> dict:
        path = self.path(p, must_exist=False)
        data = text.encode()
        if len(data) > MAX_FILE:
            raise Refused("Text is larger than 5 MB.")
        if os.path.exists(path):
            if sha(path) == hashlib.sha256(data).hexdigest():
                return {"path": path, "note": "already has exactly this content; nothing written"}
            if not (overwrite and approved):
                raise Refused(f"{path} already exists. Replacing it needs your approval (a version is kept).")
        before = self._keep(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._write_bytes(path, data)
        cid = self._record({"kind": "write", "path": path, "before": before, "after": sha(path),
                            "summary": f"{'replaced' if before else 'created'} {os.path.basename(path)} ({len(data)} bytes)"})
        return {"path": path, "change": cid, "verified": True}

    def move_or_copy(self, kind: str, src: str, dst: str, overwrite: bool, approved: bool) -> dict:
        s = self.path(src)
        d = self.path(dst, must_exist=False)
        if os.path.isdir(d):
            d = self.path(os.path.join(d, os.path.basename(s)), must_exist=False)
        replaced = None
        if os.path.exists(d):
            if not (overwrite and approved):
                raise Refused(f"{d} already exists. Replacing it needs your approval (a version is kept).")
            replaced = self._keep(d)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        digest = sha(s)
        (shutil.move if kind == "move" else shutil.copy2)(s, d)
        if digest and sha(d) != digest:
            raise RuntimeError(f"{d} is not identical to the original after the {kind}.")
        cid = self._record({"kind": kind, "src": s, "dst": d, "path": d, "after": sha(d), "replaced": replaced,
                            "summary": f"{'moved' if kind == 'move' else 'copied'} {os.path.basename(s)} to {os.path.dirname(d)}"})
        return {"path": d, "change": cid, "verified": True}

    def undo(self, change_id: str) -> dict:
        c = next((x for x in self._changes() if x["id"] == change_id), None)
        if not c:
            raise Refused(f"No change {change_id} on this computer.")
        if any(x.get("undo_of") == change_id for x in self._changes()):
            raise Refused("That change was already undone.")
        kind = c["kind"]
        target = c.get("dst") or c.get("path")
        if kind != "mkdir" and sha(target) != c.get("after"):
            raise Refused(f"{target} changed after ROOKS edited it, so undoing could lose your later edits. Nothing was changed.")
        if kind == "move":
            if os.path.exists(c["src"]):
                raise Refused(f"Something already exists at {c['src']}. Nothing was changed.")
            shutil.move(target, c["src"])
        elif kind in ("copy", "write") and not c.get("before"):
            parked = os.path.join(self.vdir, "undone", f"{change_id}-{os.path.basename(target)}")
            os.makedirs(os.path.dirname(parked), exist_ok=True)
            shutil.move(target, parked)  # never deleted: kept in the versions folder
        elif kind == "mkdir":
            if os.listdir(target):
                raise Refused("That folder is no longer empty. Nothing was changed.")
            os.rmdir(target)
        else:
            orig = (c.get("before") or c.get("replaced") or {}).get("copy")
            if not orig or not os.path.exists(orig):
                raise Refused("The saved version is missing. Nothing was changed.")
            self._keep(target)
            shutil.copy2(orig, target)
        if kind in ("move", "copy") and c.get("replaced"):
            shutil.copy2(c["replaced"]["copy"], target)
        self._record({"kind": "undo", "undo_of": change_id, "path": target, "summary": f"undid: {c.get('summary', '')}"})
        return {"undone": change_id, "summary": c.get("summary", "")}

    # Spreadsheets
    def _load_sheet(self, path: str, sheet: str = "") -> tuple[list[str], list[list]]:
        ext = os.path.splitext(path)[1].lower()
        if os.path.getsize(path) > MAX_FILE:
            raise Refused("That spreadsheet is larger than 5 MB.")
        if ext == ".csv":
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
        elif ext in (".xlsx", ".xlsm"):
            try:
                import openpyxl
            except ImportError:
                raise Refused("Reading .xlsx needs openpyxl on this computer: python3 -m pip install --user openpyxl")
            wb = openpyxl.load_workbook(path, data_only=False)
            ws = wb[sheet] if sheet else wb.active
            rows = [["" if v is None else v for v in r] for r in ws.iter_rows(values_only=True)]
        else:
            raise Refused("Spreadsheets must be .csv or .xlsx.")
        if not rows:
            return [], []
        return [str(h) for h in rows[0]], rows[1:MAX_ROWS + 1]

    def _save_sheet(self, path: str, header: list[str], rows: list[list], like: str, sheet: str = "") -> None:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".csv":
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
            self._write_bytes(path, buf.getvalue().encode())
        else:
            import openpyxl

            if os.path.exists(like) and os.path.splitext(like)[1].lower() in (".xlsx", ".xlsm"):
                wb = openpyxl.load_workbook(like)
                ws = wb[sheet] if sheet else wb.active
                ws.delete_rows(1, ws.max_row)
            else:
                wb = openpyxl.Workbook()
                ws = wb.active
            ws.append(header)
            for r in rows:
                ws.append(list(r))
            tmp = path + ".rooks-tmp.xlsx"
            wb.save(tmp)
            os.replace(tmp, path)

    def sheet_read(self, p: str, sheet: str = "", limit: int = 200) -> dict:
        path = self.path(p)
        header, rows = self._load_sheet(path, sheet)
        seen, dups = {}, []
        for i, r in enumerate(rows, 2):
            key = tuple(str(v).strip().lower() for v in r)
            if key in seen and any(str(v).strip() for v in r):
                dups.append({"row": i, "same_as_row": seen[key]})
            seen.setdefault(key, i)
        return {"path": path, "columns": header, "row_count": len(rows), "rows": [[str(v) for v in r] for r in rows[:limit]],
                "duplicate_rows": dups[:200], "note": f"Row numbers count the header as row 1. Showing {min(limit, len(rows))} of {len(rows)} rows."}

    def _col(self, header: list[str], c) -> int:
        if isinstance(c, int):
            idx = c - 1
        elif isinstance(c, str) and c in header:
            idx = header.index(c)
        elif isinstance(c, str) and re.fullmatch(r"[A-Za-z]{1,3}", c):
            idx = 0
            for ch in c.upper():
                idx = idx * 26 + ord(ch) - 64
            idx -= 1
        else:
            raise Refused(f"No column {c!r}. Columns: {', '.join(header)}")
        if not 0 <= idx < len(header):
            raise Refused(f"Column {c!r} is outside the sheet.")
        return idx

    def _output(self, path: str, output: str, suffix: str, approved: bool) -> str:
        if output:
            out = self.path(output, must_exist=False)
        else:
            base, ext = os.path.splitext(path)
            out = f"{base} ({suffix}){ext}"
        if out == path and not approved:
            raise Refused("Saving over the original needs your approval (a version is kept). Leave output empty to save a new file.")
        if os.path.exists(out) and out != path and not approved:
            raise Refused(f"{out} already exists. Choose another output name or approve replacing it.")
        return out

    def sheet_update(self, p: str, edits: list, output: str, approved: bool, sheet: str = "") -> dict:
        path = self.path(p)
        header, rows = self._load_sheet(path, sheet)
        if not isinstance(edits, list) or not edits or len(edits) > 1000:
            raise Refused("Give 1 to 1000 edits like {\"row\": 5, \"column\": \"Amount\", \"value\": 120}.")
        for e in edits:
            r = int(e.get("row", 0)) - 2
            if not 0 <= r < len(rows):
                raise Refused(f"Row {e.get('row')} does not exist (data starts at row 2).")
            ci = self._col(header, e.get("column"))
            while len(rows[r]) <= ci:
                rows[r].append("")
            rows[r][ci] = e.get("value", "")
        out = self._output(path, output, "updated", approved)
        before = self._keep(out)
        self._save_sheet(out, header, rows, path, sheet)
        h2, r2 = self._load_sheet(out, sheet)
        for e in edits:  # verify every edited cell in the saved file
            got = r2[int(e["row"]) - 2][self._col(h2, e["column"])]
            if str(got) != str(e.get("value", "")):
                raise RuntimeError(f"Saved file has {got!r} at row {e['row']}, expected {e.get('value')!r}.")
        cid = self._record({"kind": "write", "path": out, "before": before, "after": sha(out), "summary": f"updated {len(edits)} cell(s) in {os.path.basename(out)}"})
        return {"path": out, "change": cid, "verified": True, "edited": len(edits)}

    def sheet_dedupe(self, p: str, columns: list | None, output: str, approved: bool, sheet: str = "") -> dict:
        path = self.path(p)
        header, rows = self._load_sheet(path, sheet)
        idx = [self._col(header, c) for c in columns] if columns else list(range(len(header)))
        seen, keep, removed = set(), [], []
        for i, r in enumerate(rows, 2):
            key = tuple(str(r[j]).strip().lower() if j < len(r) else "" for j in idx)
            if key in seen:
                removed.append(i)
                continue
            seen.add(key)
            keep.append(r)
        out = self._output(path, output, "deduplicated", approved)
        before = self._keep(out)
        self._save_sheet(out, header, keep, path, sheet)
        if len(self._load_sheet(out, sheet)[1]) != len(keep):
            raise RuntimeError("The saved file does not have the expected number of rows.")
        cid = self._record({"kind": "write", "path": out, "before": before, "after": sha(out),
                            "summary": f"removed {len(removed)} duplicate row(s) into {os.path.basename(out)}"})
        return {"path": out, "change": cid, "verified": True, "removed_rows": removed, "kept": len(keep)}

    # Documents
    def doc_read(self, p: str) -> dict:
        path = self.path(p)
        ext = os.path.splitext(path)[1].lower()
        if os.path.getsize(path) > MAX_FILE:
            raise Refused("That document is larger than 5 MB.")
        if ext in (".txt", ".md", ".markdown", ".csv"):
            with open(path, encoding="utf-8", errors="replace") as f:
                return {"path": path, "content": f.read()}
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            paras = [re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)]
            return {"path": path, "content": "\n".join(p for p in paras)}
        raise Refused("Documents must be .txt, .md or .docx.")

    def doc_write(self, p: str, text: str | None, replace: list | None, output: str, approved: bool) -> dict:
        path = self.path(p)
        ext = os.path.splitext(path)[1].lower()
        out = self._output(path, output, "edited", approved)
        if ext in (".txt", ".md", ".markdown"):
            content = self.doc_read(path)["content"]
            if text is not None:
                content = text
            for r in replace or []:
                if r.get("find", "") not in content:
                    raise Refused(f"'{str(r.get('find'))[:60]}' is not in the document. Nothing was changed.")
                content = content.replace(r["find"], str(r.get("replace", "")), int(r.get("count", 1)) or 1)
            before = self._keep(out)
            self._write_bytes(out, content.encode())
        elif ext == ".docx":
            try:
                import docx  # python-docx
            except ImportError:
                raise Refused("Editing .docx needs python-docx on this computer: python3 -m pip install --user python-docx")
            d = docx.Document(path)
            for r in replace or []:
                hits = [para for para in d.paragraphs if r.get("find", "") in para.text]
                if not hits:
                    raise Refused(f"'{str(r.get('find'))[:60]}' is not in the document. Nothing was changed.")
                for para in hits[: int(r.get("count", 1)) or 1]:
                    para.text = para.text.replace(r["find"], str(r.get("replace", "")))
            if text is not None:
                d.add_paragraph(text)
            before = self._keep(out)
            tmp = out + ".rooks-tmp.docx"
            d.save(tmp)
            os.replace(tmp, out)
        else:
            raise Refused("Documents must be .txt, .md or .docx.")
        check = self.doc_read(out)["content"]
        for r in replace or []:
            if str(r.get("replace", "")) and str(r.get("replace", "")) not in check:
                raise RuntimeError("The saved document does not contain the replacement.")
        cid = self._record({"kind": "write", "path": out, "before": before, "after": sha(out), "summary": f"edited {os.path.basename(out)}"})
        return {"path": out, "change": cid, "verified": True}
