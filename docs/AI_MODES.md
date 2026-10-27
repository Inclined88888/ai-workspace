# ROOKS v11.1: AI modes and the Free Cloud AI first run

Branch `development/rooks-v11.1`, created from `development/rooks-ui-v11` (`d4e4b7d`). Requirement statuses are items
130 to 141 of `docs/ROADMAP.md`. The existing provider system was extended, not replaced: providers, keys, connection
checks, defaults, backups and the model list work as before.

## The four modes

| Mode | What ROOKS may use | Set up |
|---|---|---|
| **Free Cloud AI** (first-run default) | Only models verified as free (below). No paid backup, no paid connection check. | OpenRouter or Google Gemini key |
| **Premium Cloud AI** | Paid models, within a monthly spending limit you must set first | Anthropic, OpenAI, OpenRouter, OpenCode |
| **Local AI** (optional) | Only models on this computer or your network | Ollama, or a llama.cpp `llama-server` you run |
| **Custom AI Server** | Your own OpenAI compatible endpoint | Base URL and key |

`GET /api/ai-mode` reads the mode; `PUT /api/ai-mode {mode, monthly_limit_cents?}` changes it. Changing mode never
removes a provider, a key or your saved default model. A saved default that the mode does not allow is kept and shown
as "kept but not used in this AI mode"; it is used again when you switch back.

## What counts as free

A free tier on a provider is not enough. `llm.free_basis()` returns the evidence, and a model with none is not free:

- **price:** an OpenRouter model whose id ends in `:free` **and** whose price in OpenRouter's own model list
  (`GET /api/v1/models`, `pricing`) is zero for every listed field. A model named `:free` with any non-zero price is
  not free. The list is fetched again before use when it is more than six hours old.
- **confirmed:** a Google Gemini key, or a local server that is not Ollama (llama.cpp, LM Studio), that you confirmed
  cannot bill you. Google gives no API that says whether a key's project has billing (its tier is shown only in AI
  Studio), and a local server could be a proxy to a paid API. ROOKS records your confirmation in the activity log;
  it is your statement, not a check.
- **local:** Ollama on this computer or network. Ollama's cloud models (names ending in `-cloud`) run on ollama.com
  and count as neither free nor local.

OpenRouter cannot be confirmed by hand: only its price list decides.

## How Free Cloud AI is enforced

Every model call passes through `llm._chat_once` or `llm.stream`. That covers chat, missions, workers and their
reviews, research, check-ins, learning, voice summaries, vision, model roles and connection checks. Both call
`mode_guard`, which refuses a model the mode does not allow before any request is sent. Embeddings
(`memory.embed`) and speech providers (`voice._check`) apply the same rule.

- **Defaults and backups:** the automatic default and backups skip models the mode does not allow, including backups
  you chose yourself.
- **Connection checks:** in Free and Local modes they never send a request that could be charged, even if asked to.
- **After every call:** if a model reports a cost above zero while in Free mode (OpenRouter returns the cost of every
  call), ROOKS blocks that model for Free mode and records it in the activity log. That call has already happened;
  this stops the next one.

Free is not unlimited. Free OpenRouter models allow 20 requests a minute and 50 a day (1,000 a day once you have
bought $10 of credits). Gemini's free tier has its own limits per model. One assistant answer can take several
requests when it uses tools. When a limit is reached the assistant says so; there is no paid backup.

## First run

1. **Choose a mode.** After creating the workspace, Free Cloud AI is preselected and marked Recommended. The server
   sets `ai_mode = free` at setup, so the default holds even if the browser closes there.
2. **Set it up, or skip.** Each mode shows its own setup, privacy note and connection test. *Skip for now* keeps Free
   Cloud AI with nothing connected.
3. **No local work during cloud setup.** No local engine is installed, no model is downloaded and no hardware is
   measured. The Ollama catalogue and status are only requested when you open Local AI.

Each connection test sends one small real request with a tool attached, the way the assistant sends them. It reports
which model answered, how many models are verified free, and how many of those list tool support. Common failures
come with what to do: a rejected key, rate limits, no credits, no tool support, unreachable, and Gemini needing your
confirmation.

## Local downloads

`POST /api/ollama/pull` requires `consent: true`. The interface shows, for the chosen model:
- the download size, from Ollama's library;
- a rough memory estimate (not measured on your computer);
- the licence, with a link.

The Download button stays disabled until you tick the agreement. Every download is recorded in the activity log.
Sizes and licences in the catalogue:

| Model | Download | Memory (rough) | Licence |
|---|---|---|---|
| qwen3:4b | 2.5 GB | about 4 GB | Apache 2.0 |
| qwen3:8b | 5.2 GB | about 8 GB | Apache 2.0 |
| gemma3:4b | 3.4 GB | about 5 GB | Gemma Terms of Use |

A model typed by name has no known size or licence, and the interface says so.

**AirLLM is not supported.** It is a Python library with no HTTP server, and it does not call tools, which the
assistant needs. An adapter would have to run models inside ROOKS's process and still could not use tools.

**llama.cpp** is supported as a server you run yourself (`llama-server --jinja`, port 8080 by default, or set
`LLAMACPP_URL`). ROOKS does not install or start it.

## Privacy, as shown in setup

- **What is sent:** your message, the conversation, and whatever the assistant reads to answer it (notes, tasks,
  calendar, file contents, web pages, relevant memories), plus your API key. Your ROOKS password is never sent.
  Local only projects are never sent to a cloud model.
- **OpenRouter** passes prompts to the company hosting each model, each with its own retention policy. Some may train
  on prompts; OpenRouter's privacy settings let you allow or block those, separately for free and paid models.
- **Google** says free tier Gemini content is used to improve its products.
- **Local AI:** messages stay on your computer or network, and cloud models are refused, including as backups.

## Existing installs

An install set up before v11.1 has no `ai_mode`. Nothing is restricted and nothing changes: the same provider and
default model answer as before. Settings shows "Not chosen" and says choosing is optional. No migration rewrites a
setting.

## Tests

| Suite | Result |
|---|---|
| Backend (pytest) | 264 passed, including `test_ai_mode.py` (15) and the rewritten free-model tests |
| Onboarding in the browser (`ui_onboard.py`) | 45 of 45: choices, keyboard, consent, premium limit, skip, enforcement, migration, phone, axe |
| Earlier browser suites | all passed (UI 43, models 21, search 5 and 6, add-ons 20, missions 28, trust 14, voice 26, workers 11, Needs You 11, research 9, v9 15, terminal 17, rich answers 33, navigation 19) |
| Computer Use end to end | 38 of 38 |
| Companion | 27 passed, 1 skipped (`openpyxl` not installed in the test environment) |
| Accessibility sweep | 44 page views, no serious or critical axe violations, no sideways scrolling at 390 px |

The browser tests used a stand-in OpenRouter (real response shape and `pricing` fields) and a stand-in Ollama. One
check sent a fake key to the real openrouter.ai, which rejected it. No real model answered any test, no real Gemini
key was used, and no money was spent.
