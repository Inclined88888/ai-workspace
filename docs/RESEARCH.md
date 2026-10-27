# Research: what other projects do, and what ROOKS took from them

Checked on 2026-10-27.

## How each fact was checked

For each repository:

- **Licence.** Read from its licence file at the default branch head.
- **Last commit.** The date of the newest commit on the default branch.
- **What it does.** Taken from the top of its README.

All of it was fetched with `git clone --filter=blob:none`.

What was not checked:

- **Star counts.** The GitHub API was not available from the build environment. No star figure appears here and
  none should be inferred.
- **Capabilities.** These are what each project says about itself. None of them was installed, benchmarked or
  tested for compatibility.

## Shortlist

| Project | Licence (verified) | Last commit | Why it matters to ROOKS | Decision |
|---|---|---|---|---|
| openclaw/openclaw | MIT | 2026-10-27 | Personal assistant across chat apps; models are swappable plugins; state stays local | Pattern adopted earlier: primary model plus backups (fallbacks) |
| NousResearch/hermes-agent | MIT | 2026-10-26 | Learning loop that creates and improves skills; many providers | Pattern adopted earlier: skills from experience. Already an optional add-on |
| langchain-ai/langmem | MIT | 2026-10-02 | Extracts memories from conversations and keeps them current | Pattern adopted: memories are updated in place (`replaces`) instead of piling up contradictions |
| letta-ai/letta | Apache-2.0 | 2026-10-18 | Stateful agents with memory. README says active code moved to `letta-ai/letta-code` | Idea only: memory provenance (source, conversation, previous value) |
| mem0ai/mem0, getzep/graphiti, topoteretes/cognee | Apache-2.0 | 2026-10 | Memory layers and knowledge graphs | Not adopted: each adds a service or vector store. SQLite FTS covers current scale |
| asg017/sqlite-vec | MIT or Apache-2.0 (dual files) | 2026-05-17 | Vector search inside SQLite | Candidate for later semantic recall without a new service. Not added: needs a native extension in the image |
| temporalio/temporal, hatchet-dev/hatchet, PrefectHQ/prefect | MIT / MIT / Apache-2.0 | 2026-10 | Durable workflows, retries, resumable jobs | Pattern adopted at small scale: interrupted research can be retried, and company tasks re-queue on restart. No workflow engine added (too heavy for the core) |
| unclecode/crawl4ai | Apache-2.0 | 2026-10-26 | Turns web pages into LLM-ready Markdown | Not adopted: trafilatura already does extraction in-process |
| browser-use/browser-use | MIT | 2026-10-26 | Browser agent. README advertises CAPTCHA handling and "stealth" browsers | **Rejected**: CAPTCHA and anti-bot evasion conflict with ROOKS rules |
| firecrawl, skyvern | AGPL-3.0 | 2026-10 | Crawling and browser automation | Not adopted: AGPL, and overlaps existing features |
| ItzCrazyKns/Perplexica (README now says "Vane") | MIT | 2026-09-01 | Self-hosted answer engine with sources | Idea already present: Deep Research with citations |
| searxng/searxng | AGPL-3.0 | 2026-10-25 | Metasearch | Kept as an optional, unmodified add-on container |
| mostlygeek/llama-swap | MIT | 2026-10-25 | Hot-swaps local models behind one API | Candidate add-on for people running several local models. Not added |
| BerriAI/litellm | MIT, except an `enterprise/` folder under separate terms | 2026-10-26 | Gateway to 100+ providers | Not adopted: ROOKS's own model layer covers the providers it supports, without a dependency |
| block/goose | Apache-2.0 | 2026-10-26 | General desktop agent | Reference only |
| modelcontextprotocol/servers | MIT, moving to Apache-2.0 (README) | 2026-10-24 | Reference MCP servers | ROOKS already serves MCP |
| ComposioHQ/composio | MIT | 2026-10-26 | Hosted toolkits and auth | Not adopted: a cloud dependency and an account requirement |
| promptfoo/promptfoo | MIT. README says it is now part of OpenAI | 2026-10-27 | Evals and red teaming | Candidate for a separate test harness. Not part of the app |
| guardrails-ai/guardrails | Apache-2.0 | 2026-08-26 | Output validators | Not adopted: the specific checks ROOKS needs (secrets, approvals) are written directly |
| OWASP Top 10 for LLM Applications | CC BY-SA 4.0 | 2026-08-05 | Threat list | Used as a checklist for the security work below |

Projects that were probed but not shortlisted, with what was verified:

| Licence | Projects | Last commit |
|---|---|---|
| MIT | agent-zero, OpenHands, nanobot, nanoclaw, pydantic-ai, langgraph, microsoft/agent-framework, llama_index, supermemory, stagehand, ScrapeGraphAI, ollama, llama.cpp, LocalAI, opencode | All 2026-10 |
| Apache-2.0 | open-interpreter, ragflow, haystack, qdrant, playwright, cline, E2B, gvisor, deepeval, vllm, opentelemetry-python, ossf/scorecard | All 2026-10 |
| Apache-2.0 | aider | 2026-05-22 |
| Apache-2.0 | ragas | 2026-02-24 |
| Apache-2.0 (the file opens with an NVIDIA copyright line) | NVIDIA/OpenShell | 2026-10-26 |
| MIT or Apache-2.0 (dual) | zeroclaw | 2026-10-24 |
| BSD-3-Clause | jupyter/notebook | 2026-10-20 |
| Elastic License 2.0 (not open source by the OSI definition) | Arize Phoenix, Nango, Airbyte | All 2026-10 |
| Fair-code, with portions under separate terms | n8n | 2026-10-26 |
| Fair-code, with portions under separate terms | activepieces, langfuse | All 2026-10 |

