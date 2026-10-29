# Backlog

Out of scope for the current branch, dropped, or blocked. Nothing is dropped silently: every entry gives its reason.

## Dropped

### 106 and 107: session adapter (QR login, memory only) and its warning and kill switch. Dropped by decision D3.
* Anthropic's terms (https://code.claude.com/docs/en/legal-and-compliance, read 2026-10-28): "Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users. Moreover, developers may not collect, store, or intermediate Claude.ai credentials or session tokens — sign-in to a Claude account must complete through Anthropic's own flow." Enforcement is "without prior notice".
* For ChatGPT, OpenAI's terms page refused automated reads (403), so its exact wording was not verified. The legitimate route is Sign in with ChatGPT (item 101).
* R5 now reads "legitimate only". `experimental_session_slot` survives only as a reserved value that refuses (docs/design/0001).

## Blocked on someone else

### 101: Sign in with ChatGPT client ID (decision D4)
* Plan usage needs an OpenAI-approved client ID, which the repo owner registers.
* Until then, the flow ships behind a feature flag in a "request access" state, with a localhost callback and a manual code paste.
* Whether OpenAI accepts a localhost callback for self-hosted apps is not stated in the docs (checked 2026-10-28).

## Scheduled later

* **Claude Code launcher add-on (Phase 3, decision D2).** It runs the user's own unmodified Claude Code binary, and the user signs in through Anthropic's flow. Needs Node, so it is an add-on (R6).
* **Idempotency across interleaved writes (item 28 follow up).** A model loop of the form A, B, A runs A twice, because a repeat is only recognised when no other change ran in between (so toggles keep working). Closing it needs per tool semantics (which writes are naturally idempotent). Design record 0001, "Idempotency keys".
* **The web chat does not send an Idempotency-Key yet.** It never retries a request, so there is nothing to deduplicate; the CLI (item 84) should send one.

* **Add-on egress is not in the outbound audit log (item 75 follow-up, R8).** The browser agent's own page loads, model downloads by the ollama container, Paperclip and Open WebUI connect from their own containers. The browser agent already lists visited URLs in its job steps; ingesting them as `egress` rows (and asking Paperclip/Open WebUI for an equivalent or documenting their egress) would make local-only mode verifiable for the whole stack, not only Workbench.
* **Audit log retention.** `egress` rows are kept forever. With Telegram on, long polling adds about 2,880 rows a day (estimate, not measured). Needs a retention setting with a documented default, and an export before pruning.
* **Link egress rows to the run that caused them.** Egress rows have no `parent_action_id`; a context variable carrying the current turn would put a turn's outbound calls in its trace.

## Found during work, not yet scheduled

* **Bridge tokens for other local tools (item 103, Phase 1).** `/v1` accepts only `BROWSER_API_KEY` today. Other OpenAI-only tools (editors, scripts) need user-created, revocable bridge tokens and streaming (`stream: true` is refused with 400 for now).
* **Browser runs are not linked to the chat turn that started them.** Their `model_call` rows carry channel `browser` but no `parent_action_id`. Passing the turn id through the job would put them in the turn's trace (item 21 UI).
* **Standalone browser-agent CLI keeps its own providers.** Outside the stack (no `BROWSER_REQUIRE_WORKBENCH`), `browser-agent run` can call Anthropic, Gemini, Ollama and the like directly. That is a separate distribution, not core, but it is a second provider layer. Retire it if the CLI gains a `--workbench` default.

* **Live-key check of the new protocols.** The `responses` and `anthropic_messages` adapters pass contract tests against their documented and SDK-typed wire formats, but have not been run against the live APIs (no keys in this environment or CI). Manual check: one tool-using turn and one stream with a real OpenAI key and a real Anthropic key; record the result in docs/design/0002.
* **Native Anthropic for migrated connections.** Connections from before schema 2 keep Chat Completions through Anthropic's compatibility layer, which Anthropic says is for testing. Consider prompting the user to switch once the live-key check above passes.

* **Release numbers.** Workbench has no version scheme; CHANGELOG entries use the schema version and merge commit until releases exist (item 80).
* **Unseparated phone numbers.** R7 redaction does not match a phone written as one run of digits; it is indistinguishable from epoch timestamps and ids in logs. Revisit if a context-aware detector (item 78) lands.

* **Learning competes with turns on single-slot local models.** The benchmark measured learning calls racing the next turn on Ollama's one slot. Learning should yield to a waiting turn.
* **Benchmark coverage.** `bench/local_tasks.py` completed 25 of 50 tasks on CPU before the environment's time limit. Memory, calendar, drafts and "change nothing" tasks are unmeasured.
* **Image size figures before CI were inflated.** Resolved by item 111. Every "368 MB" reported before CI came from Docker's containerd image store, whose `inspect .Size` adds compressed blobs to unpacked layers (282 MB + 85 MB). CI builds from Docker Hub on the classic engine and measures unpacked layers: **248 MB** on main. A residual 34 MB gap between local layer sums (282 MB, mirror base images) and CI (248 MB) is not explained yet; CI's figure is the canonical one.
* **Remote branch `ci-demo/reintroduce-pr1-bug` still exists.** It belongs to closed PR #6 (the CI regression demonstration). This session cannot delete remote branches (HTTP 403); delete it in the GitHub UI.
* **Migrated legacy cost rows** keep the bare model name in `model`; new rows store the model ref. Reports group the two separately for the month of the upgrade.
* **`browse_web` results cannot be verified** by receipts, and say so.
