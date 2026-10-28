"""Outside MCP servers: their tools join the assistant's.

Streamable HTTP servers work everywhere. Stdio servers launch a command inside the Workbench container, so a stolen
session could run programs there; they are off unless MCP_STDIO=true. Each call opens its own connection (simple and
robust; a stdio server starts once per call, which is slow for heavy servers).

Tool listings are cached in the database and refreshed on startup, on change and on demand, so building the tool list
for a turn never waits on the network. MCP tools return text written by whoever runs the server: they are treated as
untrusted and ask before running unless you allow them in Tool permissions.
"""

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager

from fastapi import HTTPException

from . import config, db
from .security import decrypt

log = logging.getLogger("mcp_client")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
CONNECT_TIMEOUT_S = 20
CALL_TIMEOUT_S = 120
RESULT_CHARS = 20000


def _secret_json(value: str) -> dict:
    try:
        d = json.loads(decrypt(value) or "{}")
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def tool_name(server: str, tool: str) -> str:
    """Function names must match ^[A-Za-z0-9_-]{1,64}$ for OpenAI compatible providers."""
    return f"mcp__{server}__{re.sub(r'[^A-Za-z0-9_-]', '_', tool)}"[:64]


@asynccontextmanager
async def _connect(row: dict):
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters
    from mcp.client.streamable_http import streamable_http_client
    import httpx2  # the HTTP client the MCP library is built on

    if row["transport"] == "stdio":
        if not config.MCP_STDIO:
            raise HTTPException(400, "Stdio MCP servers are off. Set MCP_STDIO=true to let Workbench launch commands.")
        params = StdioServerParameters(command=row["command"], args=json.loads(row["args"] or "[]"), env=_secret_json(row["env_enc"]) or None)
        async with Client(params) as c:
            yield c
        return
    # Same defaults as the library's own client: follow redirects, long reads for event streams
    async with httpx2.AsyncClient(headers=_secret_json(row["headers_enc"]), follow_redirects=True,
                                  timeout=httpx2.Timeout(30, read=300)) as http:
        async with Client(streamable_http_client(row["url"], http_client=http)) as c:
            yield c


def _message(e: BaseException) -> str:
    # anyio task groups wrap the real failure; show the innermost one
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return getattr(e, "detail", None) or str(e) or e.__class__.__name__


async def _list(row: dict) -> list[dict]:
    tools, cursor = [], None
    async with _connect(row) as c:
        for _ in range(20):  # pages
            res = await c.list_tools(cursor=cursor)
            for t in res.tools:
                schema = dict(t.input_schema or {})
                schema.pop("$schema", None)
                schema.setdefault("type", "object")
                schema.setdefault("properties", {})
                tools.append({"name": t.name, "description": (t.description or t.title or "")[:1000], "input_schema": schema})
            cursor = res.next_cursor
            if not cursor:
                break
    return tools


async def refresh(sid: int) -> dict:
    row = db.one("SELECT * FROM mcp_servers WHERE id = ?", sid)
    if not row:
        raise HTTPException(404, "MCP server not found")
    try:
        tools = await asyncio.wait_for(_list(row), CONNECT_TIMEOUT_S)
        db.run("UPDATE mcp_servers SET tools = ?, last_error = '', last_refreshed = ? WHERE id = ?", json.dumps(tools), db.now(), sid)
    except Exception as e:  # noqa: BLE001 - a broken server is reported on its card, never crashes Workbench
        msg = "Timed out connecting" if isinstance(e, asyncio.TimeoutError) else _message(e)
        db.run("UPDATE mcp_servers SET last_error = ?, last_refreshed = ? WHERE id = ?", str(msg)[:500], db.now(), sid)
    return db.one("SELECT * FROM mcp_servers WHERE id = ?", sid)


async def refresh_all() -> None:
    for row in db.all("SELECT id FROM mcp_servers WHERE enabled = 1"):
        await refresh(row["id"])


def _allowed(row: dict) -> set[str]:
    return {t.strip() for t in row["allowed_tools"].split(",") if t.strip()}


def tools() -> list:
    """Assistant tools for every enabled server, from the cached listings."""
    from .assistant import Tool

    out = []
    for row in db.all("SELECT * FROM mcp_servers WHERE enabled = 1 ORDER BY id"):
        allowed = _allowed(row)
        for t in json.loads(row["tools"] or "[]"):
            if allowed and t["name"] not in allowed:
                continue

            # Arguments arrive as one dict, so a model cannot reach the server id or tool name
            def run(arguments: dict, _sid=row["id"], _tool=t["name"]):
                return call(_sid, _tool, arguments)

            out.append(Tool(tool_name(row["name"], t["name"]), f"[{row['name']}] {t['description']}".strip(), {}, run,
                            schema=t["input_schema"], reads_untrusted=True, takes_dict=True))
    return out


async def call(sid: int, tool: str, arguments: dict) -> str:
    row = db.one("SELECT * FROM mcp_servers WHERE id = ? AND enabled = 1", sid)
    if not row:
        raise HTTPException(404, "That MCP server was removed or turned off")

    async def go():
        async with _connect(row) as c:
            return await c.call_tool(tool, arguments)

    try:
        res = await asyncio.wait_for(go(), CALL_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        raise HTTPException(504, f"{row['name']}: {tool} timed out") from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - connection and protocol failures go back to the model as text
        raise HTTPException(502, f"{row['name']}: {_message(e)}"[:500]) from e
    parts = []
    for item in res.content or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(item.text)
        elif kind == "resource" and getattr(getattr(item, "resource", None), "text", None):
            parts.append(item.resource.text)
        else:
            parts.append(f"[{kind or 'content'} not shown]")
    text = "\n".join(parts).strip()
    if not text and res.structured_content is not None:
        text = json.dumps(res.structured_content, ensure_ascii=False)
    if res.is_error:
        raise HTTPException(502, f"{row['name']}: {text or 'the tool reported an error'}"[:1000])
    return text[:RESULT_CHARS] or "(no output)"
