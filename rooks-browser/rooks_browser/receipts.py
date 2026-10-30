"""Receipts in Workbench's run-log format: every visited URL, every model call, every action and every approval.

Each row is one JSON line whose keys are columns of Workbench's `actions` table (workbench/backend/app/db.py) and whose
kinds and outcomes use its vocabulary (workbench/backend/app/actions.py and egress.py):

  turn        running | backed | no_claim | model_error | step_limit | interrupted        (one per task)
  model_call  ok | error
  tool_call   ok | verified | bad_args | unknown_tool | tool_error | denied | blocked
  decision    approved | denied                                                         (the user's answer)
  egress      ok | http_<status> | error      effect local | external, args "METHOD scheme://host/path"

Egress rows follow Workbench's outbound audit: no query string, text redacted with Workbench's own patterns. Pages
(main frame and iframes) get one row each; a page's other requests (images, scripts, fonts, XHR) are folded into one
row per host per page with the count in `detail`, so the log stays readable but still names every host contacted.

Files live in <home>/runs/<session>.jsonl. `rooks-browser receipts import --db workbench.db` copies them into
Workbench's run log (channel "rooks-browser") idempotently; `--workbench-db` does it live.
"""

from __future__ import annotations

import ipaddress
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .redact import redact

CHANNEL = "rooks-browser"
COLUMNS = ("id", "ts", "kind", "channel", "provider", "model", "tool", "effect", "args", "latency_ms", "tokens_in",
           "tokens_out", "usd_cost", "outcome", "failure_reason", "verified", "evidence", "detail", "from_text", "request_id",
           "parent_action_id")
_TEXT = ("args", "failure_reason", "evidence", "detail")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def is_local(host: str) -> bool:
    """Same rule as workbench/backend/app/egress.py: loopback, private, link-local, *.local/.internal/.localhost, single labels."""
    h = (host or "").lower().strip("[]")
    if h == "localhost" or h.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return "." not in h


def target(method: str, url: str) -> str:
    u = urlsplit(url)
    host = u.hostname or ""
    return f"{method} {u.scheme}://{host}{f':{u.port}' if u.port else ''}{u.path}"


class RunLog:
    def __init__(self, home: Path, session: str | None = None, provider: str = "", model: str = "",
                 workbench_db: Path | None = None):
        self.session = session or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
        self.path = Path(home) / "runs" / f"{self.session}.jsonl"
        self.provider, self.model = provider, model
        self.rows: dict[int, dict] = {}
        self._next = 1
        self._lock = threading.Lock()
        self.workbench_db = workbench_db
        self._wb_ids: dict[int, int] = {}

    def add(self, kind: str, outcome: str, parent: int | None = None, **fields) -> int:
        row = {c: None for c in COLUMNS}
        row.update(channel=CHANNEL, provider=self.provider, model=self.model, tool="", effect="", args="", latency_ms=0,
                   tokens_in=0, tokens_out=0, failure_reason="", evidence="", detail="", from_text=0)
        row.update(fields)
        for k in _TEXT:
            if row.get(k):
                row[k] = redact(str(row[k]))[:4000]
        with self._lock:
            row.update(id=self._next, ts=now(), kind=kind, outcome=outcome, parent_action_id=parent,
                       request_id=row.get("request_id") or f"rooks-browser:{uuid.uuid4().hex}")
            self._next += 1
            self.rows[row["id"]] = row
            self._write(row)
        return row["id"]

    def finish(self, action_id: int, outcome: str, **fields) -> None:
        with self._lock:
            row = self.rows[action_id]
            for k in _TEXT:
                if fields.get(k):
                    fields[k] = redact(str(fields[k]))[:4000]
            row.update(fields, outcome=outcome)
            self._write(row)  # append-only: the last line for an id wins

    def egress(self, method: str, url: str, outcome: str, ms: int = 0, parent: int | None = None, tool: str = "page",
               detail: str = "", error: str = "") -> int:
        host = urlsplit(url).hostname or ""
        return self.add("egress", outcome, parent, provider=host, tool=f"browser.{tool}",
                        effect="local" if is_local(host) else "external", args=target(method, url), latency_ms=ms,
                        detail=detail, failure_reason=error)

    def visited(self) -> list[str]:
        return [r["args"].split(" ", 1)[1] for r in self.rows.values() if r["kind"] == "egress" and r["tool"] == "browser.page"]

    def _write(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if self.workbench_db:
            try:
                self._wb_upsert(row)
            except sqlite3.Error:
                pass  # the file receipt is the source of truth; `receipts import` can copy it later

    def _wb_upsert(self, r: dict) -> None:
        con = sqlite3.connect(self.workbench_db, timeout=10)
        try:
            have = {c[1] for c in con.execute("PRAGMA table_info(actions)")}
            if not have:
                return
            with con:
                if r["id"] in self._wb_ids:
                    con.execute("UPDATE actions SET outcome = ?, failure_reason = ?, evidence = ?, detail = ?, latency_ms = ? "
                                "WHERE id = ?", (r["outcome"], r["failure_reason"], r["evidence"], r["detail"] or "",
                                                 r["latency_ms"], self._wb_ids[r["id"]]))
                    return
                self._wb_ids[r["id"]] = _insert(con, r, have, self._wb_ids)
        finally:
            con.close()


def _insert(con: sqlite3.Connection, r: dict, have: set[str], ids: dict[int, int]) -> int:
    row = {k: v for k, v in r.items() if k in have and k != "id"}
    row["parent_action_id"] = ids.get(r.get("parent_action_id")) if r.get("parent_action_id") else None
    row["detail"] = row.get("detail") or ""
    if r.get("verified") is not None:
        row["verified"] = 1 if r["verified"] else 0
    cur = con.execute(f"INSERT INTO actions({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    return cur.lastrowid


def read(path: Path) -> list[dict]:
    rows: dict[int, dict] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r
    return [rows[k] for k in sorted(rows)]


def sessions(home: Path) -> list[Path]:
    d = Path(home) / "runs"
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def import_into(db_path: Path, files: list[Path]) -> int:
    """Copy receipts into a Workbench database's `actions` table, remapping ids and parents. Rows already there (same
    request_id, UNIQUE in Workbench) are skipped, so importing twice adds nothing. Returns the number of rows added."""
    con = sqlite3.connect(db_path)
    have = {r[1] for r in con.execute("PRAGMA table_info(actions)")}
    if not have:
        con.close()
        raise SystemExit(f"rooks-browser: {db_path} has no actions table (is it a Workbench database?)")
    added = 0
    try:
        with con:
            for f in files:
                ids: dict[int, int] = {}
                for r in read(f):
                    found = con.execute("SELECT id FROM actions WHERE request_id = ?", (r["request_id"],)).fetchone()
                    if found:
                        ids[r["id"]] = found[0]
                        continue
                    ids[r["id"]] = _insert(con, r, have, ids)
                    added += 1
    finally:
        con.close()
    return added
