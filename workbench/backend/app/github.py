"""Optional GitHub tools: search and read issues and pull requests, and (always after your approval) open an issue or
comment. Active only when a token is saved in Settings. Uses the REST API directly; no extra dependency."""

import os
import re

import httpx
from fastapi import HTTPException

from . import db
from .security import decrypt, encrypt

API = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def token() -> str:
    return decrypt(db.get_setting("secret:GITHUB_TOKEN", "") or "") or os.getenv("GITHUB_TOKEN", "")


def set_token(value: str) -> None:
    db.set_setting("secret:GITHUB_TOKEN", encrypt(value.strip()) if value.strip() else "")
    db.set_setting("github_check", "")


def _repo(repo: str) -> str:
    repo = (repo or "").strip().removeprefix("https://github.com/").strip("/")
    if not REPO_RE.match(repo):
        raise HTTPException(400, "repo must look like owner/name")
    return repo


async def _call(method: str, path: str, **kw) -> dict | list:
    if not token():
        raise HTTPException(400, "GitHub is not connected. Add a token in Settings, under Connections.")
    headers = {"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "ROOKS"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(method, API + path, headers=headers, **kw)
    if r.status_code == 401:
        raise HTTPException(400, "GitHub rejected the token. Paste a new one in Settings.")
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        raise HTTPException(429, "GitHub's rate limit is used up for now. Try again later.")
    if r.status_code in (403, 404):
        raise HTTPException(r.status_code, "GitHub says this does not exist or the token has no access to it.")
    if r.status_code >= 400:
        raise HTTPException(502, f"GitHub error {r.status_code}: {r.text[:200]}")
    return r.json()


async def check() -> dict:
    """Who the token belongs to, saved for the connection health list."""
    try:
        me = await _call("GET", "/user")
        out = {"ok": True, "login": me.get("login", "")}
    except HTTPException as e:
        out = {"ok": False, "error": str(e.detail)}
    import json

    db.set_setting("github_check", json.dumps(out))
    return out


async def search(query: str, limit: int = 10) -> list[dict]:
    r = await _call("GET", "/search/issues", params={"q": query[:256], "per_page": min(max(limit, 1), 20)})
    return [{"repo": i["repository_url"].split("/repos/")[-1], "number": i["number"], "title": i["title"], "state": i["state"],
             "pull_request": "pull_request" in i, "url": i["html_url"], "updated": i["updated_at"], "author": (i.get("user") or {}).get("login")}
            for i in r.get("items", [])]


async def read_issue(repo: str, number: int) -> dict:
    repo = _repo(repo)
    i = await _call("GET", f"/repos/{repo}/issues/{int(number)}")
    comments = await _call("GET", f"/repos/{repo}/issues/{int(number)}/comments", params={"per_page": 10})
    return {"repo": repo, "number": i["number"], "title": i["title"], "state": i["state"], "url": i["html_url"],
            "author": (i.get("user") or {}).get("login"), "labels": [lb["name"] for lb in i.get("labels", [])],
            "body": (i.get("body") or "")[:4000],
            "comments": [{"author": (c.get("user") or {}).get("login"), "body": (c.get("body") or "")[:800]} for c in comments]}


async def create_issue(repo: str, title: str, body: str = "") -> dict:
    i = await _call("POST", f"/repos/{_repo(repo)}/issues", json={"title": title[:250], "body": body[:20000]})
    return {"created": i["number"], "url": i["html_url"]}


async def comment(repo: str, number: int, body: str) -> dict:
    c = await _call("POST", f"/repos/{_repo(repo)}/issues/{int(number)}/comments", json={"body": body[:20000]})
    return {"commented": True, "url": c["html_url"]}


def tools(Tool, S, I) -> list:
    on = lambda ctx: bool(token())  # noqa: E731
    return [
        Tool("github_search", "Search GitHub issues and pull requests (GitHub search syntax, e.g. 'repo:owner/name is:open bug').",
             {"query": S()}, lambda query: search(query), ("query",), when=on),
        Tool("github_read_issue", "Read a GitHub issue or pull request with its latest comments.", {"repo": S("owner/name"), "number": I()},
             lambda repo, number: read_issue(repo, number), ("repo", "number"), when=on),
        Tool("github_create_issue", "Open a GitHub issue. Always waits for the user's approval.", {"repo": S("owner/name"), "title": S(), "body": S()},
             lambda repo, title, body="": create_issue(repo, title, body), ("repo", "title"), when=on),
        Tool("github_comment", "Comment on a GitHub issue or pull request. Always waits for the user's approval.",
             {"repo": S("owner/name"), "number": I(), "body": S()}, lambda repo, number, body: comment(repo, number, body),
             ("repo", "number", "body"), when=on),
    ]
