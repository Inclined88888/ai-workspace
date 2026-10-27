# ROOKS v10 research: FYI, Odysseus (research), Odysseus (coding workers), calling

Date: 2026-10-27. Sources were read directly; claims I could not confirm are marked **unverified**. No source code from
these projects was copied into ROOKS. Everything below was reimplemented independently, in ROOKS's own architecture.

## 1. FYI (Focus Your Ideas, fyi.ai)

### Verified
These come from the App Store version history and description, and the fyi.ai homepage.

| Capability | Evidence |
|---|---|
| Agent conversation calls with a "Listening Mode" (user-started) | App Store 0.81 (Sep 22): "Talk to your Agent, with Listening Mode"; 0.83 (Oct 12): "New Agent convo calls mode (including Listening Mode)" |
| Agent can be added to chats, projects and calls | App Store 0.81 and 0.83 |
| Voice personas: choose, or build your own (expression, abilities, knowledge, mission) | App Store description; 0.69 (Jan 16), 0.71 (Jan 29) |
| Multilingual voice; tap to speak, tap X to cancel | App Store 0.61 (Jan 20, 2025) |
| Voice notes transcribed and searchable | App Store description |
| Agent-hosted radio you can interrupt with questions | fyi.ai (RAiDiO.FYI), App Store 0.63 to 0.83 |
| Agent handles routine replies and scheduling, escalating "only what actually needs you" | fyi.ai Agentic Messenger section |
| Projects holding briefs, files and calls | fyi.ai |

### Not verified
* **The August 2026 "V2" with proactive, agent-initiated check-ins and calls: not found.** I checked the homepage
  (the word "PROACTIVE" appears once, with no description), the App Store history (no proactive outreach, no V2) and
  web searches.
* **A search snippet describing "an AI agent with its own phone number that texts, calls… 24/7".** The live
  homepage, fetched twice, says neither.
* **End-to-end encryption ("ECDHE and ECDSA") and "delete means deleted".** These are marketing claims I can't
  check.

### What ROOKS takes from it
These are concepts only.

* Projects as the shared context for conversations and calls.
* An agent that escalates only what needs you.
* Voice personas.
* Interruptible spoken responses.

