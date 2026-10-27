#!/usr/bin/env bash
# Writes paperclip/opencode.json: the OpenCode config used by Paperclip's opencode_local employees.
#  • every model downloaded in Ollama becomes "ollama/<name>" (free, local)
#  • the built in "opencode" provider (OpenCode Zen, free tier models) works when OPENCODE_API_KEY is set
#  • ROOKS (notes, tasks, calendar, mail drafts) and the browser agent are attached as MCP servers
# Run again after pulling new Ollama models. setup.sh calls it automatically.
set -euo pipefail
cd "$(dirname "$0")/.."

models=$(docker compose exec -T ollama ollama list 2>/dev/null | awk 'NR>1 && $1 !~ /embed/ {print $1}' || true)
entries=""
for m in $models; do
  [[ "$m" =~ ^[A-Za-z0-9._:/-]+$ ]] || continue
  entries+="${entries:+,}
        \"$m\": { \"name\": \"$m (local)\" }"
done

cat > paperclip/opencode.json <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://ollama:11434/v1" },
      "models": {${entries}
      }
    }
  },
  "mcp": {
    "workbench": {
      "type": "remote",
      "url": "http://workbench:8000/mcp/",
      "enabled": true,
      "headers": { "Authorization": "Bearer {env:TOOLS_API_KEY}" }
    },
    "browser": {
      "type": "remote",
      "url": "http://browser:8100/mcp",
      "enabled": true,
      "headers": { "Authorization": "Bearer {env:BROWSER_API_KEY}" }
    }
  }
}
JSON
echo "Wrote paperclip/opencode.json with $(wc -w <<<"$models" | tr -d ' ') local model(s)."
