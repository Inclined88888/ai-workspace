"""Run receipts in Workbench's run-log format.

Every action is one JSON line whose keys are columns of Workbench's `actions` table (workbench/backend/app/db.py) and
whose kinds and outcomes use its vocabulary (workbench/backend/app/actions.py):
  turn        running | no_claim | backed | model_error | step_limit | budget_paused | interrupted
  model_call  ok | error
  tool_call   verified | ok | check_failed | bad_args | unknown_tool | rejected | tool_error | denied | dry_run
  decision    approved | denied
  egress      ok | http_<status> | error        (effect local | external, like Workbench's outbound audit)
Rows link through parent_action_id. Text columns are redacted with Workbench's own patterns before they are written.
Files live in <project>/.rooks/runs/<session>.jsonl; `rooks receipts import` copies them into a Workbench database so the
terminal agent's work shows in the same run log, cost totals and reliability record (channel "rooks-code").
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .redact import redact

CHANNEL = "rooks-code"
COLUMNS = ("id", "ts", "kind", "channel", "provider", "model", "tool", "effect", "args", "latency_ms", "tokens_in",
           "tokens_out", "usd_cost", "outcome", "failure_reason", "verified", "evidence", "detail", "request_id",
           "parent_action_id")
_TEXT = ("args", "failure_reason", "evidence", "detail")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RunLog:
    def __init__(self, root: Path, session: str | None = None, provider: str = "", model: str = ""):
        self.session = session or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
        self.path = root / ".rooks" / "runs" / f"{self.session}.jsonl"
        self.provider, self.model = provider, model
        self.rows: dict[int, dict] = {}
        self._next = 1

    def add(self, kind: str, outcome: str, parent: int | None = None, **fields) -> int:
        row = {c: None for c in COLUMNS}
        row.update(channel=CHANNEL, provider=self.provider, model=self.model, tool="", effect="", args="", latency_ms=0,
                   tokens_in=0, tokens_out=0, failure_reason="", evidence="", detail="")
        row.update(fields)
        for k in _TEXT:
            if row.get(k):
                row[k] = redact(str(row[k]))[:4000]
        row.update(id=self._next, ts=now(), kind=kind, outcome=outcome, parent_action_id=parent,
                   request_id=row.get("request_id") or uuid.uuid4().hex)
        self._next += 1
        self.rows[row["id"]] = row
        self._write(row)
        return row["id"]

    def finish(self, action_id: int, outcome: str, **fields) -> None:
        row = self.rows[action_id]
        for k in _TEXT:
            if fields.get(k):
                fields[k] = redact(str(fields[k]))[:4000]
        row.update(fields, outcome=outcome)
        self._write(row)  # last line for an id wins (append-only file)

    def _write(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        gitignore = self.path.parent.parent / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n")  # receipts and undo snapshots stay out of the user's commits
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def totals(self) -> dict:
        calls = [r for r in self.rows.values() if r["kind"] == "model_call"]
        costs = [r["usd_cost"] for r in calls]
        return {"tokens_in": sum(r["tokens_in"] for r in calls), "tokens_out": sum(r["tokens_out"] for r in calls),
                "usd": round(sum(c for c in costs if c is not None), 6), "cost_unknown": any(c is None for c in costs),
                "model_calls": len(calls)}


def read(path: Path) -> list[dict]:
    rows: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r
    return [rows[k] for k in sorted(rows)]


def sessions(root: Path) -> list[Path]:
    d = root / ".rooks" / "runs"
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def import_into(db_path: Path, files: list[Path]) -> int:
    """Insert receipts into a Workbench database's `actions` table, remapping ids and parents. Idempotent: rows already
    imported (same request_id, which is UNIQUE there) are skipped. Returns the number of rows added."""
    con = sqlite3.connect(db_path)
    have = {r[1] for r in con.execute("PRAGMA table_info(actions)")}
    if not have:
        raise SystemExit(f"rooks: {db_path} has no actions table (is it a Workbench database?)")
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
                    row = {k: v for k, v in r.items() if k in have and k != "id"}
                    row["parent_action_id"] = ids.get(r.get("parent_action_id")) if r.get("parent_action_id") else None
                    row["detail"] = row.get("detail") or ""
                    if r.get("verified") is not None:
                        row["verified"] = 1 if r["verified"] else 0
                    cur = con.execute(f"INSERT INTO actions({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                                      tuple(row.values()))
                    ids[r["id"]] = cur.lastrowid
                    added += 1
    finally:
        con.close()
    return added
