#!/usr/bin/env bash
# One-off CLI actions, then an agent run. Run from the browser-agent folder after `pip install -e .`.
set -euo pipefail
mkdir -p out

browser-agent open books.toscrape.com --screenshot out/books.png
browser-agent click https://books.toscrape.com "text=Travel" --screenshot out/travel.png
browser-agent text https://books.toscrape.com --selector h1
browser-agent links https://books.toscrape.com
browser-agent screenshot https://news.ycombinator.com -o out/hn.png --full-page
browser-agent type https://quotes.toscrape.com/login "#username" "demo" --screenshot out/login.png
browser-agent providers

# Needs a model (ANTHROPIC_API_KEY, GEMINI_API_KEY, or local Ollama):
browser-agent run "Go to news.ycombinator.com and tell me the title of the top story" --max-steps 8
