# ROOKS capability roadmap: 150 requirements

Status as of 2026-10-27 (v12, branch `development/rooks-v12`). Items 101 to 117 were added in v10, items 118 to 129 in v11, items 130 to 141 in v11.1, items 142 to 150 in v12. Linux was the only test environment: no macOS result in this file comes from a Mac.

## How statuses are assigned

| Status | Meaning |
|---|---|
| **verified** | The code exists and an automated test that exercises it passed in this release. The evidence column names the test. |
| **implemented** | The code exists, but is untested, tested only against a stand-in, or untested on the target platform. |
| **partial** | Some of the requirement exists. The gap is stated. |
| **not started** | Nothing meaningful exists yet. |

Test files are referred to by short name:

- Backend tests: `workbench/backend/tests/test_*.py`
- Companion tests: `companion/tests/`
- `e2e`: `companion/tests/e2e/e2e.py`
- "browser suites": the Playwright UI suites run against the built app (scratch harnesses, not committed), except those in `workbench/tests/browser/` (committed from v12)

Priority: **P1** next, **P2** soon, **P3** later. Complexity: **S** small, **M** medium, **L** large.

## Summary

Counted from the tables below.


| Status | Count |
|---|---|
| verified | 88 |
| implemented | 17 |
| partial | 34 |
| not started | 11 |

## Memory and context (1 to 10)

| ID | Requirement | Status | What exists (modules) | Evidence | Pri | Cx | Security | Acceptance / next (dependencies) |
|---|---|---|---|---|---|---|---|---|
| 1 | Long-term memory | verified | `memory.py`, `learn.py`, memories table with provenance | test_assistant `test_memory_is_learned...`, test_memory | P2 | M | Secret filter on every write | Learned facts recalled in later chats ✔ |
| 2 | Project memory | verified | `projects.py`, `memory.scope_sql`: facts saved in a project are recalled only there; profile and preferences stay user wide; learning, search_memory and forget are scoped | test_projects `test_memories_saved_in_a_project_stay_in_it`, `test_isolated_project...`; v9 UI suite (15) | P3 | M | A project conversation cannot read or delete another project's memories | Missing: moving a memory between projects in the UI |
| 3 | Reliable context retrieval | verified | Hybrid FTS, equivalents, typo fix, fusion | test_memory benchmark: 0.62→1.00 dev, 0.44→0.92 held-out (author-written sets; see MEMORY.md) | P2 | M | — | Independent eval set; real embeddings measured |
| 4 | Memory corrections | verified | Edit, revert, supersede with previous wording | test_memory `test_correction_revert...`, mission browser suite (correct and undo) | P3 | S | Audit records corrections | ✔ |
| 5 | Tool awareness | verified | `/api/tools` registry, validated arguments, skill warnings | test_tools_registry | P3 | S | — | ✔ |
| 6 | Project isolation | partial | Projects with brief, goals, instructions, model, monthly budget, local-only, "kept separate" memory, narrowed permissions; archive, bin, delete for good with the name typed | test_projects (14 tests), v9 UI suite | P2 | L | Permissions can only be narrowed; local-only refuses cloud models, including fallbacks | Missing: uploaded files and Research runs cannot be assigned to a project from the UI (the API accepts it for research); no per-project folders for Computer Use |
| 7 | Context compression | verified | Messages older than the 40 message window are folded into a running summary after the turn (`learn.summarize`), shown in the prompt | test_projects `test_old_messages_are_summarised_into_the_prompt` (stand-in model) | P3 | M | Summaries pass the secret redactor; prompt says quoted content is data | Summary quality depends on the model; not measured with a real one |
| 8 | Personal preferences | verified | Preference memories in every prompt; appearance settings | test_safety (recall), test_ux (appearance) | P3 | S | — | ✔ |
| 9 | Semantic retrieval | implemented | Optional provider embeddings (`memory.py`) | test_memory with a stand-in model only | P2 | M | Memories go to the embedding provider (UI says so) | Measured with a real local embedding model |
| 10 | Memory privacy | verified | Secret and card filter, Memory Off, export, import, delete | test_hardening, test_safety, test_memory | P2 | S | Core control | Retention controls (86) |

## Autonomy (11 to 20)

| ID | Requirement | Status | What exists | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 11 | Autonomous completion | verified | Missions: plan, approve, run, verify, report; templates; step dependencies; configurable retries (1 to 5); stop for review on unverified steps | test_missions, test_projects (dependencies, review), e2e Mission and T, mission browser suite | P3 | L | Plan approval before any action | ✔ |
| 12 | Outcome verification | verified | Per-step checker plus rules the model cannot override; research citation and quote checks; v10: worker acceptance check, an independent reviewer in its own conversation, links must trace to what a worker read, research claims re-read against their passage | test_missions `test_claimed_done_without_a_tool...`, test_research, test_workers `test_a_worker_gets_only_its_roles_tools...`, test_scenarios_v10 D | P2 | M | — | ✔ |
| 13 | Failure recovery | verified | Step retry, mission resume, research retry, computer "unconfirmed" state; a mission resumed after a restart with a computer action in flight is told to observe first | test_missions, test_hardening, test_projects `test_recovery_tells_the_step_to_look_first`, e2e G | P3 | M | No blind repeats | ✔ |
| 14 | Loop prevention | verified | Identical-call refusal, stall detection, turn time budget | test_hardening, test_missions | P3 | S | — | ✔ |
| 15 | Checkpointing | verified | Mission state saved per step; action replay | test_missions recovery, mission browser suite (process killed with kill -9) | P3 | M | — | ✔ |
| 16 | Long-running tasks | partial | Missions up to 10 steps with dependencies, minute and spend budgets | test_projects | P2 | L | Budgets cap cost | Missing: sub-plans, more than 10 steps, multi-hour runs measured |
| 17 | Parallel execution | verified | v10 workers: a persistent queue with bounded parallelism (1 by default, up to 4), lease epochs and atomic budget reservations. Missions themselves still run one at a time; computer work is never started automatically | test_workers `test_parallel_workers_cannot_spend_more...`, test_scenarios_v10 H | P3 | L | Shared computer stays serialized: computer workers wait for you | Missions in parallel with each other |
| 18 | Project continuity | verified | Every turn in a project starts with its brief: goals, instructions, decisions, open questions, open tasks, recent missions and what happened (episodes from finished missions) | test_projects `test_brief_notes_and_linked_work`, `test_mission_in_project...` (episode memory), v9 UI suite | P3 | M | Brief text is the user's own project data | ✔ |
| 19 | Partial results | verified | "Partial" mission status; time-budget partial answers | test_missions, test_hardening | P3 | S | — | ✔ |
| 20 | Proactive task monitoring | verified | v10 check-ins: deterministic facts from tasks, calendar, missions, approvals, a project, a page or a search; a briefing only when something is new | test_checkins (14 tests) | P2 | M | — | ✔ |

