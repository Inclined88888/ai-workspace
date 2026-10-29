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
* **R1 split (Phase 0 item 3).** Until then, `llm.describe()` derives `auth_kind` and `protocol` from the old `kind` field for the run log.
* **Idempotency semantics (item 28).** `actions.request_id` is unique per row today; callers cannot yet supply a key to deduplicate retries.

## Found during work, not yet scheduled

* **Release numbers.** Workbench has no version scheme; CHANGELOG entries use the schema version and merge commit until releases exist (item 80).
* **Unseparated phone numbers.** R7 redaction does not match a phone written as one run of digits; it is indistinguishable from epoch timestamps and ids in logs. Revisit if a context-aware detector (item 78) lands.

* **Pin Playwright.** `browser-agent/pyproject.toml` allows `playwright>=1.50`, so a fresh install can pull a version that expects a browser build that is not installed (6 browser-agent tests failed this way until `CHROME_PATH` was set).
* **Learning competes with turns on single-slot local models.** The benchmark measured learning calls racing the next turn on Ollama's one slot. Learning should yield to a waiting turn.
* **Benchmark coverage.** `bench/local_tasks.py` completed 25 of 50 tasks on CPU before the environment's time limit. Memory, calendar, drafts and "change nothing" tasks are unmeasured.
* **Image size figures before CI were inflated.** Resolved by item 111. Every "368 MB" reported before CI came from Docker's containerd image store, whose `inspect .Size` adds compressed blobs to unpacked layers (282 MB + 85 MB). CI builds from Docker Hub on the classic engine and measures unpacked layers: **248 MB** on main. A residual 34 MB gap between local layer sums (282 MB, mirror base images) and CI (248 MB) is not explained yet; CI's figure is the canonical one.
* **Browser-agent tests are not in CI.** They need Chromium and a pinned Playwright (see the Playwright entry). Add a job once Playwright is pinned.
* **Remote branch `ci-demo/reintroduce-pr1-bug` still exists.** It belongs to closed PR #6 (the CI regression demonstration). This session cannot delete remote branches (HTTP 403); delete it in the GitHub UI.
* **Migrated legacy cost rows** keep the bare model name in `model`; new rows store the model ref. Reports group the two separately for the month of the upgrade.
* **`browse_web` results cannot be verified** by receipts, and say so.
