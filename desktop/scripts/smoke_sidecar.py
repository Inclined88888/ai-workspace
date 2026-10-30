"""Start a frozen workbench-server, check it answers like the desktop shell expects, stop it.

Usage: python desktop/scripts/smoke_sidecar.py <path to workbench-server[.exe]>
Exit code 0 means: /healthz is ok, the launch token opens a session that lands on /welcome,
a wrong token is refused, and the built frontend is served.
"""

import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def get(url: str, follow: bool = True):
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        r = opener.open(url, timeout=5)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def main(binary: str) -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    token = "smoke-" + "x" * 40
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as data:
        proc = subprocess.Popen([binary, "--port", str(port), "--data-dir", data, "--exit-with-parent"],
                                stdin=subprocess.PIPE, env={**__import__("os").environ, "WORKBENCH_DESKTOP_TOKEN": token})
        started = time.time()
        try:
            while True:
                try:
                    status, _, body = get(f"{base}/healthz")
                    if status == 200:
                        break
                except OSError:
                    pass
                if proc.poll() is not None:
                    print(f"FAIL: sidecar exited with {proc.returncode}")
                    return 1
                if time.time() - started > 120:
                    print("FAIL: no /healthz answer in 120 s")
                    return 1
                time.sleep(0.5)
            print(f"ok   /healthz after {time.time() - started:.1f}s: {body.decode()}")
            status, headers, _ = get(f"{base}/api/desktop/session?token=wrong", follow=False)
            assert status == 403, f"wrong token gave {status}"
            print("ok   wrong launch token refused (403)")
            status, headers, _ = get(f"{base}/api/desktop/session?token={token}", follow=False)
            assert status == 303 and headers.get("location") == "/welcome", (status, headers)
            assert "wb_session=" in headers.get("set-cookie", ""), "no session cookie"
            print("ok   launch token opens a session and lands on /welcome")
            status, _, body = get(f"{base}/welcome")
            assert status == 200 and b'<div id="root">' in body, "frontend not served"
            print("ok   frontend served")
            return 0
        finally:
            proc.stdin.close()  # --exit-with-parent: closing stdin is how the shell's exit looks to the sidecar
            try:
                proc.wait(timeout=10)
                print(f"ok   sidecar exited by itself when its stdin closed (code {proc.returncode})")
            except subprocess.TimeoutExpired:
                proc.kill()
                print("WARN sidecar did not exit on stdin close; killed")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
