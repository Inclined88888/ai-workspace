#!/usr/bin/env bash
# Guided first run for AI Workspace. Safe to re-run: existing .env values and data are kept.
#   ./setup.sh          interactive
#   ./setup.sh --yes    accept defaults, no prompts
set -euo pipefail
cd "$(dirname "$0")"

YES=false
[[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]] && YES=true

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

ask() { # ask VAR "Question" default
  local __var=$1 __q=$2 __def=${3:-} __ans
  if $YES; then printf -v "$__var" '%s' "$__def"; return; fi
  read -r -p "  $__q${__def:+ [$__def]}: " __ans || true
  printf -v "$__var" '%s' "${__ans:-$__def}"
}
ask_secret() { # ask_secret VAR "Question"
  local __var=$1 __q=$2 __ans=""
  if ! $YES; then read -r -s -p "  $__q (Enter to skip): " __ans || true; echo; fi
  printf -v "$__var" '%s' "$__ans"
}

rand() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "${1:-32}"
  else head -c "${1:-32}" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

get_env() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
set_env() { # set_env KEY VALUE (replaces or appends)
  local key=$1 val=$2 tmp
  tmp=$(mktemp)
  if grep -qE "^$key=" .env; then
    awk -v k="$key" -v v="$val" 'BEGIN{FS=OFS="="} $1==k {print k"="v; next} {print}' .env > "$tmp"
  else
    cat .env > "$tmp"; printf '%s=%s\n' "$key" "$val" >> "$tmp"
  fi
  cat "$tmp" > .env; rm -f "$tmp"
}

bold "AI Workspace setup"

command -v docker >/dev/null 2>&1 || fail "Docker is not installed. See https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required (the 'docker compose' command)."
docker info >/dev/null 2>&1 || fail "Cannot talk to the Docker daemon. Is it running, and is your user in the docker group?"

FIRST_RUN=false
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  FIRST_RUN=true
fi

# Upgrades: add settings introduced since this .env was created (existing values are never touched)
while IFS= read -r line; do
  key=${line%%=*}
  [[ "$line" =~ ^[A-Z_]+= ]] && ! grep -qE "^$key=" .env && printf '%s\n' "$line" >> .env
done < .env.example

# Secrets: generate any that are empty. Existing ones are never rotated (that would lock you out of data).
for key in WEBUI_SECRET_KEY APP_SECRET TOOLS_API_KEY SEARXNG_SECRET BROWSER_API_KEY PAPERCLIP_AUTH_SECRET PAPERCLIP_TOOL_ACTION_SECRET PAPERCLIP_AGENT_JWT_SECRET; do
  [[ -z "$(get_env "$key")" ]] && set_env "$key" "$(rand 32)"
done
[[ -z "$(get_env SETUP_CODE)" ]] && set_env SETUP_CODE "$(rand 4)"