## Computer Use (21 to 30)

| ID | Requirement | Status | What exists | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 21 | Native application interaction | implemented | Companion; macOS adapter hardened in v9 (focus verification, Retina and multi-display coordinates, menus, set value, permission probes, timeouts); X11 adapter | e2e A, D (real X11 display). macOS: 11 tests against a **mocked** osascript only; `macos_check.py` on-device harness written, **never run on a Mac** | P1 | L | Hard blocks, approvals, local re-checks | Run `companion/tests/macos_check.py` on a real Mac and record the result |
| 22 | Visual computer control | implemented | Opt-in visual targeting (`vision.py`): image test gate, consent naming the destination, local-only, per-screenshot or 1 hour session, password fields blacked out on the computer, normalized coordinates, moved-window refusal, confidence threshold, no fallback model | test_vision (5), e2e V (4 checks on a real X11 window with a **stand-in** vision model that decodes the pixels) | P1 | L | Screenshots never stored or logged; never shown to the chat model; blocked apps never captured | Test with a real vision model (local and cloud) and on macOS |
| 23 | Screen understanding | implemented | Accessibility tree as text; visual fallback for controls accessibility does not expose | e2e A, V (stand-in vision model) | P2 | L | Secure fields omitted or blacked out | Real vision model measurement; OCR of whole windows not built |
| 24 | Isolated desktop sessions | partial | ROOKS's browser uses its own profile; desktop actions use your session | e2e C | P3 | L | — | Separate macOS user or VM option |
| 25 | UI change recovery | verified | Element ids expire after changes; labels re-checked | test_engine, e2e F | P3 | S | Prevents wrong-target clicks | ✔ |
| 26 | Dialog handling | partial | Buttons like Don't Save, Replace, Move to Trash, Delete, Send are consequential and need approval; menu tool for Save As | test_engine (consequential labels, not dialog-specific) | P2 | M | Dialogs often confirm destructive actions | Missing: dialog and sheet detection; a test with a real save dialog |
| 27 | Window switching | verified | Target app brought forward before input | e2e D and E (after ROOKS's browser covered the app) | P3 | S | — | Verify on macOS (21) |
| 28 | Target verification | verified | Label must match the real element; consequential labels need approval | test_engine, e2e F | P3 | S | Core control | ✔ |
| 29 | Emergency stop | verified | Stop on page, bar and computer; aborts mid-typing | e2e E (typing stopped at 160 of about 3,150 characters), UI suite, test_engine | P3 | S | Core control | ✔ |
| 30 | Secure remote dispatch | partial | Outbound-only companion, pairing codes, private network only by default | test_computer | P3 | L | Remote stays off by default | Device pairing with TLS for remote use |

## Browser and research (31 to 40)

| ID | Requirement | Status | What exists | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 31 | Browser workflows | verified | Companion browser tools; browser add-on | e2e C | P2 | M | Submit, buy and similar need approval | ✔ |
| 32 | Authorized authenticated sessions | partial | Persistent ROOKS browser profile; you log in yourself | — | P2 | M | Never stores passwords; no CAPTCHA bypass | Documented manual login flow, tested |
| 33 | Browser layout changes | implemented | Refs expire after navigation; labels re-checked | Not directly tested in the browser | P2 | S | — | A test with a changed page |
| 34 | Structured extraction | partial | Research claim extraction; page element lists | test_research | P2 | M | — | Extract a table to a spreadsheet or CSV |
| 35 | Multi-source research | verified | All search providers, sub-questions | test_research | P3 | M | robots.txt honoured | ✔ |
| 36 | Source verification | verified | Quote-checked claims, citation cleanup | test_research | P3 | M | — | ✔ |
| 37 | Contradictory evidence | partial | v10: the gap review names disagreeing claims, which the evidence ledger links both ways and lowers in confidence; the report's disagreement section | test_research `test_a_gap_starts_a_bounded_second_round...` | P2 | M | — | Automatic numeric conflict detection (still found by the model) |
| 38 | Browser retries | partial | Read tools retry once; browser actions do not | test_tools_registry | P3 | S | Never retry writes blindly | Retry navigation-only actions |
| 39 | Website monitoring | not started | — | — | P2 | M | robots.txt and rate limits | Scheduled page checks with change alerts |
| 40 | Research deliverables | verified | Structured report with Limitations; saved as a document with its evidence table, into a project | test_research `test_a_gap_starts...` (save into project), Research UI suite (9) | P3 | S | — | ✔ |

## Files and documents (41 to 50)

| ID | Requirement | Status | What exists | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 41 | File search | verified | Uploaded files, full-text search | test_files | P3 | S | — | ✔ |
| 42 | Cloud file handling | not started | — | — | P2 | L | OAuth scopes | Google Drive or OneDrive connector |
| 43 | Safe file destinations | verified | Allowed folders only, no overwrite without approval, no deletion | test_engine, e2e B | P3 | S | Link escapes refused | ✔ |
| 44 | File versioning | verified | Companion `files.py`: version kept before every replace, verified writes, identical writes skipped, undo with conflict check, nothing deleted; history and Undo on the Computer page | companion test_files (7), e2e T (original untouched), v9 UI suite (card) | P3 | M | Undo covers ROOKS's file tools only, never edits made by clicking in apps | ✔ |
| 45 | Word editing | verified | .docx read; find and replace or append via python-docx (optional) into a new version file | companion test_files `test_xlsx_and_docx` | P3 | M | Original kept unless overwrite is approved | Formatting-preserving edits beyond text runs; Word or Pages GUI untested |
| 46 | Spreadsheet editing | verified | Direct CSV (stdlib) and .xlsx (openpyxl, optional) read, cell update and de-duplicate, into a new file by default | companion test_files, e2e T (real CSV through the real companion, mission done, original untouched) | P2 | L | Overwrite needs approval and keeps a version | Numbers or Excel GUI editing still untested; formulas are not recalculated |
| 47 | Presentation creation | not started | — | — | P3 | L | — | Generate .pptx |
| 48 | PDF understanding | verified | PDF text extraction | test_files | P3 | S | — | Scanned PDFs (OCR) |
| 49 | Document previews | partial | Markdown preview; downloads | — | P3 | M | Downloads stay attachments | PDF and image previews |
| 50 | Artifact organization | partial | Notes and tasks created in a project are linked to it; project page lists conversations, missions, tasks, notes, research and files | test_projects `test_brief_notes_and_linked_work` | P3 | M | — | Missing: mission deliverables filed as documents automatically |

## Automations (51 to 60)

| ID | Requirement | Status | What exists | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 51 | Reliable scheduling | verified | Cron schedules in your timezone | test_assistant `test_schedule_runs...` | P3 | S | — | ✔ |
| 52 | Restart-safe automations | partial | Next run booked before each run; missed runs are not caught up | — | P2 | S | — | Run missed jobs once after a restart |
| 53 | Event triggers | not started | — | — | P2 | M | Untrusted events must not escalate | Email or webhook triggers |
| 54 | Automation run history | partial | v10 check-ins: every run with status, attempt, new facts, who wrote it, errors, and every delivery; shown in Automations. Scheduled jobs still keep only their last status | test_checkins, voice UI suite | P2 | S | — | Full run log per scheduled job |
| 55 | Scoped unattended permissions | verified | Unattended runs cannot schedule or use the computer; approvals go to the inbox | test_safety, test_computer | P3 | S | Core control | ✔ |
| 56 | Failure notifications | verified | Check-ins retry the same slot (5, 15, 60 minutes), then tell you once; failing check-ins and jobs appear in Needs You | test_checkins `test_failures_retry_the_same_slot_then_tell_you_once`, test_needs_you | P2 | S | — | ✔ |
| 57 | Automation quality evaluation | not started | — | — | P3 | M | — | Score runs against expectations |
| 58 | Reusable workflows | verified | Skills, learned or written | test_assistant, test_tools_registry | P3 | S | Secret check on skills | ✔ |
| 59 | Proactive suggestions | not started | — | — | P3 | M | — | Suggest automations from repeated tasks |
| 60 | Remote task monitoring | partial | Telegram, responsive web app | test_telegram | P3 | M | — | Mission status over Telegram |

## Integrations (61 to 70)

| ID | Requirement | Status | What exists | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 61 | Easy connectors | partial | Providers, search, mail, calendar, GitHub, Telegram, companion | test_models, test_search, test_tools_registry | P2 | M | Keys encrypted | One-click OAuth connectors |
| 62 | Connection health | verified | Health list | test_hardening, test_tools_registry, browser suite | P3 | S | — | ✔ |
| 63 | MCP reliability | implemented | MCP server with permission checks | test_hardening (permission refusal only) | P2 | M | A shared key cannot tell clients apart | Per-client keys; MCP client support |
| 64 | Centralized integrations | implemented | Settings, Connections | browser suites | P3 | S | — | — |
| 65 | Email workflows | partial | Search and drafts; never sends | Earlier suites | P2 | M | Sending stays off | Send with per-message approval |
| 66 | Calendar coordination | partial | ICS and CalDAV read; create events | — | P2 | M | — | Find free time, propose meetings |
| 67 | Business and CRM workflows | partial | Seven workflow templates (spreadsheet cleanup, invoice check, document edit, email drafts, follow-ups, weekly report, inventory) that create reviewed missions | test_projects `test_template...`; e2e T ran spreadsheet cleanup on a real file. The other six were **not run end to end** | P2 | L | Drafts only; new files by default; plans reviewed first | Run each template against real data; no CRM connector |
| 68 | Business knowledge access | partial | Notes, documents, files search | test_files | P2 | M | — | Shared business knowledge base (needs 99) |
| 69 | Cross-app information movement | partial | Tools plus Computer Use | e2e B, C, D (single apps) | P1 | L | Approvals per app | Copy data between two real apps, verified |
| 70 | Integration resilience | partial | Model and search fallback; read retry | test_models, test_search | P3 | M | — | Circuit breakers per integration |

## Models and cost (71 to 80)

| ID | Requirement | Status | What exists | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 71 | Efficient model routing | implemented | Model roles: chat, planning, research, local used automatically; coding, documents, long context selectable | test_projects `test_model_roles_need_evidence...` (planning only) | P2 | M | A role model is used exactly, never swapped | Test the research and chat roles in flow; cheap model for checks |
| 72 | Cost predictions | implemented | Mission estimate from your past missions' spend per step; shown on the mission | Not asserted by a test | P2 | M | Estimate only; caps still enforce | Test, and per-model price based estimate |
| 73 | Usage limits | verified | Monthly limit; agent, mission and project budgets; mission spend caps reserved against the monthly limit before start | test_models, test_missions, test_projects `test_budget_reservations...`, `test_project_budget_stops_work` | P3 | S | — | ✔ |
| 74 | Provider fallback | verified | Free-only backups unless allowed | test_models | P3 | S | — | ✔ |
| 75 | Model capability matching | verified | Capabilities with evidence (tested, reported, unknown): tools, JSON test, image test, context length, local, free; roles refused without evidence | test_projects `test_model_roles...`, test_vision | P3 | M | No silent model switch for screen data | ✔ |
| 76 | Local model support | implemented | Ollama provider | Stand-in models only | P2 | S | — | Live Ollama test |
| 77 | Context management | implemented | History window plus running summary; project brief | test_projects | P2 | M | — | Token-aware context sizing per model |
| 78 | Performance optimization | partial | Light core; no benchmarks | — | P3 | M | — | Measure p95 latency |
| 79 | Model comparison | implemented | Compare page | Not re-tested this release | P3 | S | — | — |
| 80 | Cost attribution | implemented | Usage by model, channel and agent | test_assistant `test_usage_and_prices` (partly) | P3 | S | — | Per-mission cost shown (spend is tracked) |

## Safety and privacy (81 to 90)

| ID | Requirement | Status | What exists | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 81 | Least-privilege permissions | verified | 13 permission groups; Computer Use modes | test_hardening, test_computer | P3 | S | Core | ✔ |
| 82 | Scoped approvals | verified | Per-action approvals; temporary per-app grants | test_computer, test_missions | P3 | S | Core | ✔ |
| 83 | Undo and rollback | partial | Memory revert, upgrade rollback, file undo for ROOKS's file tools | test_memory, test_upgrade, companion test_files | P2 | L | No universal undo is promised for actions in other apps | Missing: undo for desktop app actions is not possible in general; only file tools are covered |
| 84 | Prompt-injection protection | verified | Outside content gates persistent and desktop changes | test_safety, test_computer | P2 | M | Mitigation, not a guarantee | Red-team suite |
| 85 | Secret protection | verified | Encryption; keys never sent to the browser; secret filter | test_models `test_keys_never_reach_the_browser`, test_hardening | P3 | S | Core | ✔ |
| 86 | Data privacy controls | partial | Memory Off, export, screenshot toggle | — | P2 | M | — | Conversation retention settings |
| 87 | Auditable actions | verified | Activity log; tool log without typed text | test_hardening, test_computer | P3 | S | Core | ✔ |
| 88 | Safe plugins | not started | Skills are text only | — | P3 | L | Sandboxing required | Plugin model with permissions |
| 89 | Integration isolation | partial | Add-ons in their own containers; companion as a separate process; the assistant's terminal and file tools run in a separate runner container with only the workspace volume (no database, secrets or published port) | test_workspace (runner process); the runner container built and run with Docker: unprivileged, no /data, no secrets, offline network blocks downloads, browser suite 17/17 against it | P2 | M | A runner that could see ROOKS secrets reports it in Settings | Start the full compose stack with the runner via ./setup.sh |
| 90 | Configuration backups | partial | Upgrade backup includes `.env` | test_upgrade | P2 | S | Backups are mode 600 | In-app backup and restore |

## Product and operations (91 to 100)

| ID | Requirement | Status | What exists | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 91 | Simple installation | verified | One-line installer | Install simulation (earlier release); your own Mac install worked | P3 | S | — | ✔ |
| 92 | Guided configuration | verified | Onboarding and setup checks | ui browser suite (43 checks) | P3 | S | — | ✔ |
| 93 | Clear error reporting | verified | Provider- and search-specific messages | test_models, test_search | P3 | S | — | ✔ |
| 94 | Live progress | verified | Missions (including step review), research, Computer page and bar with screen-share indicator | Browser suites (mission 28, UI 13, v9 15) | P3 | S | — | ✔ |
| 95 | Mobile continuity | partial | Responsive web, Telegram | Browser suites (phone width) | P3 | M | — | Installable mobile web app |
| 96 | Unified interface | verified | One app with grouped navigation including Projects; project context shown in chat and missions | v9 UI suite, earlier browser suites | P3 | S | — | ✔ |
| 97 | Safe updates | verified | `upgrade.sh`: checksum, conflicts (manifest and git), compose-name check, backup, rollback | test_upgrade (6), v6 to v7 rehearsal | P3 | S | — | ✔ |
| 98 | Backup and restore | partial | Upgrade backups and database copy; rollback restores code | test_upgrade | P2 | M | — | Scheduled backups; database restore command |
| 99 | Team collaboration | not started | Single owner | — | P3 | L | Roles and permissions needed | Multiple users |
| 100 | Measurable reliability | partial | Test suites; no runtime metrics | — | P2 | M | — | Success and error rates per tool and mission |

## v10: proactive, voice, calls, workers and research (101 to 117)

| ID | Requirement | Status | What exists (modules) | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 101 | Proactive check-ins | verified | `checkins.py` on the existing scheduler loop (no second scheduler): persistent schedule, slot idempotency, retries keep their slot, restart recovery, cancellation, daily limits, spending switch to a written list | test_checkins (14) | P1 | M | No model call when nothing changed | ✔ |
| 102 | Topic subscriptions | verified | Sources: tasks, calendar, missions, approvals, project, `page:URL`, `search:words`; presets; expiry | test_checkins incl. `test_a_followed_search_reports_only_new_results` | P2 | S | Page sources honour robots.txt | ✔ |
| 103 | Smart reminders | partial | Due and overdue tasks in Needs You and the morning briefing preset | test_needs_you | P2 | M | — | Missing: reminder timing learned from your habits |
| 104 | Briefing generation | verified | Facts compared per key; only new or changed facts go to the model, which may decide nothing is worth interrupting for; written list without a model | test_checkins `test_a_briefing_only...`, `test_the_model_may_decide...`, `test_without_a_usable_model...` | P1 | M | Facts are data, never instructions | ✔ |
| 105 | In-app voice conversations | verified (provider path) | `voice.py`, `components/Voice.tsx`: listen, answer aloud, barge-in by voice, half duplex, microphone indicator, end with transcript, summary or nothing | test_voice (5), voice browser suite (26) with Chromium's fake microphone and a FAKE speech provider | P1 | M | No audio stored; consent per cloud destination; local-only refused | Not verified: browser speech recognition (needs the browser maker's service), a real speech provider, barge-in on real speakers without headphones |
| 106 | Agent-initiated voice sessions | verified | Check-ins set to "call" raise an in-app invitation: accept opens a voice session that starts with the briefing; decline, snooze twice, or miss it for one written briefing | test_checkins `test_call_invitation...`, voice browser suite | P1 | M | Only inside contact hours and daily limits | ✔ |
| 107 | Optional outbound calls | implemented | `calls.py`: provider abstraction, test provider, Twilio REST; one-way spoken briefing that identifies itself as an AI | test_checkins `test_phone_needs_password...` (TEST PROVIDER: no call is placed), `test_twilio_without_credentials...` | P3 | M | Password for every change, verified number, per day, week and monthly cost caps, no retries, never records | Not verified: a real Twilio call (no account was used, no paid call was made). Two-way phone needs a public webhook: not built |
| 108 | Consent and quiet hours | verified | Quiet hours per check-in and workspace; contact windows (defaults from the Australian telemarketing standard hours); voice consent per destination | test_checkins `test_quiet_hours...`, `test_outside_the_contact_window...`, test_voice | P1 | S | Urgent is the only exception to quiet hours, and never for calls outside contact hours | ✔ |
| 109 | Call history and privacy | verified | Invitation and call history; numbers encrypted and masked; no recording; voice transcripts deletable at session end | test_checkins, test_voice, voice browser suite (number never shown in full) | P2 | S | Privacy Act applies to what you keep | ✔ |
| 110 | Worker orchestration | verified | `workers.py`: contracts (goal, inputs, expected output, acceptance, role, scope, model, cap, time limit, retries, dependencies), persistent queue, leases with fencing epochs, dependencies, blocked propagation, cancellation | test_workers (9), test_scenarios_v10 D, E, H; workers browser suite (11) | P1 | L | — | Company agents do not use workers yet |
| 111 | Independent verification | verified | Worker check plus a reviewer that sees only the result and its evidence; rejections retried with reasons; research claims re-read against their passage | test_workers `test_a_rejected_result...`, test_research, test_scenarios_v10 D | P1 | M | — | A reviewer on a different model by default |
| 112 | Worker isolation | verified (permissions) | Role tool allow-lists enforced on every call; scope read, draft or write; writes wait for approval; no approval widens a worker; computer work needs you to start it | test_workers `test_planted_instructions...`, test_scenarios_v10 G | P1 | M | Isolation is by permission inside one process, not an OS sandbox (terminal work still runs in the runner container) | Process isolation per worker |
| 113 | Shared evidence | partial | Workers pass results (as data) to the tasks that wait for them; research evidence ledger saved into a project | test_workers `test_dependencies...`, test_research | P2 | M | — | Missing: workers writing to the evidence ledger |
| 114 | Advanced research rounds | verified | Bounded rounds (1 to 4 by depth), deterministic depth gate plus gap review; stops on enough evidence, no new sources, round, source or time limit; cancel | test_research `test_a_gap_starts...`, `test_rounds_are_bounded_by_depth`, `test_the_depth_gate` | P1 | M | robots.txt honoured | ✔ |
| 115 | Evidence ledgers | verified | `evidence` table: claim, source and URL, title, published and retrieved dates, exact quote and its location, credibility, confidence, verification, contradictions, corroboration, fact, inference or uncertain | test_research, Research UI suite (9, with a SEEDED run) | P1 | M | Quotes are found in the page by code; nothing is cited that was not read | ✔ |
| 116 | Ongoing research monitoring | verified | Follow a research question: a check-in with a `search:` source, opt in and out from the report | test_research (monitor on and off), test_checkins `test_a_followed_search...`, Research UI suite | P2 | S | — | ✔ |
| 117 | Unified attention inbox | verified | `attention.py`, Needs You page: approvals, workers, missions, research, automations, calls, spending, reminders, follow-ups; each says what, why, risk, cost, each choice and what is next; defer | test_needs_you, Needs You browser suite (11) | P1 | M | Single actions only; no blanket approval | ✔ |

