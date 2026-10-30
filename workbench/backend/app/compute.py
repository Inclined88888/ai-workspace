"""Run analysis code (pandas, numpy, matplotlib, sympy) in a local sandbox and show the results in the app.

The code gets copies of the files it was given in ./inputs (read only by convention), writes anything it makes to
./out, and has no network, no shell, no access to the rest of the computer and a time and memory limit. Layers are the
same as skill_sandbox: bubblewrap on Linux when available, a clean environment, OS limits, and an audit hook that blocks
network, programs and file access outside the work folder. Figures left open are saved as PNG automatically; files in
./out are added to the Files library as outputs.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import analysis, config, skill_sandbox

TIMEOUT = 90
MEMORY = 2 << 30

BOOT = r'''
import json, os, sys, io, runpy
_cfg = json.loads(os.environ.pop("WB_COMPUTE", "{}"))
_root = os.path.realpath(_cfg["root"]); _ok_read = tuple(os.path.realpath(p) for p in _cfg["read"])
_secret = ("/.ssh", "/.aws", "/.gnupg", "/.netrc", "/.config", "Keychains", "Login Data", "AppData")
def _inside(p): return p == _root or p.startswith(_root + os.sep)
def _hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        p = os.path.realpath(os.fsdecode(args[0])); mode = args[1] if len(args) > 1 else "r"
        w = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(mode, int) and bool(mode & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)))
        if w and not _inside(p) and not p.startswith(("/dev/null",)) and not p.startswith(tuple(_cfg["cache"])):
            raise PermissionError("The sandbox only writes inside its own folder: " + p)
        if not w and any(s in p for s in _secret) and not _inside(p) and not p.startswith(tuple(_cfg["cache"])):
            raise PermissionError("The sandbox cannot read private folders: " + p)
        if not w and not _inside(p) and not p.startswith(_ok_read) and not p.startswith(tuple(_cfg["cache"])):
            raise PermissionError("The sandbox can only read its input files: " + p)
    elif event in ("socket.getaddrinfo", "socket.connect", "socket.bind") :
        raise PermissionError("No network in the analysis sandbox")
    elif event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "ctypes.dlopen") and not (event == "ctypes.dlopen" and _cfg.get("allow_dlopen")):
        raise PermissionError("The analysis sandbox cannot start other programs")
os.environ["MPLBACKEND"] = "Agg"
try:
    import matplotlib; matplotlib.use("Agg")
except Exception: pass
for _m in ("numpy", "pandas", "sympy", "matplotlib.pyplot"):
    try: __import__(_m)
    except Exception: pass
sys.addaudithook(_hook)
_g = {"__name__": "__main__"}
try:
    import numpy as np, pandas as pd; _g.update(np=np, pd=pd)
except Exception: pass
try:
    import sympy as sp; _g["sp"] = sp
except Exception: pass
try:
    import matplotlib.pyplot as plt; _g["plt"] = plt
except Exception: plt = None
_err = None
try:
    exec(compile(open("main.py").read(), "analysis.py", "exec"), _g)
except SystemExit: pass
except BaseException as e:
    import traceback; _err = traceback.format_exc(limit=6)
if plt is not None:
    for i, n in enumerate(plt.get_fignums(), 1):
        plt.figure(n).savefig(os.path.join("out", f"figure-{i}.png"), dpi=120, bbox_inches="tight")
if _err:
    sys.stderr.write(_err); sys.exit(1)
'''


def _python() -> list[str]:
    """The interpreter that has pandas & co.: this app's own Python (a venv from the installer), or the frozen app."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-python"]
    return [sys.executable, "-I"]


def available() -> dict:
    found = {}
    for m in ("pandas", "numpy", "matplotlib", "sympy", "scipy"):
        try:
            __import__(m)
            found[m] = True
        except ImportError:
            found[m] = False
    return {"libraries": found, "isolation": skill_sandbox.isolation_layers()}


