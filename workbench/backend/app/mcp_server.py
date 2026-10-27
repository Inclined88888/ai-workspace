"""MCP endpoint for ROOKS (mounted at /mcp/, bearer TOOLS_API_KEY).

Same operations as the Open WebUI tool server in tools.py, for MCP clients such as Hermes Agent and
OpenCode (Paperclip employees). Sending email is deliberately not exposed: agents can only save drafts.
"""

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from . import permissions, services, tools

mcp = MCPServer(
    name="workbench",
    instructions="The user's personal workspace: notes and documents, tasks, calendar, email search and drafts. "
    "Never claim an email was sent: save_email_draft only stores a draft the user reviews.",
)


def _allowed(name: str) -> None:
    try:
        permissions.check_external(name, "mcp")
    except HTTPException as e:
        raise ToolError(str(e.detail)) from e


def _call(name: str, fn, *args, **kwargs):
    _allowed(name)
    try:
        return fn(*args, **kwargs)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from e


async def _acall(name: str, fn, *args, **kwargs):
    _allowed(name)
    try:
        return await fn(*args, **kwargs)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from e


@mcp.tool(description="Search the user's notes and documents by keywords. kind: 'note', 'document' or empty for both.")
def search_notes(query: str = "", kind: str | None = None, limit: int = 10) -> list[dict]:
    return _call("search_notes", tools.search_notes, query, kind, limit)


@mcp.tool(description="Read the full text of a note or document by id.")
def read_note(doc_id: int) -> dict:
    return _call("read_note", tools.read_note, doc_id)


@mcp.tool(description="Create a note (kind 'note') or document (kind 'document'). Body is Markdown; tags comma separated.")
def create_note(title: str, body: str = "", kind: str = "note", tags: str = "") -> dict:
    return _call("create_note", tools.create_note, services.DocIn(title=title, body=body, kind=kind, tags=tags))


@mcp.tool(description="Append Markdown to the end of an existing note or document.")
def append_to_note(doc_id: int, text: str) -> dict:
    return _call("append_to_note", tools.append_note, doc_id, tools.AppendIn(text=text))


@mcp.tool(description="List open tasks (include_done=true to include completed), optionally for one project.")
def list_tasks(include_done: bool = False, project: str | None = None) -> list[dict]:
    return _call("list_tasks", tools.list_tasks, include_done, project)


@mcp.tool(description="Create a task. due is YYYY-MM-DD, priority 0 (none) to 3 (high).")
def create_task(title: str, notes: str = "", due: str | None = None, priority: int = 0, project: str = "") -> dict:
    return _call("create_task", tools.create_task, services.TaskIn(title=title, notes=notes, due=due, priority=priority, project=project))


@mcp.tool(description="Update a task. Set status to 'done' to complete it, 'doing' or 'todo' otherwise.")
def update_task(task_id: int, status: str | None = None, title: str | None = None, due: str | None = None,
                priority: int | None = None, notes: str | None = None) -> dict:
    return _call("update_task", tools.update_task, task_id, services.TaskPatch(status=status, title=title, due=due, priority=priority, notes=notes))


@mcp.tool(description="The user's agenda: upcoming events plus due and overdue tasks for the next N days.")
def get_agenda(days: int = 7) -> dict:
    return _call("get_agenda", tools.get_agenda, days)


@mcp.tool(description="List calendar events between two ISO 8601 times (UTC). Defaults to the next 7 days.")
def list_events(start: str | None = None, end: str | None = None) -> list[dict]:
    return _call("list_events", tools.list_events, start, end)


@mcp.tool(description="Create a calendar event. Timed events need an offset, e.g. 2026-05-01T09:00:00+10:00; all day events use YYYY-MM-DD.")
async def create_event(title: str, start: str, end: str | None = None, all_day: bool = False, location: str = "",
                       description: str = "", calendar_id: int | None = None) -> dict:
    return await _acall("create_event", tools.create_event, services.EventIn(title=title, start=start, end=end, all_day=all_day,
                                                             location=location, description=description, calendar_id=calendar_id))


@mcp.tool(description="Search recent email in the user's inbox.")
async def search_email(query: str = "", account_id: int | None = None) -> list[dict]:
    return await _acall("search_email", tools.search_email, query, account_id)


@mcp.tool(description="Read one email by account id and uid (from search_email).")
async def read_email(account_id: int, uid: str) -> dict:
    return await _acall("read_email", tools.read_email, account_id, uid)


@mcp.tool(description="Save an email draft for the user to review and send in ROOKS. This never sends email.")
def save_email_draft(to: str, subject: str, body: str, in_reply_to: str = "", account_id: int | None = None) -> dict:
    return _call("save_email_draft", tools.save_draft, tools.DraftIn(to=to, subject=subject, body=body, in_reply_to=in_reply_to, account_id=account_id))


mcp_app = mcp.streamable_http_app(
    streamable_http_path="/", stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