if $FIRST_RUN; then
  echo
  bold "Basics"
  ask NAME "Workspace name" "AI Workspace"
  set_env WORKSPACE_NAME "$NAME"

  ask LAN "Allow access from other devices on your network? (y/N)" "n"
  if [[ "$LAN" =~ ^[Yy] ]]; then
    set_env BIND_ADDRESS 0.0.0.0
    HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    set_env WEBUI_PUBLIC_URL "http://${HOST_IP:-localhost}:$(get_env WEBUI_PORT)"
    set_env PAPERCLIP_PUBLIC_URL "http://${HOST_IP:-localhost}:$(get_env PAPERCLIP_PORT)"
    set_env PAPERCLIP_ALLOWED_HOSTNAMES "localhost${HOST_IP:+,$HOST_IP}"
    info "Bound to all interfaces. Traffic is plain HTTP: put a reverse proxy with HTTPS in front before exposing it beyond your LAN."
  fi

  ask MODEL "Default local model (from ollama.com/library)" "$(get_env DEFAULT_MODEL)"
  set_env DEFAULT_MODEL "$MODEL"

  echo
  bold "Cloud providers (optional)"
  info "Keys are stored in .env (chmod 600) and passed to both apps."
  ask_secret OPENAI "OpenAI API key"
  ask_secret ANTHROPIC "Anthropic API key"
  ask_secret OPENROUTER "OpenRouter API key"
  ask_secret GEMINI "Gemini API key (browser agent, Gemini CLI employees)"
  ask_secret OPENCODE "OpenCode Zen API key (free tier models for Paperclip employees, opencode.ai/auth)"
  [[ -n "$GEMINI" ]] && set_env GEMINI_API_KEY "$GEMINI"
  [[ -n "$OPENCODE" ]] && set_env OPENCODE_API_KEY "$OPENCODE"
  URLS=(); KEYS=()
  [[ -n "$OPENAI" ]] && { set_env OPENAI_API_KEY "$OPENAI"; URLS+=("https://api.openai.com/v1"); KEYS+=("$OPENAI"); }
  [[ -n "$ANTHROPIC" ]] && { set_env ANTHROPIC_API_KEY "$ANTHROPIC"; URLS+=("https://api.anthropic.com/v1"); KEYS+=("$ANTHROPIC"); }
  [[ -n "$OPENROUTER" ]] && { set_env OPENROUTER_API_KEY "$OPENROUTER"; URLS+=("https://openrouter.ai/api/v1"); KEYS+=("$OPENROUTER"); }
  if (( ${#URLS[@]} )); then
    set_env WEBUI_ENABLE_OPENAI_API true
    set_env WEBUI_OPENAI_API_BASE_URLS "$(IFS=';'; echo "${URLS[*]}")"
    set_env WEBUI_OPENAI_API_KEYS "$(IFS=';'; echo "${KEYS[*]}")"
  fi
fi

# GPU: use the NVIDIA override when the host can run GPU containers.
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 && docker info 2>/dev/null | grep -qi nvidia; then
  set_env COMPOSE_FILE "docker-compose.yml:docker-compose.gpu.yml"
  info "NVIDIA GPU detected: Ollama will use it."
elif command -v nvidia-smi >/dev/null 2>&1; then
  info "NVIDIA GPU found but Docker has no nvidia runtime. Install the NVIDIA Container Toolkit for GPU speed. Running on CPU."
fi

echo
bold "Starting containers (first run downloads several GB, this can take a while)"
docker compose up -d --build

echo
bold "Waiting for Ollama"
for _ in $(seq 1 60); do
  docker compose exec -T ollama ollama list >/dev/null 2>&1 && break
  sleep 2
done
docker compose exec -T ollama ollama list >/dev/null 2>&1 || fail "Ollama did not start. Check: docker compose logs ollama"

MODEL=$(get_env DEFAULT_MODEL)
if docker compose exec -T ollama ollama list | awk 'NR>1{print $1}' | grep -qx "$MODEL"; then
  info "$MODEL already downloaded."
else
  ask PULL "Download $MODEL now? (Y/n)" "y"
  if [[ "$PULL" =~ ^[Yy] ]]; then
    docker compose exec -T ollama ollama pull "$MODEL" || info "Pull failed. You can retry later in Workbench → Settings → Local models."
  fi
fi
ask EMBED "Download nomic-embed-text for better document search in Chat? (y/N)" "n"
[[ "$EMBED" =~ ^[Yy] ]] && { docker compose exec -T ollama ollama pull nomic-embed-text || true; }

# Give Paperclip's OpenCode employees every local model plus the browser tool
./scripts/sync-opencode-config.sh && docker compose restart paperclip >/dev/null

echo
bold "Paperclip admin"
PAPERCLIP_INVITE=$(./scripts/paperclip-invite.sh 2>&1 | tail -1 || true)

BIND=$(get_env BIND_ADDRESS); HOST=localhost
[[ "$BIND" == "0.0.0.0" ]] && HOST=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
bold "Ready"
info "Workbench:  http://${HOST}:$(get_env WORKBENCH_PORT)    setup code: $(get_env SETUP_CODE)"
info "Chat:       http://${HOST}:$(get_env WEBUI_PORT)    (the first account you create becomes admin)"
info "Company:    http://${HOST}:$(get_env PAPERCLIP_PORT)    (Paperclip)"
info "            First admin: open $PAPERCLIP_INVITE"
info "            (expires in 72h; new link any time: ./scripts/paperclip-invite.sh)"
echo
info "Do the first sign up in Chat and Paperclip right away so nobody else can claim the admin accounts."
info "Stop: docker compose down    Update: docker compose pull && docker compose up -d --build"