Prior art for gating proactive calls: a public pull request on `ferni-ai/voiceai` (#848) puts check-in calls behind
an off-by-default flag, at most one a day, inside allowed call hours and outside quiet hours. ROOKS uses the same
kinds of gates.

## 2. Odysseus research workspace (github.com/odysseus-dev/odysseus)

* **License:** AGPL-3.0. Reusing its code would put ROOKS under the AGPL network clause, so nothing was copied.
* **Read:** `src/deep_research.py` (1,981 lines), `src/research_navigator.py`, `src/agent_evidence.py`, at commit
  4d0610a.

### Verified from the source

| Concept | Where |
|---|---|
| Iterative rounds. Each round: plan actions, generate queries, search, fetch, extract, synthesize an evolving report, then decide whether to stop | `DeepResearcher` docstring and `run` loop (lines 337 to 620) |
| Bounds: `max_rounds=8`, `max_time=300` s, `max_urls_per_round=3`, `min_rounds=2`, `max_empty_rounds=2`, per-phase timeouts, cooperative `cancel()` | constructor, lines 345 to 410 |
| Bounded concurrent extraction (`asyncio.Semaphore(extraction_concurrency)`, 1 to 12) | lines 1136 to 1225 |
| Deterministic depth gate before the model may stop: under 2 useful findings, under 2 sources, no primary or official source (when under 4 findings), weak best source | `_needs_more_evidence_before_stop`, line 1678 |
| Source credibility heuristics: official .gov/.edu = 90, primary vendor = 82, docs or release notes = 78, community = 45, commercial or listicle = 40, adjusted for freshness | `research_navigator.assess_source`, lines 96 to 131 |
| Model decides YES/NO to stop, failing open to "continue" (bounded by `max_rounds`) | `_should_stop`, line 1737 |
| Synthesis over a sliding window of findings | `_synthesize` |
| Inline URL citations in the report | prompts, lines 168 and 229 |

**Practical limits:**
* The evidence is the model's evolving report text, not a claim-level ledger.
* Stopping relies on the model plus heuristics.
* It depends on SearXNG or a search provider.

### What ROOKS already had
`app/research.py`:
* sub-question planning;
* search across providers;
* robots.txt;
* per-source claim extraction with quotes checked against the page;
* citation cleanup;
* cancel, retry and budgets.

### Gaps adopted, implemented natively
* Bounded follow-up rounds driven by a gap analysis.
* A source credibility and freshness score.
* A persistent evidence ledger (claim, source, dates, excerpt, kind, verification, contradictions).
* An independent verification pass.
* Saving to a project, and opt-in monitoring.

## 3. Odysseus coding-worker delivery (github.com/jpolec/odysseus)

* **This is the intended project:** "The free, local delivery system for coding agents"; runs Codex or Claude CLI
  in isolated git worktrees.
* **License:** MIT (Copyright 2026 Takuya Matsuyama, Jakub Polec).
* **Read:** `odysseus/worker_leases.py`, `scheduler.py`, `attention.py`, `evaluation.py`, at commit 107bb32
  (2026-08-22).

### Verified from the source

| Concept | Where |
|---|---|
| Durable per-run worker leases with monotonically increasing fencing epochs: a stale worker cannot change a run it no longer owns (`StaleWorkerLease`) | `worker_leases.py` |
| Persistent scheduler with bounded concurrency (`max_parallel` minus active) | `scheduler.py` line 153 |
| Implement, check, review workflow; retries carry the failing checks' context into the next attempt | `scheduler.py` lines 592 to 722 |
| Provider credit or quota exhaustion is non-retryable: the run pauses and asks the operator to choose (switch lane, refill and retry, continue) | `scheduler.py` lines 65, 404 to 440 |
| Evaluation: weighted deterministic checks (0.45), independent review (0.40), review independence (0.15); default threshold 0.85; human review required by default; the same lane reviewing its own work is penalised | `evaluation.py` |
| A "Needs You" attention queue with typed items (review, question, permission_request, blocked, decision_required, evaluation_failed, ci_failed, budget, stalled…) and priorities | `attention.py` |

**Practical limits:**
* It needs tmux, git and an agent CLI.
* It is built for coding work, not general tasks.

### Adopted natively in ROOKS workers
* Lease epochs, so recovery can't double-run a task.
* Bounded parallelism.
* Retries carrying failure context.
* An independent reviewer that must not be the worker's own run.
* Human review by default when unverified.
* Budget exhaustion as an attention item, not a retry.
* Typed Needs You items.

### Rejected
* Installing Odysseus or tmux.
* Driving external CLI agents.
* Worktrees for non-coding work. ROOKS's runner workspace already isolates coding.

## 4. Optional telephone calls

| Item | Finding |
|---|---|
| Provider | Twilio Programmable Voice: REST `POST /2010-04-01/Accounts/{AccountSid}/Calls.json` with `To`, `From` and inline `Twiml`. HTTP basic auth with the Account SID and Auth Token. |
| Price (Twilio AU page; no effective date shown) | Outbound to Australian mobiles US$0.0750 per minute, local US$0.0252 per minute; number rental listed inconsistently (US$8.25 to US$20 a month). Plivo benchmark: US$0.058 per minute to mobiles (May 2026). **Check before use.** |
| Two-way phone conversation | Needs Twilio to reach ROOKS from the internet (webhooks or media streams). That conflicts with ROOKS's no-public-exposure rule, so it is **not built**. ROOKS places one-way spoken briefings; you reply in the app. |
| Australian rules | The Telecommunications (Telemarketing and Research Calls) Industry Standard 2017 covers calls promoting goods or services, donations or research. Calls to yourself, about your own matters, at your request are outside it. ROOKS still uses its hours as defaults (weekdays 9am to 8pm, weekends 9am to 5pm) and always identifies itself as an AI at the start. Recording consent differs by state's listening-device laws, so ROOKS never records calls. The Privacy Act 1988 applies to the number and transcripts kept. This is engineering guidance, not legal advice. |

Sources:
[fyi.ai](https://fyi.ai/) ·
[FYI.AI on the App Store](https://apps.apple.com/us/app/fyi-ai-focus-your-ideas/id1554341467) ·
[ferni-ai/voiceai PR #848](https://github.com/ferni-ai/voiceai/pull/848) ·
[odysseus-dev/odysseus](https://github.com/odysseus-dev/odysseus) ·
[jpolec/odysseus](https://github.com/jpolec/odysseus) ·
[Twilio Voice pricing, Australia](https://www.twilio.com/en-us/voice/pricing/au) ·
[Plivo Australia pricing](https://www.plivo.com/sip-trunking/pricing/au/) ·
[ACMA Do Not Call industry standards](https://www.donotcall.gov.au/industry/industry-overview/industry-standards) ·
[HSF Kramer on the 2017 standard](https://www.hsfkramer.com/insights/2017-04/new-standard-for-telemarketing-and-research-calls)
