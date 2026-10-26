#!/usr/bin/env bash
# Guided setup for AI Workspace. Safe to re-run: existing .env values, secrets and data are kept.
#
#   ./setup.sh                          the light core: one container (assistant, memory, skills, automations,
#                                       company, notes, tasks, calendar, mail, research)
#   ./setup.sh --with search,browser    add optional add-ons (comma separated):
#       search        SearXNG web search without an API key            (~110 MB RAM)
#       browser       Chromium browser agent                           (~2.4 GB download)
#       local-models  Ollama in Docker (on a Mac prefer the native Ollama app: it uses the GPU)
#       openwebui     Open WebUI chat app                              (~6.5 GB download)
#       paperclip     Paperclip company app                            (~13 GB download)
#       hermes        Hermes Agent                                     (~4.3 GB download)
#   ./setup.sh --with none              back to the core only (stops add-ons, keeps their data)
#   ./setup.sh --yes                    accept defaults, no prompts
# Works with the bash 3.2 that ships with macOS.
set -euo pipefail
cd "$(dirname "$0")"

YES=false
WITH=""
WITH_GIVEN=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) YES=true ;;
    --with) WITH=${2:-}; WITH_GIVEN=true; shift ;;
    --with=*) WITH=${1#--with=}; WITH_GIVEN=true ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "Unknown option $1 (see ./setup.sh --help)" >&2; exit 1 ;;
  esac
  shift
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
ask() { # ask VAR "Question" default
  local __var=$1 __q=$2 __def=${3:-} __ans=""
  if $YES; then printf -v "$__var" '%s' "$__def"; return; fi
  read -r -p "  $__q${__def:+ [$__def]}: " __ans || true
  printf -v "$__var" '%s' "${__ans:-$__def}"
}
ask_secret() {
  local __var=$1 __q=$2 __ans=""
  if ! $YES; then read -r -s -p "  $__q (Enter to skip): " __ans || true; echo; fi
  printf -v "$__var" '%s' "$__ans"
}
rand() { if command -v openssl >/dev/null 2>&1; then openssl rand -hex "${1:-32}"; else head -c "${1:-32}" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi; }
get_env() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
set_env() {
  local key=$1 val=$2 tmp
  tmp=$(mktemp)
  if grep -qE "^$key=" .env; then
    awk -v k="$key" -v v="$val" 'BEGIN{FS=OFS="="} $1==k {print k"="v; next} {print}' .env > "$tmp"
  else
    cat .env > "$tmp"; printf '%s=%s\n' "$key" "$val" >> "$tmp"
  fi
  cat "$tmp" > .env; rm -f "$tmp"
}
has() { case ",$ADDONS," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
host_tz() {
  local tz=""
  if [[ -L /etc/localtime ]]; then tz=$(readlink /etc/localtime | sed -E 's#.*/zoneinfo/##'); fi
  [[ -z "$tz" && -f /etc/timezone ]] && tz=$(cat /etc/timezone)
  echo "${tz:-UTC}"
}

bold "AI Workspace setup"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed (Mac: Docker Desktop or OrbStack; Linux: https://docs.docker.com/engine/install/)."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required (the 'docker compose' command)."
docker info >/dev/null 2>&1 || fail "Cannot talk to Docker. Is Docker running (and is your user in the docker group on Linux)?"

FIRST_RUN=false
if [[ ! -f .env ]]; then cp .env.example .env; chmod 600 .env; FIRST_RUN=true; fi
# Upgrades: add settings introduced since this .env was created (existing values are never touched)
while IFS= read -r line; do
  key=${line%%=*}
  if [[ "$line" =~ ^[A-Z_]+= ]] && ! grep -qE "^$key=" .env; then printf '%s\n' "$line" >> .env; fi
done < .env.example
# Secrets: generate any that are empty; never rotate existing ones (that would lock you out of stored data)
for key in APP_SECRET TOOLS_API_KEY WEBUI_SECRET_KEY SEARXNG_SECRET BROWSER_API_KEY PAPERCLIP_AUTH_SECRET \
           PAPERCLIP_TOOL_ACTION_SECRET PAPERCLIP_AGENT_JWT_SECRET HERMES_API_KEY HERMES_DASHBOARD_SECRET; do
  if [[ -z "$(get_env "$key")" ]]; then set_env "$key" "$(rand 32)"; fi
done
if [[ -z "$(get_env SETUP_CODE)" ]]; then set_env SETUP_CODE "$(rand 4)"; fi
if [[ -z "$(get_env HERMES_DASHBOARD_PASSWORD)" ]]; then set_env HERMES_DASHBOARD_PASSWORD "$(rand 12)"; fi

# Add-ons: --with replaces the saved list; without it the saved list is kept
if $WITH_GIVEN; then
  ADDONS=$(echo "$WITH" | tr -d ' ' | tr 'A-Z' 'a-z')
  [[ "$ADDONS" == "none" ]] && ADDONS=""
  for a in $(echo "$ADDONS" | tr ',' ' '); do
    case "$a" in search|browser|local-models|openwebui|paperclip|hermes) ;; *) fail "Unknown add-on '$a'" ;; esac
  done
  set_env ADDONS "$ADDONS"
else
  ADDONS=$(get_env ADDONS)
fi
set_env COMPOSE_PROFILES "$ADDONS"

if $FIRST_RUN; then
  echo; bold "Basics"
  ask NAME "Workspace name" "AI Workspace"; set_env WORKSPACE_NAME "$NAME"
  ask TZ_IN "Your timezone (for schedules)" "$(host_tz)"; set_env WORKSPACE_TIMEZONE "$TZ_IN"
  ask LAN "Allow access from other devices on your network? (y/N)" "n"
  if [[ "$LAN" =~ ^[Yy] ]]; then
    set_env BIND_ADDRESS 0.0.0.0
    info "Bound to all interfaces over plain HTTP. Use a VPN (e.g. Tailscale) or an HTTPS proxy beyond your home network."
  fi
  echo; bold "Models"
  info "Paste at least one key (stored in .env, mode 600), or use local models (Ollama)."
  ask_secret ANTHROPIC "Anthropic API key"; [[ -n "$ANTHROPIC" ]] && set_env ANTHROPIC_API_KEY "$ANTHROPIC"
  ask_secret OPENROUTER "OpenRouter API key (hundreds of models, real cost tracking)"; [[ -n "$OPENROUTER" ]] && set_env OPENROUTER_API_KEY "$OPENROUTER"
  ask_secret OPENAI "OpenAI API key"; [[ -n "$OPENAI" ]] && set_env OPENAI_API_KEY "$OPENAI"
fi

# Local models: Ollama add-on container, or the native Ollama app on this computer (fast on a Mac)
if has local-models; then
  set_env OLLAMA_URL "http://ollama:11434"
elif [[ -z "$(get_env OLLAMA_URL)" || "$(get_env OLLAMA_URL)" == "http://ollama:11434" ]]; then
  set_env OLLAMA_URL ""
  if curl -sf -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    ask USE_NATIVE "Ollama is running on this computer. Use it for local models? (Y/n)" "y"
    [[ "$USE_NATIVE" =~ ^[Yy] ]] && set_env OLLAMA_URL "http://host.docker.internal:11434"
  fi
fi

# Point the workspace at the enabled add-ons (and clear the ones that were turned off)
HOST=localhost
if [[ "$(get_env BIND_ADDRESS)" == "0.0.0.0" ]]; then
  HOST=$( (hostname -I 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || echo localhost) | awk '{print $1}')
fi
if has search; then set_env SEARXNG_URL "http://searxng:8080"; set_env WEBUI_ENABLE_WEB_SEARCH true; else set_env SEARXNG_URL ""; set_env WEBUI_ENABLE_WEB_SEARCH false; fi
if has browser; then set_env BROWSER_URL "http://browser:8100"; else set_env BROWSER_URL ""; fi
if has openwebui; then set_env CHAT_URL "http://${HOST}:$(get_env WEBUI_PORT)"; set_env WEBUI_PUBLIC_URL "$(get_env CHAT_URL)"; else set_env CHAT_URL ""; fi
if has paperclip; then
  set_env PAPERCLIP_URL "http://${HOST}:$(get_env PAPERCLIP_PORT)"; set_env PAPERCLIP_PUBLIC_URL "$(get_env PAPERCLIP_URL)"
  set_env PAPERCLIP_ALLOWED_HOSTNAMES "localhost${HOST:+,$HOST}"
else set_env PAPERCLIP_URL ""; fi
if has hermes; then set_env HERMES_URL "http://${HOST}:$(get_env HERMES_DASHBOARD_PORT)"; else set_env HERMES_URL ""; fi
# Open WebUI connections: cloud providers, plus Hermes when that add-on is on
URLS=""; KEYS=""
add_conn() { URLS="${URLS:+$URLS;}$1"; KEYS="${KEYS:+$KEYS;}$2"; }
has hermes && add_conn "http://hermes:8642/v1" "$(get_env HERMES_API_KEY)"
[[ -n "$(get_env ANTHROPIC_API_KEY)" ]] && add_conn "https://api.anthropic.com/v1" "$(get_env ANTHROPIC_API_KEY)"
[[ -n "$(get_env OPENROUTER_API_KEY)" ]] && add_conn "https://openrouter.ai/api/v1" "$(get_env OPENROUTER_API_KEY)"
[[ -n "$(get_env OPENAI_API_KEY)" ]] && add_conn "https://api.openai.com/v1" "$(get_env OPENAI_API_KEY)"
set_env WEBUI_OPENAI_API_BASE_URLS "$URLS"; set_env WEBUI_OPENAI_API_KEYS "$KEYS"
set_env WEBUI_ENABLE_OPENAI_API "$([[ -n "$URLS" ]] && echo true || echo false)"

# NVIDIA GPU for the Ollama add-on (Linux only)
if has local-models && command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 && docker info 2>/dev/null | grep -qi nvidia; then
  set_env COMPOSE_FILE "docker-compose.yml:docker-compose.gpu.yml"; info "NVIDIA GPU detected: Ollama will use it."
fi

echo; bold "Starting${ADDONS:+ (add-ons: $ADDONS)}"
docker compose up -d --build
# Stop add-ons that are no longer enabled (their data volumes are kept)
for svc in searxng browser ollama open-webui paperclip hermes; do
  case "$svc" in searxng) a=search ;; ollama) a=local-models ;; open-webui) a=openwebui ;; *) a=$svc ;; esac
  if ! has "$a" && [[ -n "$(docker compose --profile "$a" ps -q "$svc" 2>/dev/null)" ]]; then
    docker compose --profile "$a" stop "$svc" >/dev/null 2>&1 && info "Stopped $svc (add-on off)"
  fi