## v11: rich answers, discovery and interface (118 to 129)

All browser evidence below used a local fake model and fake providers: it verifies the interface and server contracts, not any real provider.

| ID | Requirement | Status | What exists (modules) | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 118 | Structured answers from real tool results | verified | `blocks.py` (schema v1: records, table, kpis, chart, sources, list, notice), blocks on `tool_result` events, rebuilt on reload | test_blocks (4), rich browser suite (33) | P1 | M | Built by code from tool data; the model never writes blocks; failed tools give none | ✔ |
| 119 | Safe rich text | verified | `rich/RichText.tsx`: DOMPurify, code colouring and copy, scrolling tables, safe links, citations only to sources read; no images or media | rich browser suite: hostile markup, `javascript:` and `data:` links, images not loaded | P1 | M | No model text becomes script or markup; image exfiltration closed | Behaviour change: images in notes are no longer shown |
| 120 | Charts from verified data only | verified | `rich/Chart.tsx` (lazy, bar, line, donut, table view, hover readout); usage charts from the ledger | test_blocks (usage equals ledger), rich browser suite (values equal recorded totals) | P1 | M | Non-numeric or unknown chart data is not drawn | More chart sources (research, missions) |
| 121 | Actions inside answers | verified | `rich/Blocks.tsx`: controls chosen by record kind and id, through the normal API | rich browser suite (a card action marks a task done) | P1 | S | Data cannot add or change a control; permissions and approvals apply | ✔ |
| 122 | Edit, regenerate, copy | verified | `POST /conversations/{id}/truncate`, composer edit mode, regenerate, copy | test_blocks (truncate, refused during an approval), rich browser suite | P1 | S | Truncate refuses while an approval runs | ✔ |
| 123 | Starters that launch real capabilities | verified | 11 starter cards; new tools `get_usage`, `list_models`, `project_status`, `list_missions`, `start_mission`, `start_research` | rich browser suite, test_blocks (gating) | P2 | S | `start_mission` and `start_research` always ask, and are not offered to missions, schedules or agents | ✔ |
| 124 | Capability discovery | verified | `capabilities.py`, `GET /api/capabilities`, `pages/Capabilities.tsx`: ready, needs setup, off, optional, not available yet; last success and error from the tool log | test_capabilities, navigation browser suite | P1 | M | Unavailable features offer no action; no paid model is run to check | No plugin marketplace (by design until code review exists) |
| 125 | Command palette | verified | `components/CommandPalette.tsx`: Ctrl or Cmd K, pages, settings, content search, ask, research, add task, plan mission | navigation browser suite | P2 | S | Navigates or creates in plain view only; never runs tools or skips approvals | ✔ |
| 126 | Settings by category | partial | `pages/Settings.tsx`: ten sections, navigator that follows scrolling, jump menu on phones | navigation browser suite, main UI suite | P2 | M | A key shows as connected only after a real check | Missing: a separate Notifications section (delivery is set per automation) |
| 127 | Accessibility (WCAG 2.2 AA) | partial | Contrast tokens, labels, names, focusable regions, keyboard paths | axe-core sweep of 44 page views, no serious or critical violations; keyboard checks in both suites | P1 | M | — | Not done: a manual audit and a real screen reader test |
| 128 | Phones | verified | Chat list toggle, settings jump menu, projects pane, measured charts | Overflow sweep at 390 px (no page scrolls sideways), phone checks in both suites | P1 | S | — | ✔ |
| 129 | Math notation and footnotes in answers | not started | — | — | P3 | S | Must not reintroduce raw HTML | Add with a sanitised renderer |

