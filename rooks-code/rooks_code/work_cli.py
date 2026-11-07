"""`rooks work`: the app's professional workspaces from the terminal (same list, same rules as the app's Your work page).

  rooks work              list them; the active one is marked
  rooks work show         the active one and what it adds
  rooks work use ID       choose one (adds its pack through the app's security gate; Pro ones need Pro)

The interactive terminal's /work runs the same code (``text``).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .app_api import App, AppError, run


def _state(w: dict) -> str:
    if w.get("active"):
        return "in use"
    return "available" if w.get("allowed", True) else "needs Pro"


def listing(app: App) -> str:
    rows = app.call("GET", "/workspaces")
    out = [f"  {'*' if w.get('active') else ' '} {w['id']:8} {w['name']:20} {_state(w):10} {w.get('for', '')}" for w in rows]
    out.append("  Choose one with: rooks work use ID")
    return "\n".join(out)


def show(app: App) -> str:
    w = next((w for w in app.call("GET", "/workspaces") if w.get("active")), None)
    if not w:
        return "  General"
    out = [f"  {w['name']} ({w['id']}): {w.get('for', '')}"]
    if w.get("pack"):
        out.append(f"  Tools: {'added' if w.get('pack_added') else 'not added (rooks work use ' + w['id'] + ' adds them)'}")
    if w.get("starters"):
        out.append("  Try: " + "; ".join(w["starters"]))
    return "\n".join(out)


def use(app: App, wid: str) -> str:
    w = app.call("POST", f"/workspaces/{wid}")
    extra = " Its tools are added." if w.get("pack") and w.get("pack_added") else ""
    return f"  Now working as {w['name']}. Chat, the terminal and your chat apps all use it.{extra}"


def text(root: Path, rest: str) -> str:
    """/work in the interactive terminal: '' lists, 'show', or 'use ID' (or just 'ID')."""
    words = rest.split()
    try:
        app = App(root)
        if not words:
            return listing(app)
        if words[0] == "show":
            return show(app)
        wid = words[1] if words[0] == "use" and len(words) > 1 else words[0]
        if wid == "use":
            return "  /work use ID (see /work for the list)"
        return use(app, wid)
    except AppError as e:
        return f"  {e}"


def main(rest: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="rooks work", description="AI Workspace professional workspaces")
    ap.add_argument("-C", "--cwd", default=".")
    ap.add_argument("action", nargs="?", default="list", choices=("list", "show", "use"))
    ap.add_argument("workspace", nargs="?", help="workspace id (see rooks work)")
    a = ap.parse_args(rest)
    if a.action == "use" and not a.workspace:
        ap.error("rooks work use needs a workspace id")

    def go() -> int:
        app = App(Path(a.cwd).resolve())
        print(use(app, a.workspace) if a.action == "use" else show(app) if a.action == "show" else listing(app))
        return 0

    return run(go)
