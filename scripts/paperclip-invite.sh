#!/usr/bin/env bash
# Prints a one-time invite link that creates the first Paperclip admin (you, the board).
# Safe to re-run: while no admin exists each run replaces the previous invite; afterwards it says so.
set -euo pipefail
cd "$(dirname "$0")/.."

pc() { docker compose exec -T -u node paperclip sh -c "cd /app/cli && ./node_modules/.bin/tsx src/index.ts $*" 2>&1 | sed 's/\x1b\[[0-9;]*m//g'; }
url=$(grep -E '^PAPERCLIP_PUBLIC_URL=' .env 2>/dev/null | cut -d= -f2- || true)
url=${url:-http://localhost:3200}

for _ in $(seq 1 60); do curl -sf "$url/api/health" >/dev/null 2>&1 && break; sleep 3; done
status=$(curl -sf "$url/api/health" | grep -o '"bootstrapStatus":"[a-z_]*"' | cut -d'"' -f4 || true)
if [[ "$status" != "bootstrap_pending" ]]; then
  echo "Paperclip already has an admin (status: ${status:-unreachable}). Sign in at $url"
  exit 0
fi

out=$(pc "auth bootstrap-ceo --base-url '$url'")
if grep -q "No config found" <<<"$out"; then
  # First run in Docker: onboard writes the instance config, prints the invite, then starts a
  # throwaway foreground server. The timeout ends it; the real server keeps running untouched.
  out=$(docker compose exec -T -u node paperclip sh -c "cd /app/cli && timeout 120 ./node_modules/.bin/tsx src/index.ts onboard -y --bind lan --no-install-service < /dev/null" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' || true)
fi
invite=$(grep -o 'https\?://[^ ]*/invite/pcp_bootstrap_[a-z0-9]*' <<<"$out" | tail -1 || true)
if [[ -z "$invite" ]]; then
  echo "Could not create a Paperclip invite. Output:"; echo "$out" | tail -20; exit 1
fi
echo "$invite"
