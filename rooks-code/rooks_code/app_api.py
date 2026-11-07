"""Small client for the AI Workspace app's terminal API (/v1/rooks/*), used by `rooks pack`, `rooks egress`, ...

Uses the same connection as chat (provider workbench: WORKBENCH_URL, the Terminal access token from `rooks login`).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config


class AppError(Exception):
    pass


class App:
    def __init__(self, root: Path):
        s = config.resolve({"provider": "workbench"}, root)
        if not s.base_url or not s.api_key:
            raise AppError("not connected to AI Workspace: run `rooks login` and choose the AI Workspace server")
        self.base = s.base_url.rstrip("/")  # ends in /v1
        self.hdr = {"Authorization": f"Bearer {s.api_key}", "Content-Type": "application/json"}

    def call(self, method: str, path: str, body: dict | None = None, **query) -> dict | list:
        q = f"?{urllib.parse.urlencode(query)}" if query else ""
        req = urllib.request.Request(f"{self.base}/rooks{path}{q}", method=method, headers=self.hdr,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail")
            except (ValueError, AttributeError):
                detail = None
            if isinstance(detail, dict):  # the app's upgrade card (402) and similar: show its plain message
                detail = detail.get("message") or detail.get("error")
            if e.code == 401:
                detail = "the app refused the Terminal access token: make a new one in Settings > Terminal access, then `rooks login`"
            raise AppError(str(detail or f"HTTP {e.code}")) from None
        except (urllib.error.URLError, OSError) as e:
            raise AppError(f"cannot reach AI Workspace at {self.base}: {getattr(e, 'reason', e)}") from None


def run(fn) -> int:
    """Run fn(); print an AppError on stderr as one line and return 1."""
    try:
        return fn() or 0
    except AppError as e:
        print(f"rooks: {e}", file=sys.stderr)
        return 1
