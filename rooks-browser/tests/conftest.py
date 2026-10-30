import json
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))
SITE = ROOT / "site"


class SiteHandler(SimpleHTTPRequestHandler):
    """The local test shop, plus a fake SearXNG at /searx/search."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/searx/search":
            q = parse_qs(u.query).get("q", [""])[0]
            base = f"http://{self.headers['Host']}"
            body = json.dumps({"query": q, "results": [
                {"title": "Blue Widget · Corner Shop", "url": f"{base}/widget.html", "content": "Blue Widget $12.50, in stock"},
                {"title": "Products · Corner Shop", "url": f"{base}/products.html", "content": "All products"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        super().do_GET()


@pytest.fixture(scope="session")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SiteHandler, directory=str(SITE)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOKS_BROWSER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROOKS_BROWSER_NO_PROBE", "1")
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "WORKBENCH_URL", "ROOKS_BROWSER_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "home"


ENGINE = os.getenv("ROOKS_BROWSER_TEST_ENGINE", "auto")