## v11.1: AI modes and the Free Cloud AI first run (130 to 141)

Browser evidence used a stand-in OpenRouter and Ollama; no real model answered and nothing was paid for.

| ID | Requirement | Status | What exists (modules) | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 130 | Free Cloud AI as the first-run default | verified | `ai_mode` set to free at setup; onboarding preselects it; Skip keeps it | test_ai_mode `test_a_new_workspace...`, onboarding browser suite | P1 | S | Nothing that costs money runs by default | ✔ |
| 131 | Zero cost verified before a model is used (OpenRouter) | verified | `llm.free_basis`: `:free` id and every listed price zero, from OpenRouter's model list, refreshed after 6 hours | test_free_providers, test_models, test_ai_mode (price change), browser (`:free` with a price is refused) | P1 | M | A name is never enough | ✔ |
| 132 | Zero cost for Gemini and local servers | partial | Your confirmation that the key's project or the server cannot bill, logged | test_free_providers, test_ai_mode | P1 | S | Not verifiable by ROOKS | Missing: an API check; Google offers none |
| 133 | Free-only enforcement everywhere | verified | `mode_guard` in `_chat_once` and `stream`; embeddings and voice checked too | test_ai_mode (chat, default, stream, research, workers, missions, vision path, embeddings, chat API), browser | P1 | M | Refused before any request | ✔ |
| 134 | No paid fallback in Free mode | verified | Defaults and backups filtered; a model that reports a charge is blocked | test_ai_mode (backups, charge) | P1 | S | — | ✔ |
| 135 | Premium with a spending limit first | verified | Premium cannot be chosen without a monthly limit; paid checks opt in only | test_ai_mode, browser | P1 | S | Non-OpenRouter spend counts only with prices entered | ✔ |
| 136 | Local AI, downloads with consent | implemented | Consent required by the server; size, memory, licence shown; llama.cpp preset | test_ai_mode (consent), browser (stand-in Ollama) | P2 | M | Downloads logged | Not run: a real Ollama download or a real llama.cpp server |
| 137 | AirLLM adapter | not started | Shown as not supported, with the reason | browser | P3 | L | — | AirLLM has no server and no tool calling |
| 138 | Custom AI server mode | verified | Existing custom endpoints; every older suite runs in this mode | all browser suites | P2 | S | — | ✔ |
| 139 | Existing installs unchanged | verified | No mode means no restriction; Settings says choosing is optional | test_ai_mode, browser migration checks | P1 | S | No setting rewritten | ✔ |
| 140 | Privacy, connection tests and troubleshooting in setup | implemented | `PrivacyNote`, `Troubleshoot`, test reports with tool support counts | browser (content checks) | P1 | S | Provider data policies summarised from their own pages | Not verified against real Gemini or OpenRouter answers beyond one rejected key |
| 141 | Onboarding on phones and with assistive tech | verified | Radio group with arrow keys, labels, no sideways scroll | browser: axe at desktop and phone, keyboard, overflow | P1 | S | — | Not done: a real screen reader test |

