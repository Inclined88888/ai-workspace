"""rooks redact: scan and test secret redaction patterns."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import redact


class RedactCLI:
    def __init__(self, root: Path):
        self.root = root

    def run(self, argv: list[str]) -> int:
        ap = argparse.ArgumentParser(prog="rooks redact", description="Test and scan secret redaction")
        ap.add_argument("action", nargs="?", choices=("test", "scan", "patterns"), default="test")
        ap.add_argument("path", nargs="?", help="File or directory to scan (for scan action)")
        ap.add_argument("--json", action="store_true", help="Output raw JSON")
        args = ap.parse_args(argv)

        if args.action == "test":
            return self.test()
        if args.action == "scan":
            if not args.path:
                print("Usage: rooks redact scan <path>")
                return 1
            return self.scan(Path(args.path), args.json)
        if args.action == "patterns":
            return self.show_patterns(args.json)
        return 1

    def test(self) -> int:
        """Run the built-in test cases for redaction patterns."""
        test_cases = [
            # (input, expected_contains, expected_not_contains)
            ("Authorization: Bearer sk-ant-abc123", "<redacted>", "sk-ant-"),
            ("Authorization: Bearer AIzaSyB12345678901234567890123456789", "<redacted:key>", "AIzaSy"),
            ("Cookie: sessionid=abc123; other=value", "<redacted>", "abc123"),
            ("https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getMe", "/bot<redacted>", "AAHdqTcv"),
            ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0", "<redacted:jwt>", "eyJhbGci"),
            ("api_key = 'sk-proj-abcdefghijklmnopqrstuvwxyz123456'", "<redacted>", "sk-proj-"),
            ("password=mysecretpassword123", "<redacted>", "mysecret"),
            ("token = 'abcdefghijklmnopqrstuvwxyz'", "<redacted>", "abcdefgh"),
            ("user@example.com", "<email>", "user@example.com"),
            ("+1-555-123-4567", "<phone>", "+1-555"),
            ("(555) 123-4567", "<phone>", "555)"),
        ]

        print("Running redaction tests...")
        passed = 0
        failed = 0
        for inp, should_contain, should_not_contain in test_cases:
            out = redact.redact(inp)
            ok = should_contain in out and should_not_contain not in out
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            else:
                failed += 1
            print(f"  {status}: {inp[:60]} -> {out[:60]}")

        print(f"\n{passed} passed, {failed} failed")
        return 0 if failed == 0 else 1

    def scan(self, path: Path, as_json: bool) -> int:
        """Scan a file or directory for unredacted secrets."""
        if not path.exists():
            print(f"Error: {path} does not exist")
            return 1

        files = [path] if path.is_file() else list(path.rglob("*"))
        files = [f for f in files if f.is_file()]

        findings = []
        for f in files:
            try:
                content = f.read_text(errors="ignore")
            except Exception:
                continue

            for pattern, repl in redact._PATTERNS:
                for m in pattern.finditer(content):
                    start = max(0, m.start() - 40)
                    end = min(len(content), m.end() + 40)
                    context = content[start:end].replace("\n", "\\n")
                    findings.append({
                        "file": str(f.relative_to(self.root) if f.is_relative_to(self.root) else f),
                        "pattern": pattern.pattern[:80],
                        "match": m.group()[:80],
                        "context": context,
                    })

        if as_json:
            print(json.dumps(findings, indent=2))
        else:
            if not findings:
                print("No potential secrets found.")
            else:
                print(f"Found {len(findings)} potential secrets:")
                for finding in findings:
                    print(f"  {finding['file']}: {finding['match']} (pattern: {finding['pattern'][:50]})")
                    print(f"    Context: ...{finding['context']}...")

        return 0

    def show_patterns(self, as_json: bool) -> int:
        """Show all redaction patterns."""
        patterns = [{"pattern": p.pattern, "replacement": r} for p, r in redact._PATTERNS]
        if as_json:
            print(json.dumps(patterns, indent=2))
        else:
            print("Redaction patterns:")
            for i, (p, r) in enumerate(redact._PATTERNS):
                print(f"  {i+1}. {p.pattern[:100]} -> {r}")
        return 0
