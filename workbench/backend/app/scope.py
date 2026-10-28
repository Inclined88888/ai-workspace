"""What the assistant may see from a conversation: the boundary between projects.

The pages of ROOKS show you everything. The assistant, working in a conversation, sees:
  * outside any project: items in no project, and items of projects that are not isolated;
  * in a project: that project's items and items in no project;
  * in an isolated project: only that project's items.
This applies to notes and documents, files, tasks, missions, research, project status and past conversations. Memory has
its own, stricter rule (memory.scope_sql). A file attached to a message in this conversation is always readable in it,
because you gave it to the assistant there.

ROOKS has one user per workspace; this is a boundary between projects, not between people.
"""

from fastapi import HTTPException

from . import db

TABLES = {"docs", "files", "tasks", "missions", "research", "conversations"}


def visible(project_id: int | None, alias: str = "") -> tuple[str, list]:
    a = f"{alias}." if alias else ""
    if not project_id:
        return f"({a}project_id IS NULL OR {a}project_id IN (SELECT id FROM projects WHERE isolated = 0))", []
    p = db.one("SELECT isolated FROM projects WHERE id = ?", project_id)
    if p and p["isolated"]:
        return f"{a}project_id = ?", [project_id]
    return f"({a}project_id IS NULL OR {a}project_id = ?)", [project_id]


def allowed_ids(table: str, ids: list[int], project_id: int | None) -> set[int]:
    assert table in TABLES
    ids = [i for i in ids if isinstance(i, int)]
    if not ids:
        return set()
    where, args = visible(project_id)
    rows = db.all(f"SELECT id FROM {table} WHERE id IN ({','.join('?' * len(ids))}) AND {where}", *ids, *args)
    return {r["id"] for r in rows}


def attached_here(file_id: int, conversation_id: int | None) -> bool:
    if not conversation_id:
        return False
    return bool(db.one("SELECT 1 FROM messages WHERE conversation_id = ? AND role = 'user' AND instr(content, ?) > 0 LIMIT 1",
                       conversation_id, f"(file #{file_id})"))


def require(table: str, row_id: int, ctx) -> None:
    """Refuse an item outside this conversation's project boundary, in words the assistant can pass on."""
    pid = getattr(ctx, "project_id", None) if ctx else None
    if table == "files" and attached_here(row_id, getattr(ctx, "conversation_id", None)):
        return
    if not db.one(f"SELECT 1 FROM {table} WHERE id = ?", row_id):
        raise HTTPException(404, "Not found")
    if row_id not in allowed_ids(table, [row_id], pid):
        raise HTTPException(403, "Not available here: it belongs to another project that this conversation cannot see. "
                                 "Open that project to work with it.")


def filter_rows(table: str, rows: list[dict], ctx, key: str = "id") -> list[dict]:
    pid = getattr(ctx, "project_id", None) if ctx else None
    ok = allowed_ids(table, [r.get(key) for r in rows], pid)
    conv = getattr(ctx, "conversation_id", None) if ctx else None
    return [r for r in rows if r.get(key) in ok or (table == "files" and attached_here(r.get(key), conv))]
