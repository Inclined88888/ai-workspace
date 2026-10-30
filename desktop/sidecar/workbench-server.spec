# PyInstaller spec: freezes Workbench (FastAPI + SQLite) and its built frontend into one executable.
# Build:  cd desktop/sidecar && pyinstaller --noconfirm workbench-server.spec   (after `npm run build` in workbench/frontend)
# Output: dist/workbench-server[.exe]; desktop/scripts/build-sidecar.* renames it with the Rust target triple for Tauri.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

REPO = Path(SPECPATH).resolve().parent.parent
BACKEND = REPO / "workbench" / "backend"
STATIC = REPO / "workbench" / "frontend" / "dist"
assert (STATIC / "index.html").exists(), "Build the frontend first: cd workbench/frontend && npm ci && npm run build"

datas = [(str(STATIC), "static")]
binaries = []
hiddenimports = collect_submodules("app") + collect_submodules("uvicorn")
# mcp.cli needs the optional typer extra and exits on import; the server side is all Workbench uses.
hiddenimports += collect_submodules("mcp", filter=lambda n: not n.startswith("mcp.cli"))
datas += collect_data_files("mcp")
for pkg in ("trafilatura", "justext", "htmldate", "courlan", "tzdata", "keyring", "icalendar",
            "recurring_ical_events", "caldav", "croniter", "certifi", "dateutil", "lxml"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(Path(SPECPATH) / "workbench_server.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "openai"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="workbench-server",
    console=True,  # stdout/stderr go to the shell's log file; Tauri hides the console window on Windows
    upx=False,
    target_arch=None,
    codesign_identity=None,  # Tauri signs external binaries when APPLE_SIGNING_IDENTITY is set
    entitlements_file=None,
)
