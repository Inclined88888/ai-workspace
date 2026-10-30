"""The cross-platform half of install.sh / install.ps1. They fetch the source and `uv`, then run:

    uv run --no-project --python 3.12 desktop/launcher/install_helper.py --src <source tree> [options]

It installs, per user and without admin rights:
  * the app:          ~/.ai-workspace/app (backend, built web UI, sidecar, launcher) + ~/.ai-workspace/venv (Python 3.12)
  * `ai-workspace`:   the launch command, in ~/.local/bin (Windows: %USERPROFILE%\\.local\\bin), plus a Start menu,
                      Applications-folder or desktop-menu shortcut
  * `rooks`:          Rooks Code, the terminal coding agent (uv tool)
  * `rooks-browser`:  Rooks Browser, the AI browser agent (uv tool) and a browser for it: your Chrome or Edge, else
                      Playwright's Chromium
  * the native app:   only when a published GitHub release has one for this computer (--native auto|yes|no)

Run again to upgrade; data, keys and settings are kept (they live in the app's data folder, not here).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

WIN, MAC = sys.platform == "win32", sys.platform == "darwin"
NAME = "AI Workspace"


def say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def step(msg: str) -> None:
    print(f"\n\033[1m{msg}\033[0m" if not WIN else f"\n== {msg}", flush=True)


def run(cmd: list[str], **kw) -> None:
    subprocess.run(cmd, check=True, **kw)


def uv() -> str:
    return os.environ.get("UV") or shutil.which("uv") or "uv"


def copy_app(src: Path, home: Path) -> Path:
    """Copy what the app needs to run into home/app (replaced on upgrade)."""
    app = home / "app"
    tmp = home / "app.new"
    shutil.rmtree(tmp, ignore_errors=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "node_modules", ".pytest_cache", "bench")
    shutil.copytree(src / "workbench" / "backend", tmp / "workbench" / "backend", ignore=ignore)
    shutil.copytree(src / "workbench" / "frontend" / "dist", tmp / "workbench" / "frontend" / "dist")
    for d in ("sidecar", "launcher"):
        shutil.copytree(src / "desktop" / d, tmp / "desktop" / d, ignore=ignore)
    for f in ("VERSION", "LICENSE", "QUICKSTART.md"):
        if (src / f).exists():
            shutil.copy2(src / f, tmp / f)
    shutil.rmtree(app, ignore_errors=True)
    tmp.rename(app)
    return app


def ensure_frontend(src: Path) -> None:
    dist = src / "workbench" / "frontend" / "dist" / "index.html"
    if dist.exists():
        say("web UI: prebuilt")
        return
    npm = shutil.which("npm")
    if not npm:
        sys.exit("This copy has no prebuilt web UI (workbench/frontend/dist). Use the release zip, or install Node.js 20+ "
                 "(nodejs.org) and run the installer again.")
    say("web UI: building with npm (one minute)…")
    fe = src / "workbench" / "frontend"
    run([npm, "ci", "--no-audit", "--no-fund"], cwd=fe)
    run([npm, "run", "build"], cwd=fe)


def make_venv(home: Path, app: Path) -> Path:
    venv = home / "venv"
    if not venv.exists():
        run([uv(), "venv", "--quiet", "--python", "3.12", str(venv)])
    py = venv / ("Scripts/python.exe" if WIN else "bin/python")
    reqs = ["-r", str(app / "workbench" / "backend" / "requirements.txt"), "keyring==25.6.0"]
    run([uv(), "pip", "install", "--quiet", "--python", str(py), *reqs])
    return py


def install_tools(src: Path) -> None:
    for pkg in ("rooks-code", "rooks-browser"):
        run([uv(), "tool", "install", "--quiet", "--force", "--reinstall", "--python", "3.12", str(src / pkg)])
        say(f"{pkg}: installed")


def tool_python(tool: str) -> Path:
    d = Path(subprocess.run([uv(), "tool", "dir"], capture_output=True, text=True, check=True).stdout.strip())
    return d / tool / ("Scripts/python.exe" if WIN else "bin/python")


def ensure_browser(skip: bool) -> str:
    py = tool_python("rooks-browser")
    found = subprocess.run([str(py), "-c", "import json; from rooks_browser.browser import installed_browsers as f; print(json.dumps(f()))"],
                           capture_output=True, text=True)
    have = json.loads(found.stdout or "{}") if found.returncode == 0 else {}
    if have:
        name = "Google Chrome" if "chrome" in have else "Microsoft Edge"
        say(f"browser: using your {name}")
        return name
    if skip:
        say("browser: none found; skipped (--no-browser). Install Chrome or Edge later.")
        return ""
    say("browser: no Chrome or Edge found, downloading Chromium for Rooks Browser (about 170 MB)…")
    args = [str(py), "-m", "playwright", "install", "chromium"]
    if not (WIN or MAC) and os.geteuid() != 0 and shutil.which("sudo") and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        args.append("--with-deps")
    elif not (WIN or MAC) and os.geteuid() == 0:
        args.append("--with-deps")
    run(args)
    return "Chromium"


def write_launcher(bin_dir: Path, home: Path, app: Path, py: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = app / "desktop" / "launcher" / "ai_workspace.py"
    if WIN:
        target = bin_dir / "ai-workspace.cmd"
        target.write_text(f'@echo off\r\n"{py}" "{launcher}" %*\r\n', encoding="utf-8")
    else:
        target = bin_dir / "ai-workspace"
        target.write_text(f'#!/bin/sh\nexec "{py}" "{launcher}" "$@"\n')
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def write_shortcut(launcher: Path, app: Path, py: Path) -> str:
    """A clickable entry: Start menu (Windows), ~/Applications (macOS) or the desktop menu (Linux)."""
    icon = app / "workbench" / "frontend" / "dist" / "favicon.svg"
    try:
        if WIN:
            menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{NAME}.lnk"
            pyw = py.with_name("pythonw.exe")
            ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);$s.TargetPath=$env:PYW;"
                  "$s.Arguments='\"'+$env:LAUNCHER+'\"';$s.WorkingDirectory=$env:USERPROFILE;$s.Save()")
            env = {**os.environ, "LNK": str(menu), "PYW": str(pyw if pyw.exists() else py),
                   "LAUNCHER": str(app / "desktop" / "launcher" / "ai_workspace.py")}
            run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], env=env, capture_output=True)
            return str(menu)
        if MAC:
            bundle = Path.home() / "Applications" / f"{NAME}.app"
            if (Path("/Applications") / f"{NAME}.app").exists():
                return "(native app in /Applications)"
            macos = bundle / "Contents" / "MacOS"
            macos.mkdir(parents=True, exist_ok=True)
            (bundle / "Contents" / "Info.plist").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>'
                f"<key>CFBundleName</key><string>{NAME}</string><key>CFBundleExecutable</key><string>ai-workspace</string>"
                "<key>CFBundleIdentifier</key><string>com.inclined88888.aiworkspace.launcher</string>"
                "<key>CFBundlePackageType</key><string>APPL</string></dict></plist>\n")
            exe = macos / "ai-workspace"
            exe.write_text(f'#!/bin/sh\nexec "{launcher}" --web\n')
            exe.chmod(0o755)
            return str(bundle)
        apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        f = apps / "ai-workspace.desktop"
        f.write_text(f"[Desktop Entry]\nType=Application\nName={NAME}\nComment=Your AI assistant, coding agent and browser agent\n"
                     f"Exec={launcher}\nIcon={icon}\nTerminal=false\nCategories=Utility;Development;\n")
        return str(f)
    except (OSError, subprocess.CalledProcessError) as e:
        return f"(no shortcut: {e})"


def native_asset(repo: str) -> tuple[str, str] | None:
    """(name, url) of a published native installer for this computer, if any release has one."""
    arch = platform.machine().lower()
    if MAC:
        want = (lambda n: n.endswith(".dmg") and ("aarch64" in n if arch in ("arm64", "aarch64") else ("x64" in n or "x86_64" in n)))
    elif WIN:
        want = (lambda n: n.endswith("-setup.exe"))
    else:
        want = (lambda n: n.endswith(".AppImage") and ("amd64" in n or "x86_64" in n))
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=10",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "ai-workspace-installer"})
        with urllib.request.urlopen(req, timeout=15) as r:
            releases = json.load(r)
    except (OSError, ValueError):
        return None
    for rel in releases:
        for a in rel.get("assets", []):
            if want(a.get("name", "")):
                return a["name"], a["browser_download_url"]
    return None


def install_native(repo: str, home: Path) -> str:
    found = native_asset(repo)
    if not found:
        return ""
    name, url = found
    say(f"native app: downloading {name}…")
    tmp = Path(tempfile.mkdtemp())
    f = tmp / name
    urllib.request.urlretrieve(url, f)  # noqa: S310 - GitHub release asset over https
    if MAC:
        mnt = tmp / "mnt"
        run(["hdiutil", "attach", "-nobrowse", "-quiet", "-mountpoint", str(mnt), str(f)])
        try:
            src = next(mnt.glob("*.app"))
            dest_dir = Path("/Applications") if os.access("/Applications", os.W_OK) else Path.home() / "Applications"
            dest_dir.mkdir(exist_ok=True)
            shutil.rmtree(dest_dir / src.name, ignore_errors=True)
            run(["ditto", str(src), str(dest_dir / src.name)])
            return str(dest_dir / src.name)
        finally:
            subprocess.run(["hdiutil", "detach", "-quiet", str(mnt)])
    if WIN:
        run([str(f), "/S"])  # NSIS, per-user install (installMode currentUser): no admin prompt
        return "Start menu → AI Workspace"
    dest = home / "bin" / "AI-Workspace.AppImage"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, dest)
    dest.chmod(0o755)
    return str(dest)


def ensure_path(bin_dir: Path) -> str:
    parts = [os.path.normcase(os.path.normpath(p)) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if os.path.normcase(os.path.normpath(str(bin_dir))) in parts:
        return ""
    if WIN:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                cur, kind = winreg.QueryValueEx(k, "Path")
            except FileNotFoundError:
                cur, kind = "", winreg.REG_EXPAND_SZ
            if str(bin_dir).lower() not in cur.lower():
                winreg.SetValueEx(k, "Path", 0, kind, f"{cur};{bin_dir}" if cur else str(bin_dir))
        return "Open a new terminal window so the commands are found."
    line = f'export PATH="{bin_dir}:$PATH"  # added by the AI Workspace installer\n'
    shell = os.path.basename(os.environ.get("SHELL", ""))
    rcs = [Path.home() / ".zshrc"] if (MAC or shell == "zsh") else [Path.home() / ".bashrc"]
    if shell == "bash" and MAC:
        rcs.append(Path.home() / ".bash_profile")
    for rc in rcs:
        text = rc.read_text() if rc.exists() else ""
        if "added by the AI Workspace installer" not in text:
            with open(rc, "a") as fh:
                fh.write(("\n" if text and not text.endswith("\n") else "") + line)
    return f"Open a new terminal window (or run: export PATH=\"{bin_dir}:$PATH\") so the commands are found."


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True)
    p.add_argument("--home", default=os.environ.get("AI_WORKSPACE_HOME") or str(Path.home() / ".ai-workspace"))
    p.add_argument("--bin", default=os.environ.get("AI_WORKSPACE_BIN") or str(Path.home() / ".local" / "bin"))
    p.add_argument("--repo", default=os.environ.get("AI_WORKSPACE_REPO") or "Inclined88888/ai-workspace")
    p.add_argument("--native", choices=("auto", "yes", "no"), default=os.environ.get("AI_WORKSPACE_NATIVE", "auto"))
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-shortcut", action="store_true")
    a = p.parse_args()
    src, home, bin_dir = Path(a.src).resolve(), Path(a.home).expanduser(), Path(a.bin).expanduser()
    home.mkdir(parents=True, exist_ok=True)

    step("1/5  App")
    ensure_frontend(src)
    app = copy_app(src, home)
    py = make_venv(home, app)
    say(f"app: {app}")
    step("2/5  Rooks Code (rooks) and Rooks Browser (rooks-browser)")
    install_tools(src)
    step("3/5  A browser for Rooks Browser")
    browser = ensure_browser(a.no_browser)
    step("4/5  Launch command and shortcut")
    launcher = write_launcher(bin_dir, home, app, py)
    say(f"command: {launcher}")
    if not a.no_shortcut:
        say(f"shortcut: {write_shortcut(launcher, app, py)}")
    step("5/5  Native desktop app")
    native = ""
    if a.native != "no":
        native = install_native(a.repo, home)
    if native:
        say(f"native app: {native}")
    else:
        say("native app: no published installer for this computer yet; `ai-workspace` opens the app in its own "
            "Chrome/Edge app window instead (same app, same data).")
        if a.native == "yes":
            return 1
    hint = ensure_path(bin_dir)
    (home / "install.json").write_text(json.dumps({"src": str(src), "app": str(app), "python": str(py), "browser": browser,
                                                   "native": native, "bin": str(bin_dir)}, indent=1))
    print("\nDone. Start it with:\n\n    ai-workspace\n\nAlso installed:  rooks (coding agent)   rooks-browser (browser agent)")
    if hint:
        print(f"\n{hint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