## v12: usability, reliability and finish (142 to 150)

Browser evidence uses the stand-in AI in `workbench/tests/browser/fake_ai.py`; no real provider was involved.

| ID | Requirement | Status | What exists (modules) | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 142 | Cohesive, documented design system | implemented | `styles.css` v12 layer, `docs/DESIGN_SYSTEM.md` | screens.py screenshots (judged by eye, not testable) | P1 | M | — | A designer's review |
| 143 | Every screen audited and fixed | verified | Fixes listed in `docs/V12.md` | test_regressions (22) | P1 | L | — | ✔ |
| 144 | Assistant reliability | verified | Drafts per conversation, replies stay in their conversation, no navigation hijack | test_regressions | P1 | M | — | ✔ |
| 145 | Settings that confirm and explain | verified | Save states, timezone setting, compact AI mode, Free mode pickers | test_journey, test_regressions | P1 | M | Free mode pickers cannot select paid models | ✔ |
| 146 | Keyboard access everywhere | verified | List rows as links and buttons, palette focus return, one overlay at a time | test_keyboard (20), test_regressions | P1 | M | — | A real screen reader pass |
| 147 | Performance measured and improved | verified | Pages on demand, adaptive polling | perf.py: first screen script 684 to 392 kB, idle requests 33 to 8 a minute | P2 | M | Stop stays on a 2 s check while Computer Use is on | Deep link loads 20 to 50 ms slower |
| 148 | Desktop, tablet and phone | verified | Shell fix, phone layouts | screens.py 485 checks | P1 | M | — | Real devices, Safari, Firefox |
| 149 | Committed, reproducible browser tests | verified | `workbench/tests/browser/` with `run.sh` | all pass | P1 | M | No real keys used | ✔ |
| 150 | Manual screen reader test | not started | — | — | P2 | S | — | Test with VoiceOver and NVDA |

