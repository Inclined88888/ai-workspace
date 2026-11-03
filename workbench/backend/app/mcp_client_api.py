"""MCP servers API (/api/mcp/...): the user's own MCP servers that Workbench connects to (mcp_client.py).

Catalogue entries are the official reference servers with their documented npx/uvx commands; a placeholder value
("<...>") or a name under secret_env/secret_headers marks a value the server needs from the user, filled in the
add form and stored encrypted (never returned by any route). Secrets are stored via security.encrypt and are
redacted everywhere they could surface (public_row). Remote servers' traffic is in the egress log via httpx.
"""

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db, mcp_client
from .security import require_user

router = APIRouter(prefix="/api/mcp", dependencies=[Depends(require_user)])

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")

CATALOGUE = [
    {"name": "filesystem", "description": "Read and write files in folders you allow (official reference server).",
     "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "<allowed-folder>"],
     "env": {}, "placeholders": {"args": {"<allowed-folder>": "Folder the server may touch, e.g. /Users/you/work"}}},
    {"name": "github", "description": "GitHub repositories, issues and pull requests (official reference server).",
     "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
     "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": ""}, "secret_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
     "placeholders": {"env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "github.com/settings/tokens (repo scope)"}}},
    {"name": "fetch", "description": "Fetch a web page and read it as Markdown (official reference server).",
     "transport": "stdio", "command": "uvx", "args": ["mcp-server-fetch"], "env": {}},
    {"name": "memory", "description": "A knowledge graph the assistant can remember things in (official reference server).",
     "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"], "env": {}},
    {"name": "sqlite", "description": "Query and inspect one SQLite database (official reference server).",
     "transport": "stdio", "command": "uvx",
     "args": ["mcp-server-sqlite", "--db-path", "<database-file>"], "env": {},
     "placeholders": {"args": {"<database-file>": "Path to a .db or .sqlite file"}}},
    {"name": "playwright", "description": "Drive a real browser: click, fill, read pages (official reference server).",
     "transport": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp@latest"], "env": {}},
]


class ServerIn(BaseModel):
    name: str = Field(..., max_length=32)
    transport: str = "stdio"                      # stdio | streamable_http | sse
    command: str = Field("", max_length=200)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = Field("", max_length=500)
    headers: dict[str, str] = Field(default_factory=dict)
    secret_env: list[str] = Field(default_factory=list)     # env names whose values are stored encrypted
    secret_headers: list[str] = Field(default_factory=list)
    secrets: dict[str, dict[str, str]] = Field(default_factory=dict)  # {"env": {...}, "headers": {...}} values
    enabled: bool = True


def _validate(body: ServerIn) -> None:
    if not _NAME.match(body.name):
        raise HTTPException(400, "The server name must be 1-32 lowercase letters, digits, - or _ (used in tool names).")
    if body.transport == "stdio":
        if not body.command:
            raise HTTPException(400, "A stdio server needs a command.")
    else:
        if body.transport not in ("streamable_http", "sse"):
            raise HTTPException(400, "transport must be stdio, streamable_http or sse.")
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(400, "A remote server needs an http(s) URL.")


def _spec(body: ServerIn) -> dict:
    return {"command": body.command, "args": body.args, "env": body.env, "url": body.url, "headers": body.headers,
            "secret_env": body.secret_env, "secret_headers": body.secret_headers, "transport": body.transport,
            "enabled": body.enabled}


def _row(server_id: int) -> dict:
    row = db.one("SELECT * FROM mcp_servers WHERE id = ?", server_id)
    if not row:
        raise HTTPException(404, "No MCP server with that id.")
    return row


@router.get("/servers")
def list_servers():
    return {"servers": [mcp_client.public_row(r) for r in db.all("SELECT * FROM mcp_servers ORDER BY name")]}


@router.get("/catalogue")
def catalogue():
    return {"catalogue": CATALOGUE}


@router.post("/servers")
def add_server(body: ServerIn):
    _validate(body)
    if db.one("SELECT id FROM mcp_servers WHERE name = ?", body.name):
        raise HTTPException(409, f"There is already an MCP server named {body.name}.")
    sid = mcp_client.save_server(body.name, _spec(body), body.secrets)
    mcp_client.notify_change()
    return mcp_client.public_row(_row(sid))


@router.put("/servers/{server_id}")
def update_server(server_id: int, body: ServerIn):
    _row(server_id)
    _validate(body)
    clash = db.one("SELECT id FROM mcp_servers WHERE name = ? AND id != ?", body.name, server_id)
    if clash:
        raise HTTPException(409, f"There is already an MCP server named {body.name}.")
    old = _row(server_id)
    mcp_client.save_server(body.name, _spec(body), body.secrets, server_id=server_id)
    if old["enabled"] and not body.enabled:  # disabling closes the connection right away
        mcp_client.restart_all()
    mcp_client.notify_change()
    return mcp_client.public_row(_row(server_id))


@router.delete("/servers/{server_id}")
def remove_server(server_id: int):
    _row(server_id)
    db.run("DELETE FROM mcp_servers WHERE id = ?", server_id)
    mcp_client.notify_change()
    return {"removed": server_id}


@router.post("/servers/{server_id}/toggle")
def toggle_server(server_id: int, body: dict | None = None):
    row = _row(server_id)
    enabled = bool(body.get("enabled")) if body and "enabled" in body else not row["enabled"]
    db.run("UPDATE mcp_servers SET enabled = ? WHERE id = ?", int(enabled), server_id)
    mcp_client.notify_change()
    return mcp_client.public_row(_row(server_id))


@router.post("/servers/{server_id}/test")
async def test_server(server_id: int):
    """Connect (or reconnect) and list tools; the answer is the same shape connected_tools uses."""
    row = _row(server_id)
    if not row["enabled"]:
        raise HTTPException(400, "Enable the server before testing it.")
    mcp_client.restart(row["name"])

    def probe() -> dict:
        try:
            server = mcp_client._open(row)  # fresh one-off connection, does not disturb the registry
        except Exception as e:  # noqa: BLE001 - the point of the test is the error message
            return {"status": "error", "error": mcp_client._err_text(e), "tools": []}
        try:
            return {"status": "connected", "server_info": getattr(server, "server_info", {}),
                    "tools": [{"name": t["name"], "description": t.get("description", "")} for t in server.tools]}
        finally:
            server.close()

    result = await asyncio.to_thread(probe)
    db.run("UPDATE mcp_servers SET last_error = ? WHERE id = ?", result.get("error", ""), server_id)
    return result


@router.get("/tools")
def list_tools():
    """Tools of every enabled server, connecting on demand; failures appear per server, never blocking the rest."""
    return {"servers": mcp_client.connected_tools()}
