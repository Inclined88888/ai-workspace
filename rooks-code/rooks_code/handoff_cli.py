"""rooks handoff: claim a web conversation from the terminal."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from . import config


class HandoffCLI:
    def __init__(self, settings, root: Path):
        self.s = settings
        self.root = root
        self.base_url = s.base_url.rstrip("/")
        self.api_key = s.api_key

    def run(self, argv: list[str]) -> int:
        ap = argparse.ArgumentParser(prog="rooks handoff", description="Claim a web conversation from the terminal")
        ap.add_argument("code", nargs="?", help="Handoff code from the web app")
        args = ap.parse_args(argv)

        if not args.code:
            print("Usage: rooks handoff <code>")
            print("")
            print("Get a handoff code from the web app (Conversation menu > Continue in Terminal)")
            print("Then run: rooks handoff <code>")
            return 1

        return self.claim(args.code)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.api_key}", "X-Workbench": "1", "Content-Type": "application/json"}

    def claim(self, code: str) -> int:
        url = f"{self.base_url}/v1/rooks/handoff?code={code}"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"Error: {e.read().decode()}")
            return 1

        cid = data.get("conversation_id")
        if not cid:
            print("Error: No conversation_id returned")
            return 1

        print(f"Claimed conversation #{cid}")
        print(f"To continue: rooks --session {cid}")
        return 0
