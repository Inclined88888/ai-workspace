"""`rooks pack`: the AI Workspace Packs gallery from the terminal: list, info, add, remove (same as the app's Packs page).
Adding a pack runs every skill in it through the app's security gate; all of them install, or none."""

from __future__ import annotations

import argparse
from pathlib import Path

from .app_api import App, run


def main(rest: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="rooks pack", description="AI Workspace packs")
    ap.add_argument("-C", "--cwd", default=".")
    ap.add_argument("action", nargs="?", default="list", choices=("list", "info", "add", "remove"))
    ap.add_argument("pack", nargs="?", help="pack id (see rooks pack list)")
    a = ap.parse_args(rest)
    if a.action != "list" and not a.pack:
        ap.error(f"rooks pack {a.action} needs a pack id")

    def go() -> int:
        app = App(Path(a.cwd).resolve())
        if a.action in ("add", "remove"):
            p = app.call("POST", f"/packs/{a.pack}/{a.action}")
            if a.action == "add":
                print(f"{p['name']}: {'already added' if p.get('already') else 'added'}")
                for name, g in (p.get("gate") or {}).items():
                    print(f"  {name}: risk {g.get('risk', '?')}" if isinstance(g, dict) else f"  {name}: {g}")
            else:
                print(f"{p['name']}: removed (your quotes, clauses and notes stay)")
            return 0
        packs = app.call("GET", "/packs")
        if a.action == "info":
            p = next((p for p in packs if p["id"] == a.pack), None)
            if not p:
                print(f"rooks: no pack called {a.pack!r}; try: {', '.join(x['id'] for x in packs)}")
                return 1
            print(f"{p['name']} ({p['id']}) {'added ' + str(p['installed_at']) if p['installed'] else 'not added'}")
            print(f"  {p.get('description', '')}")
            for sk in p.get("skills", []):
                print(f"  skill: {sk.get('name', sk.get('folder', '?'))}")
            return 0
        for p in packs:
            state = "added" if p["installed"] else ("available" if p.get("allowed", True) else "turned off")
            print(f"  {p['id']:8} {p['name']:24} {state:10} {p.get('description', '')[:70]}")
        return 0

    return run(go)
