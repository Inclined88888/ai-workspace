"""After install.sh / install.ps1: start `ai-workspace --no-open`, wait for it to answer, check the agents are on
PATH-able locations, then `ai-workspace --stop`. Used by the CI "One-command install" job on Linux, macOS and Windows."""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WIN = sys.platform == "win32"
BIN = Path(os.environ.get("AI_WORKSPACE_BIN") or Path.home() / ".local" / "bin")


def tool(name: str) -> str:
    for cand in (BIN / f"{name}.cmd", BIN / f"{name}.exe", BIN / name):
        if cand.exists():
            return str(cand)
    sys.exit(f"{name} was not installed in {BIN}")


def main() -> int:
    launcher = tool("ai-workspace")
    print(subprocess.run([launcher, "--version"], capture_output=True, text=True, check=True).stdout.strip())
    for name in ("rooks", "rooks-browser"):
        out = subprocess.run([tool(name), "--version" if name == "rooks" else "--help"], capture_output=True, text=True)
        print(f"{name}: exit {out.returncode}")
        assert out.returncode == 0, out.stderr
    proc = subprocess.Popen([launcher, "--no-open"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    state = Path.home() / ".ai-workspace" / "launcher.json"
    deadline = time.monotonic() + 180
    url = None
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            url = json.loads(state.read_text())["url"]
            with urllib.request.urlopen(url + "/healthz", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(1)
    else:
        print(proc.stdout.read() if proc.poll() is not None else "(still starting)")
        sys.exit("the app did not answer /healthz within 180 s")
    print(f"app answered at {url}")
    print(subprocess.run([launcher, "--status"], capture_output=True, text=True).stdout)
    subprocess.run([launcher, "--stop"], check=True)
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
