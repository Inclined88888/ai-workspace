"""rooks pack: manage AI Workspace packs from the terminal."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from . import config


class PackCLI:
    def __init__(self, settings, root: Path):
        self.s = settings
        self.root = root
        self.base_url = s.base_url.rstrip("/")
        self.api_key = s.api_key

    def run(self, argv: list[str]) -> int:
        ap = argparse.ArgumentParser(prog="rooks pack", description="Manage AI Workspace packs")
        ap.add_argument("action", nargs="?", choices=("list", "add", "remove", "info", "templates"))
        ap.add_argument("pack", nargs="?")
        args = ap.parse_args(argv)

        if not args.action:
            ap.print_help()
            return 0

        if args.action == "list":
            return self.list()
        if args.action == "info":
            if not args.pack:
                print("rooks pack info <pack_id>")
                return 1
            return self.info(args.pack)
        if args.action == "add":
            if not args.pack:
                print("rooks pack add <pack_id>")
                return 1
            return self.add(args.pack)
        if args.action == "remove":
            if not args.pack:
                print("rooks pack remove <pack_id>")
                return 1
            return self.remove(args.pack)
        if args.action == "templates":
            if not args.pack:
                print("rooks pack templates <pack_id>")
                return 1
            return self.templates(args.pack)
        return 1

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.api_key}", "X-Workbench": "1", "Content-Type": "application/json"}

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}/api/packs{path}"
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def _post(self, path: str, data: dict | None = None) -> dict:
        url = f"{self.base_url}/api/packs{path}"
        payload = json.dumps(data or {}).encode()
        req = urllib.request.Request(url, data=payload, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def _delete(self, path: str) -> dict:
        url = f"{self.base_url}/api/packs{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="DELETE")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def list(self) -> int:
        data = self._get("")
        print("Available packs:")
        for p in data:
            status = "installed" if p.get("installed") else "available"
            gate = p.get("gate", {})
            risk = f" ({gate.get('risk', '?')})" if gate else ""
            print(f"  {p['id']:12} {p['name']:20} [{status}{risk}]  {p.get('description', '')[:60]}")
        return 0

    def info(self, pack_id: str) -> int:
        data = self._get(f"/{pack_id}")
        print(f"Pack: {data['name']} ({data['id']})")
        print(f"  Version: {data.get('version', '?')}")
        print(f"  Description: {data.get('description', '')}")
        print(f"  Feature: {data.get('feature', '')}")
        installed = data.get("installed", False)
        print(f"  Installed: {'yes' if installed else 'no'}")
        if installed:
            print(f"  Installed at: {data.get('installed_at', '?')}")
            gate = data.get("gate", {})
            if gate:
                print(f"  Gate: risk={gate.get('risk', '?')}, permissions={gate.get('extra_permissions', [])}")
        print("  Skills:")
        for sk in data.get("skills", []):
            print(f"    - {sk['name']}: {sk.get('description', '')[:60]}")
        if data.get("templates"):
            print("  Templates:")
            for t in data["templates"]:
                print(f"    {t}")
        return 0

    def add(self, pack_id: str) -> int:
        try:
            result = self._post(f"/{pack_id}/add")
            print(f"Added pack: {result['name']} ({result['id']})")
            if result.get("gate"):
                print(f"  Gate: risk={result['gate'].get('risk', '?')}, permissions={result['gate'].get('extra_permissions', [])}")
            return 0
        except urllib.error.HTTPError as e:
            print(f"Error: {e.read().decode()}")
            return 1

    def remove(self, pack_id: str) -> int:
        try:
            result = self._delete(f"/{pack_id}/remove")
            print(f"Removed pack: {result['name']} ({result['id']})")
            return 0
        except urllib.error.HTTPError as e:
            print(f"Error: {e.read().decode()}")
            return 1

    def templates(self, pack_id: str) -> int:
        try:
            data = self._get(f"/{pack_id}")
            print(f"Templates in {data['name']}:")
            for t in data.get("templates", []):
                content = self._get(f"/{pack_id}/template/{t}")
                print(f"\n=== {t} ===")
                print(content[:500])
                if len(content) > 500:
                    print(f"... ({len(content)} chars total)")
            return 0
        except urllib.error.HTTPError as e:
            print(f"Error: {e.read().decode()}")
            return 1