## v13: one conversation, honest model integrations (151 to 166)

Evidence types: **stand-in AI** (`workbench/tests/browser/fake_ai.py`, not a model), **official CLI** (the real Codex CLI 0.165.0
or Claude Code 2.1.312 against a local stand-in API with a fake key), **AirLLM** (the real package and bridge with a random-weight
test model). No live AI provider, no ChatGPT or Claude account, no desktop hardware. Details in `docs/V13.md` and `docs/INTEGRATIONS.md`.

| ID | Requirement | Status | What exists (modules) | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 151 | Results of started work come back to the conversation | verified | `coordinator.py` jobs, delivery loop, never during a turn | test_v13_workflow; scenarios B, E, F, G | P1 | M | Posted results count as outside content | ✔ |
| 152 | Approve a mission plan and Stop work from the chat | verified | `job` block, JobCard; same endpoints as the pages | scenarios B, E | P1 | M | No new approval path | ✔ |
| 153 | Spreadsheet analysis with computed figures | verified | `tables.py`, `analyze_table` (CSV, TSV, xlsx) | test_v13_workflow; scenario C | P1 | M | Regex XML, size limits, no formulas run | Older .xls not read |
| 154 | Document preview and download in chat | verified | doc card Preview (Markdown), Download | scenario D | P2 | S | — | ✔ |
| 155 | Coding agents through the companion | verified | `agents.py` (companion), `coding.py`, `run_coding_agent` | test_agents (10), test_v13_workflow, scenarios F and G (official CLI) | P1 | L | Always approved; local opt-in per agent; allowed folder; Codex sandbox self-test; Claude only `--bare`, no shell; no token files read | ChatGPT sign-in, real API keys, macOS and Windows not tested |
| 156 | ROOKS chat never uses a Claude subscription; says so | verified | directory and approval copy; `--bare` only | scenario G | P1 | S | — | ✔ |
| 157 | One connection directory, categories A to E | verified | `connections.py`, ConnectionDirectory | test_v13_connections; scenarios F to J | P1 | M | No keys shown | ✔ |
| 158 | Capabilities tested and recorded separately | verified | `verify.py` (chat, tools, round trip, streaming, JSON, vision, embeddings) | test_v13_connections; scenario H | P1 | M | Same mode and spending guards; asks before a test that may be charged | Vision and embeddings not run in the browser suite |
| 159 | One model selection: Automatic, ready, untested, unavailable | verified | `ModelSelect`, `/api/models` status | scenarios A and J | P1 | S | Free mode never picks paid | ✔ |
| 160 | any-llm evaluated; adopted narrowly | implemented | `anyllm_engine.py`, opt-in per Anthropic connection | test_v13_connections (stand-in Messages API) | P2 | M | Same guards, output cap reaches native requests | Not tested against the real Anthropic API |
| 161 | AirLLM evaluated for real; experimental, off | verified | `addons/airllm/` bridge and README | scenario H (capability tests fail, as they should) | P3 | M | Bridge binds 127.0.0.1 | Real model, GPU not tested |
| 162 | Ollama and llama.cpp optional, nothing automatic | verified | directory entries with setup steps | scenario H | P2 | S | — | Not installed on the test machine |
| 163 | Minimal navigation, area tabs, old routes kept | verified | `App.tsx` NAV, AREAS, AreaTabs | test_journey (updated), scenarios | P1 | M | — | ✔ |
| 164 | Check-ins asked for in conversation, with approval and quiet hours | verified | `create_checkin` (always approved) | test_v13_workflow; scenario I | P2 | S | — | ✔ |
| 165 | Research Stop takes effect at once | verified | `research.stop_now`, `_end` | test_v13_workflow | P2 | S | A late failure cannot overwrite | ✔ |
| 166 | Committed scenario tests A to J | verified | `workbench/tests/browser/test_v13_scenarios.py` | 38 checks | P1 | M | Fake keys only | ✔ |

