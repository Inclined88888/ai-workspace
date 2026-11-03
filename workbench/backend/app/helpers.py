"""Optional helper apps for the Files page: Tesseract (scanned pages), FFmpeg (video details), LibreOffice (old Office
files). The Files page says plainly which are missing and offers one Install button, which runs the same quiet
brew / winget install the installer offers. Nothing here ever blocks reading files: without a helper, files are read
with less detail."""

import platform
import shutil
import subprocess
import threading
from pathlib import Path

HELPERS = {
    "tesseract": {"label": "Tesseract", "why": "reads scanned pages and photos of text", "brew": ["brew", "install", "--quiet", "tesseract"],
                  "winget": "UB-Mannheim.TesseractOCR", "apt": "tesseract-ocr"},
    "ffmpeg": {"label": "FFmpeg", "why": "reads video length and details", "brew": ["brew", "install", "--quiet", "ffmpeg"],
               "winget": "Gyan.FFmpeg", "apt": "ffmpeg"},
    "libreoffice": {"label": "LibreOffice", "why": "opens old .doc, .xls and .ppt files", "brew": ["brew", "install", "--cask", "--quiet", "libreoffice"],
                    "winget": "TheDocumentFoundation.LibreOffice", "apt": "libreoffice"},
}

_state = {"running": False, "log": [], "error": ""}
_lock = threading.Lock()


def present(key: str) -> bool:
    if key == "tesseract":
        return bool(shutil.which("tesseract") or Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists())
    if key == "ffmpeg":
        return bool(shutil.which("ffprobe"))
    return bool(shutil.which("soffice") or shutil.which("libreoffice") or Path("/Applications/LibreOffice.app").exists()
                or Path(r"C:\Program Files\LibreOffice\program\soffice.exe").exists())


def _system() -> str:
    return platform.system()


def commands(keys: list[str]) -> list[list[str]]:
    """The install commands for these helpers on this computer; [] when there is no installer we can run without a password."""
    sysname = _system()
    if sysname == "Darwin" and shutil.which("brew"):
        return [HELPERS[k]["brew"] for k in keys]
    if sysname == "Windows" and shutil.which("winget"):
        return [["winget", "install", "--id", HELPERS[k]["winget"], "-e", "--silent", "--accept-package-agreements",
                 "--accept-source-agreements", "--disable-interactivity"] for k in keys]
    return []


def manual_hint(keys: list[str]) -> str:
    sysname = _system()
    if sysname == "Darwin":
        return "Install Homebrew (brew.sh), then press Install again."
    if sysname == "Windows":
        return "Install 'App Installer' from the Microsoft Store (it adds winget), then press Install again."
    return "In a terminal, run:  sudo apt install " + " ".join(HELPERS[k]["apt"] for k in keys)


def status() -> dict:
    missing = [k for k in HELPERS if not present(k)]
    return {"missing": [{"key": k, "label": HELPERS[k]["label"], "why": HELPERS[k]["why"]} for k in missing],
            "can_install": bool(commands(missing)) if missing else False, "hint": manual_hint(missing) if missing else "",
            "installing": _state["running"], "log": list(_state["log"]), "error": _state["error"]}


def _run(cmds: list[list[str]], keys: list[str]) -> None:
    try:
        for key, cmd in zip(keys, cmds):
            label = HELPERS[key]["label"]
            _state["log"].append(f"Installing {label}…")
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
                ok = r.returncode == 0 or present(key)
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            _state["log"].append(f"{label} installed." if ok else f"{label} could not be installed; skipped.")
    finally:
        _state["running"] = False


def install(wait: bool = False) -> dict:
    with _lock:
        if _state["running"]:
            return status()
        missing = [k for k in HELPERS if not present(k)]
        cmds = commands(missing)
        _state.update(log=[], error="")
        if not missing:
            return status()
        if not cmds:
            _state["error"] = manual_hint(missing)
            return status()
        _state["running"] = True
        t = threading.Thread(target=_run, args=(cmds, missing), daemon=True)
        t.start()
    if wait:
        t.join()
    return status()