One more repository, daytonaio/daytona, has only `README.md` and `assets` at its default branch head (last commit
2026-06-25). It looks moved or archived, and its licence could not be determined.

## Capability matrix

The ROOKS column describes code in this repository. "New" means it was added in this round, with tests.

| Capability | Before this round | Now |
|---|---|---|
| Model providers, fallbacks, clear errors | Present | Unchanged |
| Tool loop prevention | Step cap only (12) | **New**: a third identical call (same tool, same arguments) in one turn is refused. After two refusals the turn ends with a plain message |
| Turn time limit | Per-tool timeout only | **New**: wall-clock budget per turn (`WORKBENCH_TURN_SECONDS`, default 300). Tools are cut off at the remaining budget. The user is told the result is partial |
| Stop and cancel | Present | Unchanged |
| Approvals | Fixed set of memory and schedule tools, only after outside content | **New**: per-group "Ask me first" for any write or consequential action, on every channel |
| Tool permissions | None | **New**: 11 groups, each set to Allowed, Ask me first, or Off. Off hides the tools from the model, and the backend refuses them if named anyway. The same groups apply to the Open WebUI tool server and MCP clients. Unknown tools are never treated as free |
| Risk levels | None | **New**: every tool is tagged read, write or consequential. A test fails if a tool has no group |
| Secrets kept out of memory | None | **New**: memory refuses or skips API keys, tokens, private keys, "password is …" phrasing and Luhn-valid card numbers. Applies to the remember tool, automatic learning, import, and manual add and edit |
| Memory contradictions | Duplicate filter only | **New**: learning can replace an outdated memory. Only memories the model was shown can be replaced, and the previous text is kept |
| Memory provenance | Source and conversation | Plus the previous value on update |
| Audit trail | None | **New**: covers sign-ins (including failures), password changes, setup, key and provider changes, permission changes, approvals and denials, and every non-read tool action by the assistant, agents, Telegram or schedules. Details pass through the secret filter. The 20,000 newest rows are kept. API at `/api/audit`; view under Settings, Security |
| Integration health | Scattered across pages | **New**: one Connection health list covering providers, search, calendars, mail, Telegram and the browser add-on, built from stored state with no outside calls |
| SSRF protection for page reads | Private, loopback, link-local, reserved and multicast addresses blocked; redirects re-checked | Stronger: only globally routable addresses are allowed (adds CGNAT 100.64/10, IPv4-mapped IPv6, 0.0.0.0). URLs that embed credentials, and schemes other than http and https, are refused. **New**: the connected peer address is checked after connecting, which catches DNS rebinding. Bodies are streamed with a 2 MB cap instead of downloaded whole |
| Research jobs | Interrupted jobs marked failed | **New**: Retry button and `POST /api/research/{id}/retry`. Depth is stored. **Fixed**: starting research crashed with "no running event loop" |
| Company tasks | Re-queued on restart; "Run again" button | Unchanged (already present) |
| Semantic (vector) recall | Keyword FTS5 | Not done. sqlite-vec is the candidate |
| Arbitrary shell execution | Not exposed | Still not exposed |

## Phase 2 additions

| Capability | Now |
|---|---|
| Autonomous multi-step work | **Missions** (`missions.py`). Lifecycle: planning, review, queued, running, waiting for approval, paused, then done, partial, failed or cancelled. Each step has a "done when" criterion and is checked independently. Code rules prevent unsupported "done". Progress is saved after every step, so a restart resumes the running step; actions already taken are replayed, never repeated. Limits on tool calls, minutes and spend; stall detection; one retry. Idea sources: the durable execution patterns of Temporal, Hatchet and Prefect, adapted at small scale with no workflow engine |
| Memory retrieval | Hybrid keyword retrieval with fusion, confidence, near-duplicate folding, revert and optional embeddings (`memory.py`, `docs/MEMORY.md`). Idea sources: the memory update and provenance ideas of LangMem and Letta; sqlite-vec was considered but not added (native extension) |
| Deep Research | Sub-question planning, all search providers, robots.txt, canonical URLs and duplicate folding, credibility, freshness and relevance scores, quote-verified claims, a structured report and a code citation check. Cancel, then run again |
| Tools | Registry (`/api/tools`), argument validation, per-tool time limits, one retry for reads only, an execution log of every run (`/api/tool-runs`), skills checked for secrets and disabled tools |
| Integrations | Optional GitHub (search and read; issue and comment creation always needs approval) |
| Upgrades | `scripts/upgrade.sh`: checksum, safe extraction, conflict detection from a manifest, code and database backup, rollback |

## Limits that remain

- **DNS rebinding through a proxy.** With an outbound HTTP proxy configured, the peer address is the proxy itself,
  so only the name check applies.
- **Permissions for outside clients.** The Open WebUI tool server and MCP clients follow the same permission groups.
  They have no approval screen, so "Ask me first" lets them read but refuses their writes. They still share one
  tools key (`TOOLS_API_KEY`), so ROOKS cannot tell two MCP clients apart.
- **What the secret filter misses.** It matches known formats. A secret in an unknown format written as plain words
  will not be caught.
