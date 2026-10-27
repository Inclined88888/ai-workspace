# browser-agent

Give an AI agent a goal in plain English and it drives a real Chromium browser to get it done. Built on [browser-use](https://github.com/browser-use/browser-use) for the agent loop and [Playwright](https://playwright.dev/python/) for one-off actions. Python 3.11+, fully asyncio.

```bash
browser-agent run "Go to weworkremotely.com and find three remote software engineer roles. Give title, company and link."
browser-agent run "On pcpartpicker.com find a compatible CPU, motherboard and 32GB RAM for a mid range AM5 gaming build under 600 USD" \
  --allow-domain "*.pcpartpicker.com"
```

## Install

```bash
pip install -e ".[server,dev]"     # from this folder
playwright install chromium         # one time browser download (add --with-deps on a fresh Linux box)
```

Set at least one model key, or run Ollama locally:

| Provider | `--provider` | Credentials | Default model |
| --- | --- | --- | --- |
| Anthropic Claude | `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` |
| Google Gemini | `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `gemini-flash-latest` |
| OpenAI | `openai` | `OPENAI_API_KEY` | pass `--model` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | pass `--model` (its `:free` models work) |
| Ollama (local, free) | `ollama` | none, `OLLAMA_HOST` (default `http://localhost:11434`) | `qwen3:8b` |
| Any OpenAI compatible API | `openai-compatible` | `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_API_KEY` | pass `--model` |

`--provider auto` (the default) picks Anthropic if its key is set, then Gemini, then local Ollama. `browser-agent providers` shows what is ready. You can also set `BROWSER_LLM_PROVIDER` and `BROWSER_LLM_MODEL`.

Model quality matters a lot here. Claude and Gemini handle multi step sites well. Local models under ~8B parameters often loop, misclick or stop early.

## CLI

The agent:

```bash
browser-agent run "GOAL" [--provider P] [--model M] [--max-steps 25] [--allow-domain PATTERN] [--headed] [--json]
```

`--max-steps` is a hard cap on steps and therefore on API spend. `--allow-domain` (repeatable) keeps the agent on the sites you name. Exit code is 0 when the agent finished, 2 when it ran out of steps.

One-off actions (no LLM, no API cost):

```bash
browser-agent open books.toscrape.com --screenshot out/books.png
browser-agent click https://books.toscrape.com "text=Travel" --screenshot out/travel.png
browser-agent type https://quotes.toscrape.com/login "#username" "demo" --screenshot out/login.png
browser-agent screenshot https://news.ycombinator.com -o out/hn.png --full-page
browser-agent screenshot https://books.toscrape.com -o out/h1.png --selector h1
browser-agent text https://books.toscrape.com --selector h1
browser-agent links https://books.toscrape.com
```

Selectors use Playwright syntax: CSS (`button.primary`), text (`text=Sign in`), role (`role=link[name='Docs']`) or XPath (`xpath=//h1`). Add `--json` for machine readable output and `--headed` to watch the browser.

## Python

```python
import asyncio
from browser_agent import run_task

async def main():
    result = await run_task(
        "Find the current top story on news.ycombinator.com and summarize it in one sentence",
        provider="anthropic",          # or gemini, ollama, openai, openrouter, openai-compatible
        max_steps=15,
        on_step=lambda e: print(e.step, e.next_goal, e.url),
    )
    print(result.final, result.success, result.urls)

asyncio.run(main())
```

Pass your own browser-use chat model with `run_task(goal, llm=ChatAnthropic(...))` for full control. One-off actions live in `browser_agent.actions` (`open_url`, `click`, `type_text`, `screenshot`, `page_text`, `links`).

## Examples

| File | What it shows |
| --- | --- |
| `examples/job_search.py` | Three remote software engineer roles, as structured output |
| `examples/pc_build.py` | PC parts on PCPartPicker, restricted to that domain |
| `examples/compare_providers.py` | Same goal on Claude and Gemini, side by side timing |
| `examples/live_steps.py` | Streaming each step and saving screenshots |
| `examples/one_off_actions.py` | open, click, type, screenshot and text without an LLM |
| `examples/cli_examples.sh` | The CLI commands above in one script |

## Service mode (used by ROOKS)

`browser-agent-server` (see `src/browser_agent/server.py`) runs the agent behind an HTTP API with a job queue, step history and live screenshots. It also serves an OpenAPI tool for Open WebUI (`/tools`) and an MCP endpoint (`/mcp`) so coding agents such as OpenCode and Claude Code (and therefore Paperclip employees) can browse. Every endpoint needs `Authorization: Bearer $BROWSER_API_KEY`. In service mode the agent refuses IP address URLs, so it cannot reach other containers or your LAN.

## Tests

```bash
pytest
```

The tests use local pages only (no network, no API keys).

## Upgrading browser-use

`pyproject.toml` pins `browser-use` to one minor series because it changes APIs between minors. To upgrade, raise the range, reinstall and run `pytest`. Everything that touches browser-use lives in `agent.py` and `llm.py`.
