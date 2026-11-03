#!/usr/bin/env python3
"""Build the Tauri v2 updater manifest (latest.json) from the signed installers in a folder.

Usage: make_latest_json.py <dir> <version> <repo> [notes_file]

The desktop workflow collects every installer plus its `.sig` into one folder. This maps each signed updater
bundle to a Tauri platform key and points its URL at the GitHub release asset of the same name.
Exits non-zero when no signed bundle is found, so a release never ships an empty manifest.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Callable

# (platform key, filename predicate). First match per key wins; order matters for Windows (MSI preferred).
RULES: list[tuple[str, Callable[[str], bool]]] = [
    ("darwin-aarch64", lambda n: n.endswith(".app.tar.gz") and "aarch64" in n),
    ("darwin-x86_64", lambda n: n.endswith(".app.tar.gz") and ("x86_64" in n or "x64" in n)),
    ("windows-x86_64", lambda n: n.endswith(".msi")),
    ("windows-x86_64-nsis", lambda n: n.endswith("-setup.exe")),
    ("linux-x86_64", lambda n: n.endswith(".AppImage")),
]


def build(folder: Path, version: str, repo: str, notes: str = "") -> dict:
    tag = version if version.startswith("v") else f"v{version}"
    platforms: dict[str, dict[str, str]] = {}
    for f in sorted(folder.iterdir()):
        sig = f.with_name(f.name + ".sig")
        if not f.is_file() or not sig.is_file():
            continue
        for key, match in RULES:
            if key not in platforms and match(f.name):
                url = f"https://github.com/{repo}/releases/download/{tag}/{urllib.parse.quote(f.name)}"
                platforms[key] = {"signature": sig.read_text().strip(), "url": url}
                break
    if not platforms:
        raise SystemExit(f"no signed updater bundles in {folder}")
    if "windows-x86_64" not in platforms and "windows-x86_64-nsis" in platforms:
        platforms["windows-x86_64"] = platforms["windows-x86_64-nsis"]
    return {
        "version": tag.lstrip("v"),
        "notes": notes,
        "pub_date": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platforms": platforms,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2
    folder, version, repo = Path(argv[1]), argv[2], argv[3]
    notes = Path(argv[4]).read_text() if len(argv) > 4 else ""
    manifest = build(folder, version, repo, notes)
    (folder / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(sorted(manifest["platforms"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
