#!/usr/bin/env bash
# Build the downloadable source zip: the committed tree (git archive) plus the prebuilt web UI, so installing from the
# zip needs no Node.js. No node_modules, venvs, build outputs or secrets (only tracked files + frontend dist).
#   scripts/release/package.sh [out.zip]
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
out=${1:-dist/ai-workspace-$(cat VERSION).zip}
mkdir -p "$(dirname "$out")"
out=$(cd "$(dirname "$out")" && pwd)/$(basename "$out")
[ -f workbench/frontend/dist/index.html ] || (cd workbench/frontend && npm ci --no-audit --no-fund && npm run build)
tmp=$(mktemp -d)
name=ai-workspace-$(cat VERSION)
git archive --format=tar --prefix="$name/" HEAD | tar -x -C "$tmp"
mkdir -p "$tmp/$name/workbench/frontend"
cp -R workbench/frontend/dist "$tmp/$name/workbench/frontend/dist"
rm -f "$out"
(cd "$tmp" && zip -qr -X "$out" "$name")
rm -rf "$tmp"
echo "$out ($(du -h "$out" | cut -f1))"
