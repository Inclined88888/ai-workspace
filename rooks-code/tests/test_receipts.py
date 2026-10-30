"""Receipts are importable into a real Workbench database, and redaction is Workbench's own."""

import ast
import json
import sqlite3
from pathlib import Path

from rooks_code import receipts
from rooks_code.redact import redact

REPO = Path(__file__).resolve().parents[2]


def workbench_schema() -> str:
    tree = ast.parse((REPO / "workbench/backend/app/db.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SCHEMA" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("SCHEMA not found in workbench db.py")


def test_redaction_is_a_verbatim_copy_of_workbench():
    ours = (Path(receipts.__file__).with_name("redact.py")).read_text().split("\n", 1)[1]
    assert ours == (REPO / "workbench/backend/app/redact.py").read_text()


def test_receipt_rows_redact_secrets(tmp_path):
    log = receipts.RunLog(tmp_path, provider="openai", model="m")
    log.add("tool_call", "ok", tool="run_command", args='curl -H "Authorization: Bearer sk-proj-abcdefghijklmnopqrstuvwxyz123"')
    row = json.loads(log.path.read_text().splitlines()[0])
    assert "sk-proj" not in row["args"] and "<redacted" in row["args"]
    assert redact("mail me at a.b@example.com") == "mail me at <email>"


def test_import_into_workbench_actions_table(tmp_path):
    db = tmp_path / "workbench.db"
    con = sqlite3.connect(db)
    con.executescript(workbench_schema())
    con.execute("INSERT INTO actions(ts, kind, outcome, request_id) VALUES ('2026-01-01T00:00:00Z', 'turn', 'backed', 'x')")
    con.commit()
    log = receipts.RunLog(tmp_path / "p", provider="ollama", model="qwen")
    turn = log.add("turn", "running", args="fix it")
    log.add("model_call", "ok", parent=turn, tokens_in=10, tokens_out=2, usd_cost=0.0)
    call = log.add("tool_call", "running", parent=turn, tool="edit_file", effect="write")
    log.add("decision", "approved", parent=call)
    log.finish(call, "verified", verified=True, evidence="calc.py read back")
    log.finish(turn, "backed")
    n = receipts.import_into(db, [log.path])
    assert n == 4
    assert receipts.import_into(db, [log.path]) == 0  # idempotent
    rows = {r[0]: r for r in con.execute("SELECT kind, outcome, parent_action_id, id, verified, channel, model FROM actions "
                                         "WHERE channel = 'rooks-code'")}
    assert rows["turn"][1] == "backed" and rows["tool_call"][1] == "verified" and rows["tool_call"][4] == 1
    assert rows["model_call"][2] == rows["turn"][3] and rows["decision"][2] == rows["tool_call"][3]
    assert rows["turn"][6] == "qwen"
