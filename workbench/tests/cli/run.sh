#!/usr/bin/env bash
# Installs the CLI into a brand-new virtual environment (as a user would), then runs test_cli.py against a fresh ROOKS
# server with the stand-in AI. ROOKS_PY: a Python with the backend's requirements (default python3).
set -eu
cd "$(dirname "$0")"
PY="${ROOKS_PY:-python3}"
venv=$(mktemp -d)/venv
python3 -m venv "$venv"
"$venv/bin/pip" install -q ../../../cli
rm -rf ../../../cli/build ../../../cli/*.egg-info
echo "installed: $("$venv/bin/rooks" --version) from $("$venv/bin/python" -c 'import rooks_cli; print(rooks_cli.__file__)')"
ROOKS_CLI="$venv/bin/rooks" "$PY" test_cli.py
