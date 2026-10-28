#!/usr/bin/env bash
# Writes data with an older ROOKS (any git ref, default the v14 release), then opens it with this checkout:
# data kept, new tables and triggers added, old rows tracked. ROOKS_PY: Python with the backend requirements.
set -eu
ref="${1:-origin/development/rooks-v14}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
py="${ROOKS_PY:-python3}"
work=$(mktemp -d)
trap 'git -C "$repo" worktree remove --force "$work/old" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
git -C "$repo" worktree add -q --detach "$work/old" "$ref"
mkdir "$work/data"
(cd "$work" && "$py" "$here/seed_and_check.py" "$work/old/workbench/backend" "$work/data" seed)
(cd "$work" && "$py" "$here/seed_and_check.py" "$repo/workbench/backend" "$work/data" check)
