"""HTTP and server-sent events over the standard library, with the errors a person or a script can act on."""

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator

from . import API_VERSION, __version__

# Exit codes (documented in README.md and `rooks --help`)
OK, ERROR, USAGE, AUTH, NEEDS_APPROVAL, WORK_FAILED, UNREACHABLE, INCOMPATIBLE = 0, 1, 2, 3, 4, 5, 6, 7
TIMEOUT, INTERRUPTED = 124, 130


class Failure(Exception):
    """Something went wrong; code is the exit code, text says what to do."""

    def __init__(self, code: int, text: str, status: int | None = None):
        super().__init__(text)
        self.code, self.text, self.status = code, text, status


class Client:
    def __init__(self, url: str, token: str, timeout: float = 300):
        if not url:
            raise Failure(AUTH, "Not connected to a ROOKS server. Run: rooks connect http://your-server:3100")
        if not token:
            raise Failure(AUTH, "No terminal token. Run: rooks connect (the token is in ROOKS: Memory and skills, Assistant settings, Terminal access)")
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def _req(self, method: str, path: str, body=None, headers: dict | None = None, raw: bytes | None = None,
             content_type: str = "application/json") -> urllib.request.Request:
        h = {"Authorization": f"Bearer {self.token}", "X-Workbench": "1", "X-Rooks-API": str(API_VERSION),
             "User-Agent": f"rooks-cli/{__version__}", "Content-Type": content_type, **(headers or {})}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        return urllib.request.Request(self.url + path, data=data, method=method, headers=h)

    def _open(self, req: urllib.request.Request, timeout: float):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}").get("detail") or e.reason
            except ValueError:
                detail = e.reason
            if isinstance(detail, list):  # validation errors
                detail = "; ".join(str(d.get("msg", d)) for d in detail)
            if e.code == 401:
                raise Failure(AUTH, "The server did not accept the terminal token. Make a new one in ROOKS (Memory and skills, Assistant settings, "
                                    "Terminal access) and run: rooks connect", 401) from None
            if e.code == 426:
                raise Failure(INCOMPATIBLE, str(detail), 426) from None
            raise Failure(ERROR, f"{detail}", e.code) from None
        except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise Failure(TIMEOUT, f"{self.url} did not answer within {timeout:.0f} seconds.") from None
            raise Failure(UNREACHABLE, f"Cannot reach {self.url} ({reason}). Is ROOKS running, and is the address right?") from None

    def get(self, path: str, timeout: float | None = None):
        with self._open(self._req("GET", path), timeout or min(self.timeout, 60)) as r:
            return json.loads(r.read() or b"null")

    def post(self, path: str, body=None, timeout: float | None = None):
        with self._open(self._req("POST", path, body if body is not None else {}), timeout or min(self.timeout, 120)) as r:
            return json.loads(r.read() or b"null")

    def upload(self, name: str, data: bytes, mime: str) -> dict:
        req = self._req("POST", "/api/files?name=" + urllib.parse.quote(name), raw=data, content_type=mime)
        with self._open(req, 120) as r:
            return json.loads(r.read())

    def stream(self, method: str, path: str, body=None, headers: dict | None = None, deadline: float | None = None) -> Iterator[dict]:
        """Server-sent events, one dict each. Stops with TIMEOUT once the deadline passes."""
        resp = self._open(self._req(method, path, body, headers), min(self.timeout, 120))
        with resp:
            for raw in resp:
                if deadline and time.monotonic() > deadline:
                    raise Failure(TIMEOUT, "Timed out waiting for the answer. The work may still finish; see: rooks jobs")
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    try:
                        yield json.loads(line[5:])
                    except ValueError:
                        continue
