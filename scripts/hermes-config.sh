#!/usr/bin/env bash
# First run setup for Hermes Agent, done through Hermes' own `hermes config set` so its defaults stay intact.
#  • model: Anthropic if ANTHROPIC_API_KEY is set, else OpenRouter, else your local Ollama DEFAULT_MODEL
#    (Hermes needs a model with at least a 64K context window; qwen3:4b has 256K, qwen3:8b only 40K)
#  • MCP servers: ROOKS (notes, tasks, calendar, mail drafts) and the browser agent; keys stay as ${VAR}
# Runs once (marker file in the hermes volume). After that the config is yours: change the model in the
# Hermes dashboard or with `hermes model`. Use --force to apply these defaults again.
set -euo pipefail
cd "$(dirname "$0")/.."
get() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
hx() { docker compose exec -T -u hermes -e HOME=/opt/data hermes "$@"; }

for _ in $(seq 1 60); do hx test -f /opt/data/config.yaml 2>/dev/null && break; sleep 2; done
if [[ "${1:-}" != "--force" ]] && hx test -f /opt/data/.ai-workspace-configured 2>/dev/null; then
  echo "Hermes already configured; leaving its config alone (use --force to reapply defaults)."
  exit 0
fi

if [[ -n "$(get ANTHROPIC_API_KEY)" ]]; then
  provider=anthropic; base_url=""; model=claude-sonnet-5-5
elif [[ -n "$(get OPENROUTER_API_KEY)" ]]; then
  provider=openrouter; base_url=https://openrouter.ai/api/v1; model=anthropic/claude-sonnet-5.5
else
  provider=custom; base_url=http://ollama:11434/v1; model=$(get DEFAULT_MODEL)
fi

hx hermes config set model.provider "$provider" >/dev/null
[[ -n "$base_url" ]] && hx hermes config set model.base_url "$base_url" >/dev/null
hx hermes config set model.default "$model" >/dev/null
# Local models: pin the window to Hermes' 64K minimum. Hermes otherwise requests the model's full window
# (256K for qwen3:4b), whose cache alone needs tens of GB of RAM.
[[ "$provider" == custom ]] && hx hermes config set model.ollama_num_ctx 65536 >/dev/null
hx hermes config set mcp_servers.workbench.url "http://workbench:8000/mcp/" >/dev/null
hx hermes config set mcp_servers.workbench.headers.Authorization 'Bearer ${TOOLS_API_KEY}' >/dev/null
hx hermes config set mcp_servers.browser.url "http://browser:8100/mcp" >/dev/null
hx hermes config set mcp_servers.browser.headers.Authorization 'Bearer ${BROWSER_API_KEY}' >/dev/null
hx touch /opt/data/.ai-workspace-configured
docker compose restart hermes >/dev/null
echo "Hermes configured: $provider/$model with ROOKS and browser tools."
