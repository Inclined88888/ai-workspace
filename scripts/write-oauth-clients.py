#!/usr/bin/env python3
"""Fill workbench/backend/app/oauth_clients.py from the environment before a release build.

    GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... MICROSOFT_OAUTH_CLIENT_ID=... \
        python scripts/write-oauth-clients.py

Unset variables keep whatever the file already has (empty means "not set up in this build", which the app handles).
Prints which providers are configured, never the values. Setup steps: docs/connect-google-microsoft.md.
"""
import os
import re
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "workbench" / "backend" / "app" / "oauth_clients.py"
FIELDS = {
    "GOOGLE_CLIENT_ID": "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET": "GOOGLE_OAUTH_CLIENT_SECRET",
    "MICROSOFT_CLIENT_ID": "MICROSOFT_OAUTH_CLIENT_ID",
    "MICROSOFT_TENANT": "MICROSOFT_OAUTH_TENANT",
}
SAFE = re.compile(r"^[A-Za-z0-9._\-]{1,200}$")


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    for name, env in FIELDS.items():
        value = os.getenv(env, "").strip()
        if not value:
            continue
        if not SAFE.match(value):
            print(f"{env} has unexpected characters; refusing to write it.", file=sys.stderr)
            return 1
        text, n = re.subn(rf'^{name} = ".*"$', f'{name} = "{value}"', text, flags=re.M)
        if n != 1:
            print(f"Could not find {name} in {PATH}", file=sys.stderr)
            return 1
    PATH.write_text(text, encoding="utf-8")
    g = bool(re.search(r'^GOOGLE_CLIENT_ID = ".+"$', text, re.M))
    m = bool(re.search(r'^MICROSOFT_CLIENT_ID = ".+"$', text, re.M))
    print(f"One-click Google: {'on' if g else 'off'}; one-click Microsoft: {'on' if m else 'off'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
