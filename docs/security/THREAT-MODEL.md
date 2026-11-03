# Threat model

Scope: everything in this repository. Each claim names the code that enforces it; `tests/test_security_docs.py` checks
those files still exist. Known gaps are listed at the end rather than hidden.

## Who we trust

| Actor | Trust | Why |
| --- | --- | --- |
| The owner (signed in to Workbench, or running `rooks` locally) | Full | It is their machine and their data. Anyone who can sign in can make the assistant do anything its tools can do. |
| Paired phones | Same as the owner, revocable | Device token from a one time, 5 minute pairing code; only hashes are stored (`app/mobile.py`). |
| Model providers | Untrusted output, trusted transport | Model output can be wrong or steered by injected text; it never authorises an action by itself. |
| Web pages, emails, files, tool output, chat channel messages from others | Untrusted | Treated as data. Reading them taints the conversation (below). |
| Skills and MCP servers from GitHub | Untrusted until approved | Eight stage gate, sandboxed at run time (`docs/skills-security.md`). |
| Other local users and processes on the same OS account | Out of scope | They can already read the owner's files. |

## Assets

* Provider API keys and OAuth tokens (mail, calendar, chat channels).
* The database: conversations, memory, mail, files, run log.
* The owner's machine: files, shell, browser sessions, desktop.
* Outgoing actions in the owner's name: email, messages, purchases, posts.

## Boundaries and how they hold

1. **Authentication.** Workbench requires a sign in. Passwords are scrypt hashed, the session cookie is HttpOnly and
   SameSite=Lax (`app/security.py`). The desktop shell signs its own window in with a one time launch token.
2. **Network exposure.** Docker publishes ports on `127.0.0.1` unless `BIND_ADDRESS` is changed
   (`docker-compose.yml`). The desktop sidecar listens on `127.0.0.1` only.
3. **Secrets at rest.** Provider keys are Fernet encrypted under `APP_SECRET` (Docker) or kept in the OS keychain with
   only a reference in the database (desktop, `app/keychain.py`).
4. **Secrets in logs.** Every log record, run log text column and client error message is redacted at creation
   (`app/redact.py`).
5. **Prompt injection.** Not filtered, because it cannot be filtered reliably. Instead, once untrusted web or email
   content is in the conversation, every tool whose effect leaves Workbench or outlives the turn is held for the
   owner's approval; sending mail and inviting people always ask; automations always ask
   (`app/assistant.py` `hold_reason`).
6. **Egress.** Every outbound connection is recorded with host, module and local or external effect, so local only
   mode is provable from the run log (`app/egress.py`, enforced by `tests/test_egress.py`).
7. **Third party code.** Skills and MCP servers are pinned to a commit, scanned, trial run with no grants, risk rated,
   approved per permission, and run in a sandbox (Seatbelt on macOS, bubblewrap on Linux, audit hooks for Python)
   with only the granted permissions (`app/skills_hub.py`, `app/skill_scan.py`, `app/skill_sandbox.py`). The sandbox
   fails closed.
8. **Coding agents.** `cli_agents.py` runs the owner's own `claude` or `codex` with provider keys stripped from the
   environment; edits and commands go through per action approval.
9. **Release integrity.** Release artifacts carry Sigstore build provenance (`.github/workflows/release.yml`). Desktop
   updates are signed with the updater key and verified by the app before install
   (`desktop/src-tauri/tauri.updater.conf.json`).

## Known gaps (not yet defended)

* **No OS sandbox for the assistant's own shell and file tools or for Rooks Code commands.** They run with the owner's
  permissions behind approvals only. Tracked as Tier 1 item 13 (Seatbelt, bubblewrap or Landlock).
* **Single user.** No roles, no per user isolation, no SSO. Do not share one Workbench between people who distrust
  each other.
* **Native installers are unsigned** until Apple and Windows signing certificates are configured
  (`docs/desktop-release.md`). Verify the SHA256SUMS and provenance instead.
* **DNS lookups are not recorded** by the egress log.
* **Approval fatigue.** Approvals protect only if the owner reads them.

## Out of scope

Physical access to an unlocked machine, a compromised OS account, malicious browser extensions, and the security of
the model providers themselves.
