"""`rooks redact`: run the app's redaction rules (redact.py: provider keys, AWS keys, private keys, JWTs, tokens,
cookies, emails, phone numbers) over a file or standard input, offline.

  some-command 2>&1 | rooks redact          print it with secrets replaced (safe to paste into an issue)
  rooks redact --check build.log            exit 1 and list what would be redacted (CI, pre-commit)
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

from . import redact

_KIND = re.compile(r"<redacted(?::([a-z-]+))?>|<(email|phone)>")


def findings(text: str) -> Counter:
    """What redaction would replace, by kind (key, aws-key, private-key, jwt, email, phone, secret)."""
    return Counter((m.group(1) or m.group(2) or "secret") for m in _KIND.finditer(redact.redact(text))) - \
        Counter((m.group(1) or m.group(2) or "secret") for m in _KIND.finditer(text))


def main(rest: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="rooks redact", description="Replace secrets and personal data in text (offline)")
    ap.add_argument("-C", "--cwd", default=".")
    ap.add_argument("file", nargs="?", default="-", help="file to read (default: standard input)")
    ap.add_argument("--check", action="store_true", help="print nothing but a summary; exit 1 if anything would be redacted")
    a = ap.parse_args(rest)
    try:
        text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print(f"rooks: cannot read {a.file}: {e.strerror}", file=sys.stderr)
        return 2
    if not a.check:
        sys.stdout.write(redact.redact(text))
        return 0
    found = findings(text)
    if not found:
        print("clean: nothing to redact")
        return 0
    print("would redact: " + ", ".join(f"{n} {k}" for k, n in sorted(found.items())))
    return 1