## v14: one place to work, honest states (167 to 178)

Evidence types as in v13, plus a **stand-in mailbox** (mail functions replaced in the test; no IMAP server). Details in
`docs/V14.md`.

| ID | Requirement | Status | What exists (modules) | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 167 | An approval runs only what was reviewed, in its context | verified | `request_approval` records scope; `decide_approval` rebuilds it; `_run_tool` never skips the tool check | test_v14_boundaries | P1 | M | Closes a widening path for workers and missions | ✔ |
| 168 | Budgets checked when an approval runs | verified | `_budget_refusal` (project, monthly, mission) | test_v14_boundaries | P1 | S | — | ✔ |
| 169 | The assistant respects project boundaries | verified | `scope.py` on notes, files, tasks, missions, research, history, project status | test_v14_boundaries (both ways) | P1 | M | Isolated projects closed to other conversations | Pages still show everything to you, by design |
| 170 | The assistant is the first screen; one sidebar with conversations | verified | `Sidebar.tsx`, `App.tsx`, `Assistant.tsx` | test_v14_shell, test_journey, test_keyboard | P1 | L | — | ✔ |
| 171 | Every page still reachable; old addresses kept | verified | Workspace fold, page tabs, routes | test_v14_shell (11 pages), screens.py | P1 | S | — | ✔ |
| 172 | Results open beside the conversation | verified | `Preview.tsx`, cards | scenarios B and D | P2 | M | Same endpoints as the pages | Files: text only, no images |
| 173 | Progress in plain sentences, details on request | implemented | `stepLabel`, `pastLabel` | screenshots | P2 | S | — | Not every tool has a label; unknown ones read generically |
| 174 | One honest state per connection | verified | `connections.py` STATES | test_v14_connections, test_v14_shell | P1 | S | — | ✔ |
| 175 | AirLLM never picked automatically | verified | `llm.experimental`, `recommended_ref` | test_v14_connections (stand-in bridge), scenario H (real bridge) | P2 | S | — | A renamed bridge behind a rewriting proxy is not recognised |
| 176 | Email triage: read and reply as drafts only | verified | `read_email`, `save_email_draft` with `in_reply_to` | test_v14_email (stand-in mailbox) | P1 | S | Email text is outside content; no send tool | Not tested against a real mail server |
| 177 | Motion system, reduced motion, dark mode contrast | verified | `styles.css` v14 layer | test_v14_shell, screens.py (axe) | P2 | S | — | ✔ |
| 178 | Performance holds | partial | Assistant in the main bundle | perf.py | P2 | M | — | Long conversation and Settings slower than v13; +73 kB first load |

