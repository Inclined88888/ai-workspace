# AI Workspace docs

A self hosted AI workspace in one light container. Start with the [README](../README.md) for install and a tour of the features.

- [API reference](API.md)
- [Security audit](AUDIT.md)
- [Phase 0 gate](PHASE0-GATE.md)
- [Releases](RELEASES.md)
- Design notes: [run log and connections](design/0001-run-log-and-connections.md), [adapters](design/0002-adapters.md)

---

# 100 reasons people pick AI Workspace

> Items marked **(Phase N)** are on the roadmap, not shipped. Everything else is in the product today.

## Weight and footprint

1. Core image is 248 MB (CI, unpacked layers), not multi-GB.
2. ~100 MB RAM at idle, measured outside Docker.
3. Runs comfortably on a €4 VPS.
4. No GPU required for the core.
5. Every add-on is off by default — you pay nothing you don't use.
6. Compose profiles add weight only when you ask for it.
7. Native Ollama on Mac is supported, so the GPU is actually used.
8. The same image runs on a Mac and a small Linux server.
9. Upgrades keep your `.env` and data.
10. Re-running the installer is safe and idempotent.

## A workspace, not just an agent runtime

11. One app (Workbench) holds every core feature.
12. No wiring six services together before you get value.
13. Notes with AI writing assist are built in.
14. Tasks are built in.
15. Calendar is built in (ICS read, CalDAV read+create, local).
16. Mail is built in (IMAP/SMTP) with draft-only safety.
17. Research with citations is built in.
18. Side-by-side model comparison is built in.
19. Searchable history across every conversation is built in.
20. The AI can act on all of it through tools, not just chat about it.

## Memory that earns its keep

21. Memory extraction runs automatically after each turn.
22. Capped at 5 durable items per turn so memory doesn't bloat.
23. 40-character minimum stops trivial messages polluting memory.
24. Deduplication is built in.
25. Recall uses FTS5 bm25 — no vector database required.
26. Relevant memories are injected into the system prompt.
27. Memory can be switched off entirely.
28. Memory is exportable and importable as JSON — yours to keep. **(Phase 2)**
29. Memory scopes for personal, project, and company. **(Phase 2)**
30. A memory card shows what the AI recalls, with edit and forget inline. **(Phase 2)**

## Skills you can see and control

31. Skills are auto-saved after 3+ tool calls in a turn.
32. Skills are listed in the system prompt and loaded on demand.
33. Manual skill creation: name, template, optional tool allowlist. **(Phase 2)**
34. A skills gallery to browse and edit. **(Phase 2)**
35. Skills triggerable from the API and CLI without a chat turn. **(Phase 2)**
36. Delegated agents can auto-load a named skill. **(Phase 3)**
37. Skills are plain text — inspectable, portable, diffable.
38. No untrusted skill marketplace by default — supply chain risk stays out.

## A company of agents with real controls

39. Mission plus an org chart with titles and `reports_to`.
40. `reports_to` is loop-checked.
41. Delegation depth is capped at 3.
42. Monthly budget per agent, in cents.
43. Monthly token limit per agent.
44. Budget is checked before every model call.
45. Overshoot is bounded to one call — spend is predictable.
46. Over-budget agents auto-pause.
47. One worker runs queued tasks — no runaway parallelism.
48. Kanban: To do, Queued, Running, Done, Needs you.
49. Mission control dashboard for spend, tokens, and stuck tasks. **(Phase 3)**
50. Company templates instantiate in one click. **(Phase 3)**

## Safety by construction

51. Secrets encrypted at rest with Fernet.
52. Encryption key derived from `APP_SECRET`.
53. CSRF header required on every non-GET `/api` call.
54. Session cookie auth.
55. CLI bearer token stored only as a SHA256 hash.
56. Telegram user-ID allowlist.
57. Mail is draft-only — it never sends.
58. No shell tool enabled by default.
59. Tool step limit of 12 per turn.
60. Tool timeout of 180 seconds.
61. Scheduled runs cannot create schedules.
62. `next_run` is booked before running, so failures cannot loop.

## Privacy and local-first

63. Everything stays inside your container.
64. SQLite local file — no external database.
65. No telemetry.
66. No account required.
67. No vendor lock-in.
68. Data export and delete are documented. **(Phase 6)**
69. Model API keys are stored encrypted, locally.
70. Works fully offline with local Ollama.

## Model freedom

71. OpenAI-compatible layer, so most providers just work.
72. Explicit `providerId/model` refs — no guessing which model ran.
73. Presets for Ollama, OpenAI, Anthropic, OpenRouter, Gemini, OpenCode Go, OpenCode Zen.
74. Free models marked "(free)" in menus.
75. OpenRouter real cost tracked per call.
76. Custom per-1M-token prices for other providers.
77. Never sends temperature, so recent Claude models don't reject requests.
78. Anthropic `max_tokens` handled correctly.

## Automations

79. Cron schedules via croniter.
80. IANA timezone support.
81. Results land in an Inbox.
82. Optional Telegram delivery.
83. Safe by design — see 61 and 62.
84. Telegram `/new` starts a fresh conversation.
85. Automations are core, not an add-on.

## Channels

86. Telegram bot built in (long polling, allowlist, encrypted token).
87. Signal as an optional add-on with an allowlist. **(Phase 4)**
88. WhatsApp as an optional add-on with an allowlist. **(Phase 4)**
89. Discord and Slack as thin, scoped adapters. **(Phase 4)**

## Install and ops

90. Install is one `curl | bash`.
91. `install.sh` wraps everything in `main()`, so a cut-off download runs nothing.
92. OS and Docker checks with helpful prompts instead of silent failures.
93. Tarball fallback when git is missing or is only the macOS stub.
94. `setup.sh` is bash 3.2 compatible, so stock macOS works.
95. Prints the URL and a one-time setup code when done.

## Extension without the bloat

96. Minimal add-on manifest spec. **(Phase 5)**
97. Build-your-own add-on guide with a 20-line working example. **(Phase 5)**
98. Community add-on registry, but never auto-installed. **(Phase 5)**

## Credibility

99. Measured numbers are published, reproducible, and updated when they drift.
100. The README carries an honest tested/untested table, and the compare page states rivals' strengths plainly.
