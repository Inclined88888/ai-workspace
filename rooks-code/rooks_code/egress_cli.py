"""`rooks egress`: what AI Workspace sent off this machine, per host (the app's egress ledger, egress.py)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .app_api import App, run


def main(rest: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="rooks egress", description="Outbound network audit of the AI Workspace app")
    ap.add_argument("-C", "--cwd", default=".")
    ap.add_argument("--hours", type=int, default=24, help="look back this many hours (1 to 2160, default 24)")
    ap.add_argument("--json", action="store_true", help="print the raw report")
    a = ap.parse_args(rest)
    if not 1 <= a.hours <= 2160:
        ap.error("--hours must be between 1 and 2160 (90 days)")

    def go() -> int:
        d = App(Path(a.cwd).resolve()).call("GET", "/egress", hours=a.hours)
        if a.json:
            print(json.dumps(d, indent=2))
            return 0
        verdict = "local only: nothing left this machine" if d["local_only"] else f"{d['external_requests']} request(s) left this machine"
        print(f"Egress, last {d['hours']}h: {verdict}")
        for h in d["hosts"]:
            fails = f", {h['failures']} failed" if h["failures"] else ""
            print(f"  {h['effect']:8} {h['host']:40} {h['requests']:>5} request(s){fails}  via {h['components'] or '?'}  last {h['last']}")
        if not d["hosts"]:
            print("  no outbound requests recorded")
        return 0

    return run(go)
