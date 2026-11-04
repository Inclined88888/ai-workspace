"""rooks egress: audit outbound network connections."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from . import config


class EgressCLI:
    def __init__(self, settings, root: Path):
        self.s = settings
        self.root = root
        self.base_url = s.base_url.rstrip("/")
        self.api_key = s.api_key

    def run(self, argv: list[str]) -> int:
        ap = argparse.ArgumentParser(prog="rooks egress", description="Audit outbound network connections")
        ap.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default 24, max 2160)")
        ap.add_argument("--json", action="store_true", help="Output raw JSON")
        args = ap.parse_args(argv)

        if args.hours > 2160:
            print("Error: max hours is 2160 (90 days)")
            return 1

        return self.summary(args.hours, args.json)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.api_key}", "X-Workbench": "1", "Content-Type": "application/json"}

    def summary(self, hours: int, as_json: bool) -> int:
        url = f"{self.base_url}/api/egress?hours={hours}"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"Error: {e.read().decode()}")
            return 1

        if as_json:
            print(json.dumps(data, indent=2))
            return 0

        print(f"Egress audit (last {data['hours']}h): {'LOCAL ONLY' if data['local_only'] else f'{data[\"external_requests\"]} external requests'}")
        print()

        if data["hosts"]:
            print("Per host:")
            for h in data["hosts"]:
                effect_marker = "🔒" if h["effect"] == "local" else "🌐"
                print(f"  {effect_marker} {h['host']:40} {h['effect']:8} {h['requests']:4} req  {h['failures']} fail  components: {h['components']}")
        else:
            print("No outbound connections recorded.")

        print()
        print("Recent (last 50):")
        for r in data["recent"]:
            marker = "🔒" if r["effect"] == "local" else "🌐"
            status = "✓" if r["outcome"] == "ok" else f"✗({r['outcome']})"
            print(f"  {marker} {r['ts'][:19]} {r['host']:30} {r['component']:20} {r['args'][:60]} {status} {r['latency_ms']}ms")

        return 0
