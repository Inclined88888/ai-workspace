import http.server
import threading
from pathlib import Path

import pytest

PAGE = """<!doctype html><html><head><title>Test Shop</title></head><body>
<h1>Parts</h1>
<a id="cpu" href="/cpu.html">CPUs</a>
<form action="/search.html"><input name="q"><button>Go</button></form>
</body></html>"""
CPU = "<!doctype html><title>CPU list</title><h1>Ryzen 7 9700X</h1>"
SEARCH = "<!doctype html><title>Results</title><p>results page</p>"


@pytest.fixture(scope="session")
def site(tmp_path_factory) -> str:
    root: Path = tmp_path_factory.mktemp("site")
    (root / "index.html").write_text(PAGE)
    (root / "cpu.html").write_text(CPU)
    (root / "search.html").write_text(SEARCH)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