done

if has local-models; then
  for _ in $(seq 1 60); do docker compose exec -T ollama ollama list >/dev/null 2>&1 && break; sleep 2; done
  MODEL=$(get_env DEFAULT_MODEL)
  if ! docker compose exec -T ollama ollama list | awk 'NR>1{print $1}' | grep -qx "$MODEL"; then
    ask PULL "Download local model $MODEL (~2.5 GB) now? (Y/n)" "y"
    [[ "$PULL" =~ ^[Yy] ]] && { docker compose exec -T ollama ollama pull "$MODEL" || info "Pull failed; retry in Settings > Local models."; }
  fi
fi
if has paperclip; then
  ./scripts/sync-opencode-config.sh && docker compose restart paperclip >/dev/null
  PAPERCLIP_INVITE=$(./scripts/paperclip-invite.sh 2>&1 | tail -1 || true)
fi
has hermes && { ./scripts/hermes-config.sh || info "Hermes config not written; run ./scripts/hermes-config.sh later."; }

echo; bold "Ready"
info "Workspace:  http://${HOST}:$(get_env WORKBENCH_PORT)    setup code: $(get_env SETUP_CODE)"
info "Terminal:   cli/ws login http://${HOST}:$(get_env WORKBENCH_PORT)   (token: Assistant > Settings > Terminal access)"
info "Telegram, web search keys, timezone: Assistant > Settings"
has openwebui && info "Open WebUI: $(get_env CHAT_URL)    (first account becomes admin; create it now)"
if has paperclip; then
  info "Paperclip:  $(get_env PAPERCLIP_URL)"
  if [[ "${PAPERCLIP_INVITE:-}" == http* ]]; then info "            first admin: $PAPERCLIP_INVITE (expires in 72h)"; else info "            ${PAPERCLIP_INVITE:-}"; fi
fi
has hermes && info "Hermes:     $(get_env HERMES_URL)    (admin / $(get_env HERMES_DASHBOARD_PASSWORD)); terminal: ./hermes"
[[ -z "$ADDONS" ]] && info "Add-ons (optional): ./setup.sh --with search,browser,local-models,openwebui,paperclip,hermes"
info "Stop: docker compose down    Update: git pull && ./setup.sh"