def run(code: str, file_ids: list[int] | None = None, timeout: int = TIMEOUT) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="wb-compute-"))
    work = tmp / "work"
    (work / "inputs").mkdir(parents=True)
    (work / "out").mkdir()
    inputs = []
    for fid in file_ids or []:
        f = analysis.get(fid)
        dst = work / "inputs" / Path(f["path"]).name
        if Path(f["path"]).is_dir():
            shutil.copytree(f["path"], dst)
        else:
            shutil.copy(f["path"], dst)
        inputs.append(f"inputs/{dst.name}")
    (work / "main.py").write_text(code)
    mpl = tmp / "mpl"
    mpl.mkdir()
    read_ok = [sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.dirname(os.path.realpath(sys.executable)), "/usr", "/lib", "/etc",
               "/System", "/Library/Fonts", "/opt", "/proc/self", "/dev", r"C:\Windows\Fonts"]
    read_ok += [p for p in sys.path if p and os.path.isdir(p)] + _link_dirs(sys.executable) + [os.path.realpath(sys.base_prefix)]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp), "TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp),
           "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "MPLCONFIGDIR": str(mpl), "OPENBLAS_NUM_THREADS": "2", "OMP_NUM_THREADS": "2",
           "WB_COMPUTE": json.dumps({"root": str(tmp), "read": read_ok, "cache": [str(mpl)], "allow_dlopen": True})}
    if os.name == "nt":
        env.update({k: os.environ[k] for k in ("SYSTEMROOT", "COMSPEC", "WINDIR") if k in os.environ})
    cmd = [*_python(), "-c", BOOT]
    full, layer = _wrap(cmd, work)

    def limits():
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))
        try:
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
        except (ValueError, OSError):
            pass
        os.setsid()

    try:
        p = subprocess.run(full, cwd=work, env=env, capture_output=True, text=True, timeout=timeout + 5, stdin=subprocess.DEVNULL,
                           preexec_fn=limits if os.name == "posix" else None)
        status = "ok" if p.returncode == 0 else "error"
        out, err = p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        status, out, err = "timeout", (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), \
            f"Stopped after {timeout} seconds."
    made = []
    for f in sorted((work / "out").rglob("*")):
        if f.is_file() and f.stat().st_size < 200 * 1024 * 1024:
            keep = analysis.outputs_dir() / f"{secrets.token_hex(3)}-{f.name}"
            shutil.move(str(f), keep)
            made.append(analysis._register_output(keep, f.name))
    shutil.rmtree(tmp, ignore_errors=True)
    return {"status": status, "isolation": layer, "inputs": inputs, "stdout": out[-20000:], "error": err[-6000:] if status != "ok" else "",
            "outputs": [{"id": m["id"], "name": m["name"], "url": f"/api/files/{m['id']}/download"} for m in made]}


def _wrap(cmd: list[str], work: Path) -> tuple[list[str], str]:
    if skill_sandbox._bwrap_ok():
        w = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-net"]
        for d in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/opt"):
            if Path(d).exists():
                w += ["--ro-bind", d, d]
        for d in {os.path.realpath(sys.base_prefix), sys.base_prefix, sys.prefix, *_link_dirs(sys.executable),
                  *[p for p in sys.path if p and os.path.isdir(p)]}:
            if Path(d).exists() and not d.startswith(("/usr", "/opt", str(work.parent))):
                w += ["--ro-bind", d, d]
        w += ["--proc", "/proc", "--dev", "/dev", "--bind", str(work.parent), str(work.parent), "--chdir", str(work)]
        return w + cmd, "bubblewrap (no network, private filesystem)"
    if sys.platform.startswith("linux") and shutil.which("unshare"):
        if subprocess.run(["unshare", "-rn", "true"], capture_output=True).returncode == 0:
            return ["unshare", "-rn", *cmd], "network namespace + audit hook"
    return cmd, "audit hook (no network, no programs, own folder only)"


def _link_dirs(exe: str) -> list[str]:
    """Every install folder a chain of interpreter symlinks passes through (uv and pyenv layouts)."""
    out, cur = [], exe
    for _ in range(8):
        if not os.path.islink(cur):
            break
        nxt = os.readlink(cur)
        cur = nxt if os.path.isabs(nxt) else os.path.join(os.path.dirname(cur), nxt)
        out.append(os.path.dirname(os.path.dirname(cur)))
    return out


def run_frozen(argv: list[str]) -> None:
    """Entry for the frozen desktop sidecar: `workbench-server --run-python -c BOOT`."""
    if len(argv) >= 2 and argv[0] == "-c":
        exec(compile(argv[1], "<sandbox>", "exec"), {"__name__": "__main__"})


_ = config