## v15: one engine, three interfaces (179 to 194)

Evidence types as before: **stand-in AI** (`fake_ai.py`, not a model), **real CLI install** (a new virtual environment),
**real Electron app** (Linux x64 under Xvfb), **installed package** (the .deb and the AppImage contents). Details in
`docs/V15.md`, `docs/DESKTOP.md`, `cli/README.md`.

| ID | Requirement | Status | What exists (modules) | Evidence | Pri | Cx | Security | Acceptance / next |
|---|---|---|---|---|---|---|---|---|
| 179 | One typed lifecycle for all work | verified | `work.py` (Phase, TRANSITIONS, one mapping per engine) | test_v15_core; test_zz_work_transitions checks every status change of the whole suite | P1 | M | — | Engines keep their own state machines; the lifecycle is the shared view |
| 180 | Every status change recorded | verified | `work_events` written by triggers in the same transaction | test_v15_core, upgrade check | P1 | S | Audit trail cannot be skipped by an engine | ✔ |
| 181 | One versioned API for every client | verified | `v1.py`: meta, status, conversations, chat, work, approvals, projects, capabilities, files, events | test_v15_core, test_cli, test_v15_web | P1 | M | Terminal token reaches v1, not Settings | ✔ |
| 182 | Incompatible client and server say what to update | verified | `X-Rooks-API`, 426 | test_v15_core; test_cli (exit 7) | P2 | S | — | ✔ |
| 183 | A retried request never runs twice | verified | `Idempotency-Key` on chat; deciding an approval twice returns the decision | test_v15_core | P1 | S | — | Only chat and approvals take keys |
| 184 | Live state for all clients | verified | `/api/v1/events`: baseline, then changes | test_v15_web (terminal chat shows in the open browser), desktop e2e | P1 | M | Same session or token auth | Server polls its database every 2 s per client |
| 185 | Capability discovery | verified | `/api/v1/capabilities` (group, risk, timeout, asks first, outside content, set up) | test_v15_core | P2 | S | — | Not a plugin system: no code is installed at run time (`docs/DSH_EVALUATION.md`) |
| 186 | Approval requests audited as a pair | verified | `approval asked` with the request number | test_v15_core | P2 | S | — | ✔ |
| 187 | Images, PDFs and spreadsheets beside the chat | verified | `Preview.tsx`, `/v1/files/{id}/view`, `/table` | test_v15_web | P2 | M | Inline only for PDFs and images, nosniff, sandbox for images | Word files show as text |
| 188 | A saved file is checked on disk | verified | `/v1/files/{id}/verify` (SHA-256 against the record) | test_v15_core (tampered and missing files), test_v15_web | P1 | S | — | Files edited on your computer are checked by the companion, not here |
| 189 | Terminal CLI on the same engine | verified | `cli/` (rooks-cli 1.0.0) | tests/cli/run.sh: clean install, 32 checks incl. interactive chat in a pseudo-terminal | P1 | L | Token never on the command line; 0600 file or keychain | Keychain path not tested (no keychain in the container) |
| 190 | Desktop app | verified (Linux) | `desktop/` (Electron) | unit 10, e2e 21, installed .deb 8, AppImage 8 | P1 | L | Page isolation, navigation lock, permission allowlist | Windows and macOS not built; unsigned; no auto-update |
| 191 | Desktop restarts a crashed server | verified | `supervisor.js` | unit tests, e2e (server killed) | P1 | M | — | Docker way tested only for its error messages |
| 192 | Desktop notifications lead to the work | implemented | `live.js` | e2e (notification logic) | P2 | S | — | A real notification daemon was not tested |
| 193 | Companion paired only from the app, by choice | verified (Linux) | setup page, `rooks_companion pair` | e2e: paired, online, stopped on quit | P1 | S | Folders only through the system folder dialog | macOS and Windows companion not run |
| 194 | Upgrade from older data | verified | `db.init` adds tables and triggers | tests/upgrade/check_upgrade_from.sh from v14 and v13 | P1 | S | — | ✔ |

## Next highest-value work

1. Build and smoke-test the desktop app on Windows and macOS, then sign it (190; `docs/DESKTOP.md` lists the steps).
2. Run one real provider and one real mail account through `rooks run` and the web app, local first (189, 176).
3. Bring the 120 message conversation and Settings back to v13 speed (178): render long conversations in windows,
   and load Settings sections when opened.
4. Try voice with real services, local first: a Whisper or Kokoro compatible server (105), then browser speech, and barge-in on real speakers.
5. Run `companion/tests/macos_check.py` on a real Mac (items 21, 22, 27, 46, 69). macOS behaviour is still only tested against a mocked osascript.
6. If you want phone calls, connect a Twilio account and place the one verification call yourself (107); ROOKS has never placed a real call.
7. Let company agents use workers (110), and write worker findings into the evidence ledger (113).
8. Test visual targeting with a real vision model, local first (22, 23).
