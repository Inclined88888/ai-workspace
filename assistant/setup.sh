#!/usr/bin/env bash
# Guided first start for the personal assistant. Safe to re-run: existing keys, memory and skills are kept.
set -euo pipefail
cd "$(dirname "$0")"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
rand() { if command -v openssl >/dev/null; then openssl rand -hex "$1"; else head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi; }
get() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- || true; }
yes_no() { local a; read -r -p "$1 (y/N): " a; [[ "$a" =~ ^[Yy] ]]; }

bold "Personal assistant setup"
command -v docker >/dev/null || fail "Install Docker first: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required."
docker info >/dev/null 2>&1 || fail "Cannot reach the Docker daemon (is your user in the docker group?)."

touch .env && chmod 600 .env
[[ -n "$(get HERMES_DASHBOARD_PASSWORD)" ]] || echo "HERMES_DASHBOARD_PASSWORD=$(rand 12)" >> .env
[[ -n "$(get HERMES_DASHBOARD_SECRET)" ]] || echo "HERMES_DASHBOARD_SECRET=$(rand 32)" >> .env

bold "Starting Hermes (first run downloads about 4 GB)"
docker compose up -d
for _ in $(seq 1 90); do docker compose exec -T hermes test -f /opt/data/config.yaml 2>/dev/null && break; sleep 2; done
docker compose exec -T hermes test -f /opt/data/config.yaml || fail "Hermes did not start. Check: docker compose logs hermes"

hq() { docker compose exec -T -u hermes -e HOME=/opt/data hermes hermes "$@" >/dev/null; }
configured=$(docker compose exec -T hermes sh -c 'test -f /opt/data/.assistant-setup-done && echo yes || true')

if [[ "$configured" != yes ]] || yes_no "Already set up. Change model, timezone or connections now?"; then
  echo
  bold "Model"
  echo "  1) Anthropic   2) OpenAI   3) OpenRouter (hundreds of models with one key)"
  read -r -p "Pick 1, 2 or 3 [1]: " pick
  case "${pick:-1}" in 2) provider=openai ;; 3) provider=openrouter ;; *) provider=anthropic ;; esac
  ./hermes key "$provider"
  ./hermes use "$provider" "${MODEL:-}"

  echo
  bold "Timezone (for schedules)"
  guess=$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo UTC)
  read -r -p "IANA timezone [${guess}]: " tz
  hq config set timezone "${tz:-$guess}"

  echo
  yes_no "Connect Telegram now?" && ./hermes connect telegram
  yes_no "Connect Discord now?" && ./hermes connect discord
  if yes_no "Schedule a daily report?"; then
    read -r -p "Time HH:MM [08:00]: " at
    read -r -p "Deliver to telegram, discord or local [telegram]: " where
    ./hermes daily-report "${at:-08:00}" "${where:-telegram}"
  fi
  docker compose exec -T hermes touch /opt/data/.assistant-setup-done
fi

echo
bold "Ready"
echo "  Terminal:   ./hermes                 (talk to it here; same memory as your chat apps)"
echo "  Switch:     ./hermes key openrouter && ./hermes use openrouter some/model"
echo "  Schedules:  ./hermes cron list       or just ask: \"every Friday at 5pm, summarise my week\""
echo "  Dashboard:  ssh -L 9119:localhost:9119 you@this-server, then http://localhost:9119"
echo "              user admin, password $(get HERMES_DASHBOARD_PASSWORD)"
echo "  Back up:    docker run --rm -v hermes-assistant_hermes-data:/d -v \"\$PWD\":/b alpine tar czf /b/hermes-backup.tgz -C /d ."
