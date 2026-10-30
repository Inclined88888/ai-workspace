"""Run a skill's helper scripts in isolation: for the trial run before install, and at run time with granted permissions.

Layers, strongest available first:
1. Linux: bubblewrap (`bwrap`) gives the script a private filesystem view (system folders read-only, its own copy of
   the skill read-write, no home folder, no secrets) and no network unless network was granted. `unshare -rn` is the
   fallback for network isolation when bwrap is missing.
2. Every platform: a clean environment (no API keys, HOME pointed at the scratch folder), a time limit, CPU and memory
   limits where the OS supports them.
3. Python scripts additionally run under an audit hook that checks every connect, DNS lookup, file open and subprocess
   against the grants, blocks the ones outside them and reports each attempt. This is what turns an out-of-grant action
   into an approval request instead of a silent failure.
macOS (sandbox-exec profiles) and Windows (AppContainer / job objects) are documented in docs/skills-security.md and
are not wired into the desktop app yet; there, layers 2 and 3 apply.
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

TIMEOUT = 20
PYTHON = os.path.realpath(sys.executable)  # the base interpreter: scripts get the standard library, not our packages
NET_SHIMS = ("curl", "wget", "nc", "ncat", "ssh", "scp", "ftp", "telnet")
PERSIST_SHIMS = ("crontab", "launchctl", "systemctl", "schtasks", "sudo")

# Installed into the child before the script runs. Reads its config from an env var it then deletes.
BOOTSTRAP = r'''
import json, os, sys, runpy
_cfg = json.loads(os.environ.pop("SKILL_SANDBOX", "{}"))
_tag = _cfg["tag"]; _root = os.path.realpath(_cfg["root"]); _net = set(_cfg["net"]); _shell = _cfg["shell"]
_files = [os.path.realpath(os.path.expanduser(f)) for f in _cfg["files"]]
_secret = ("/.ssh", "/.aws", "/.gnupg", "/.netrc", "/.config/gh", "/.npmrc", "/.pypirc", "/.kube", "/.docker/config.json", "Keychains", "Login Data")
_sys_ro = ("/usr", "/lib", "/lib64", "/bin", "/etc", "/opt", "/proc", "/dev", "/System", "/Library/Frameworks", sys.prefix, sys.base_prefix)
_stderr = sys.stderr
def _report(kind, what, target=""):
    _stderr.write(_tag + json.dumps({"kind": kind, "what": what, "target": target}) + "\n"); _stderr.flush()
def _allowed_path(p):
    return p == _root or p.startswith(_root + os.sep) or any(p == f or p.startswith(f.rstrip("/") + os.sep) for f in _files)
def _hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0]); p = os.path.realpath(path); mode = args[1] if len(args) > 1 else "r"
        writing = isinstance(mode, str) and any(c in mode for c in "wax+")
        if isinstance(mode, int): writing = bool(mode & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
        if any(s in p for s in _secret) and not _allowed_path(p):
            _report("secret-read", f"read a private file ({path})", path); raise PermissionError("blocked by skill sandbox: " + path)
        if writing and not _allowed_path(p) and not p.startswith(("/dev/null", "/dev/tty")):
            _report("write-outside", f"write outside its folder ({path})", path); raise PermissionError("blocked by skill sandbox: " + path)
        if not writing and not _allowed_path(p) and not p.startswith(_sys_ro) and "/site-packages/" not in p and "/dist-packages/" not in p:
            _report("read-outside", f"read outside its folder ({path})", path); raise PermissionError("blocked by skill sandbox: " + path)
    elif event == "socket.getaddrinfo":
        host = str(args[0])
        if host not in _net and not any(host.endswith("." + d) for d in _net):
            _report("network", f"connect to {host}", host); raise PermissionError("blocked by skill sandbox: network " + host)
    elif event == "socket.connect":
        addr = args[1]
        if isinstance(addr, tuple) and not _net:
            _report("network", f"connect to {addr[0]}:{addr[1]}", str(addr[0])); raise PermissionError("blocked by skill sandbox: network")
    elif event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn") and not _shell:
        _report("shell", f"run a program ({str(args[0])[:80] if args else event})", "shell"); raise PermissionError("blocked by skill sandbox: shell")
sys.addaudithook(_hook)
sys.argv = _cfg["argv"]
runpy.run_path(sys.argv[0], run_name="__main__")
'''

NET_ERRORS = ("Could not resolve host", "Temporary failure in name resolution", "Network is unreachable", "getaddrinfo ENOTFOUND",
              "Name or service not known", "nodename nor servname")


def isolation_layers() -> list[str]:
    layers = ["clean environment", "time and memory limits", "Python audit hook"]
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        layers.insert(0, "bubblewrap filesystem + network isolation")
    elif sys.platform.startswith("linux") and shutil.which("unshare"):
        layers.insert(0, "network namespace (unshare)")
    return layers


def _limits():  # runs in the child before exec (POSIX only)
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    except (ValueError, OSError):
        pass
    os.setsid()


def _bwrap_ok() -> bool:
    if not (sys.platform.startswith("linux") and shutil.which("bwrap")):
        return False
    try:
        return subprocess.run(["bwrap", "--ro-bind", "/", "/", "--unshare-net", "true"], capture_output=True, timeout=5).returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _wrap(cmd: list[str], work: Path, network: bool) -> tuple[list[str], str]:
    if _bwrap_ok():
        w = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
        if not network:
            w.append("--unshare-net")
        for d in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/opt"):
            if Path(d).exists():
                w += ["--ro-bind", d, d]
        for d in {os.path.realpath(sys.base_prefix)}:  # the Python running us may live outside /usr (uv, pyenv)
            if Path(d).exists() and not d.startswith(("/usr", "/opt")):
                w += ["--ro-bind", d, d]
        w += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--bind", str(work.parent), str(work.parent), "--chdir", str(work)]
        return w + cmd, "bubblewrap"
    if not network and sys.platform.startswith("linux") and shutil.which("unshare"):
        if subprocess.run(["unshare", "-rn", "true"], capture_output=True).returncode == 0:
            return ["unshare", "-rn", *cmd], "unshare"
    return cmd, "process"


def run(skill_dir: Path, script: str, args: list[str] | None = None, grants: dict | None = None, copy: bool = True) -> dict:
    """Run one helper script. grants: {"network": [domains], "files": [paths], "shell": bool}. Never raises for script errors."""
    grants = grants or {}
    args = args or []
    tmp = Path(tempfile.mkdtemp(prefix="skill-sandbox-"))
    work = tmp / "skill"
    if copy:
        shutil.copytree(skill_dir, work, symlinks=False, ignore=shutil.ignore_patterns(".git"))
    else:
        work = skill_dir
    target = (work / script).resolve()
    if not str(target).startswith(str(work.resolve()) + os.sep) or not target.is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        return {"script": script, "status": "error", "exit": None, "stdout": "", "stderr": "script not found inside the skill", "violations": []}
    tag = "@@SANDBOX-" + secrets.token_hex(8) + "@@"
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(tmp), "TMPDIR": str(tmp), "LANG": "C.UTF-8", "SKILL_DIR": str(work),
           "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
    if os.name == "nt":
        env.update({k: os.environ[k] for k in ("SYSTEMROOT", "COMSPEC") if k in os.environ})
    # Shims report a network or start-up tool being called; isolation blocks the real thing either way.
    shim_dir = tmp / ".shims"
    shim_dir.mkdir()
    shims = [(n, "persistence", f"use {n} to change system start-up or admin settings") for n in PERSIST_SHIMS]
    if not grants.get("network"):
        shims += [(n, "network", f"use {n} to reach the internet") for n in NET_SHIMS]
    for name, kind, what in shims:
        line = json.dumps({"kind": kind, "what": what, "target": ""}).replace('"', '\\"')
        f = shim_dir / name
        f.write_text(f'#!/bin/sh\necho "{tag}{line}" >&2\nexit 6\n')
        f.chmod(0o755)
    env["PATH"] = f"{shim_dir}:{env['PATH']}"
    ext = target.suffix.lower()
    first = target.read_bytes()[:128].split(b"\n", 1)[0]  # shebang line only
    if ext == ".py" or (first.startswith(b"#!") and b"python" in first):
        env["SKILL_SANDBOX"] = json.dumps({"tag": tag, "root": str(work), "net": grants.get("network") or [], "shell": bool(grants.get("shell")),
                                           "files": grants.get("files") or [], "argv": [str(target), *args]})
        cmd = [PYTHON, "-I", "-c", BOOTSTRAP]
    elif ext in (".sh", ".bash") or first.startswith(b"#!/bin/sh") or first.startswith(b"#!/bin/bash") or first.startswith(b"#!/usr/bin/env bash"):
        cmd = ["bash", str(target), *args]
    elif ext in (".js", ".mjs", ".cjs") and shutil.which("node"):
        cmd = [shutil.which("node"), "--disallow-code-generation-from-strings", str(target), *args]
    else:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"script": script, "status": "skipped", "exit": None, "stdout": "", "stderr": f"no runner for {target.name}", "violations": []}
    full, layer = _wrap(cmd, work, network=bool(grants.get("network")))
    violations: list[dict] = []
    try:
        p = subprocess.run(full, cwd=work, env=env, capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL,
                           preexec_fn=_limits if os.name == "posix" else None)
        status, code, out, err = ("ok" if p.returncode == 0 else "failed"), p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        status, code = "timeout", None
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
    clean_err = []
    for line in err.splitlines():
        if line.startswith(tag):
            try:
                v = json.loads(line[len(tag):])
                # Report paths as the user would recognise them: the sandbox's fake home is shown as ~
                for k in ("what", "target"):
                    v[k] = str(v.get(k, "")).replace(str(work), "<skill>").replace(str(tmp), "~")
                if v not in violations:
                    violations.append(v)
            except ValueError:
                pass
        else:
            clean_err.append(line)
    err = "\n".join(clean_err)
    if not grants.get("network") and any(s in err or s in out for s in NET_ERRORS):
        violations.append({"kind": "network", "what": "reach the internet (blocked by the sandbox)", "target": ""})
    if "Read-only file system" in err:
        violations.append({"kind": "write-outside", "what": "write to a protected system folder", "target": ""})
    for f in sorted(tmp.rglob("*")):  # anything left in the fake home folder is a write outside the skill
        rel = f.relative_to(tmp)
        if f.is_file() and rel.parts[0] not in ("skill", ".shims"):
            violations.append({"kind": "write-outside", "what": f"write to your home folder (~/{rel})", "target": f"~/{rel}"})
    written = sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.is_file() and not (skill_dir / p.relative_to(work)).exists()) if copy else []
    shutil.rmtree(tmp, ignore_errors=True)
    return {"script": script, "status": status, "exit": code, "isolation": layer, "stdout": out[-4000:], "stderr": err[-4000:],
            "violations": violations, "created_files": written[:50]}


def trial(skill_dir: Path, scripts: list[str], meta: dict) -> dict:
    """Trial run before install: no network, no shell, no files outside the skill. Runs the declared test command if the
    skill has one (frontmatter `test: scripts/x.py --flag`), otherwise each helper script with --help."""
    plan: list[tuple[str, list[str]]] = []
    test = meta.get("test") if isinstance(meta.get("test"), str) else ""
    if test:
        parts = test.split()
        plan.append((parts[0], parts[1:]))
    else:
        plan += [(s, ["--help"]) for s in scripts[:6]]
    results = [run(skill_dir, s, a, grants={}) for s, a in plan]
    violations = [v for r in results for v in r["violations"]]
    status = "nothing to run" if not results else ("timeout" if any(r["status"] == "timeout" for r in results) else
                                                 "blocked actions" if violations else "passed")
    return {"status": status, "runs": results, "violations": violations, "layers": isolation_layers()}
