# Phase 0 acceptance and Exit Gate 0

These are reconstructed engineering checks, not a recovered copy of Claude's plan.
The owner asked to continue without that checklist. Scope follows the numbered
items named in the handoff. A row is not complete merely because code exists.

| Item | Acceptance | Evidence / remaining work |
| --- | --- | --- |
| 73 encryption | Stored provider, calendar, mail and search credentials use authenticated encryption; reads do not expose them; tampering fails closed; terminal tokens are hashed | `tests/test_phase0.py` exercises real API writes and database ciphertext/readback. Existing Fernet implementation retained; no incompatible key migration |
| 79 telemetry / Paperclip | Core has no analytics exporter; Paperclip explicitly disables first-party telemetry even if saved preferences enable it | Compose now fixes `PAPERCLIP_TELEMETRY_DISABLED=1`, `DO_NOT_TRACK=1`, `OTEL_SDK_DISABLED=true`. Upstream precedence verified in [config.ts](https://github.com/paperclipai/paperclip/blob/master/packages/shared/src/telemetry/config.ts); no full Paperclip image/packet-capture test performed |
| 83 OpenAPI | Discoverable main API schema requires session or terminal authentication; schema describes route authorization; no credential values | `/api/openapi.json`, `app/openapi.py`, integration test; [API guide](API.md) |
| 84 CLI | Stdlib client uses the shared runtime, private token storage, idempotency for deliberate retries and nonzero failure exits | `cli/tests/test_ws.py`, existing server idempotency/approval tests; [API guide](API.md) |
| 80 signed releases | Versioned artifacts have verifiable workflow identity, checksums and provenance; untested/off-main commits cannot publish | Keyless release workflow and [verification guide](RELEASES.md). [Candidate signing run 40831989297](https://github.com/Inclined88888/ai-workspace/actions/runs/40831989297) passed: provenance issued and all subjects verified; no versioned release published |
| 1 GB check | Core starts, writes/reads authenticated data and survives restart in a hard 1 GiB/one-CPU container, with image <=400 MB and idle RAM <=150 MiB | `scripts/ci/check-image.sh`, used by CI. [CI run 40831092498](https://github.com/Inclined88888/ai-workspace/actions/runs/40831092498) passed: 248 MB layers, task persistence, median idle RAM 70.68 MiB after 60 s (six samples), 15 access-log lines, no planted secrets or formatter errors |

## Encryption boundary and recovery

This is **credential-field encryption**, not encrypted SQLite or whole-disk
encryption. Notes, mail drafts, conversations, metadata and deployment `.env` are
not encrypted by this change. The local CLI token/pending prompt file is mode 0600,
not encrypted. The server derives independent encryption/signing keys from
`APP_SECRET`; losing/changing it invalidates existing sessions and prevents
credential decryption. Back up the secret separately from database exports, protect
backups and use host disk encryption for all content. Do not rotate by replacing
`.env` casually. Re-enter credentials if the old key is irretrievable. An attacker
with both the running process environment and database can decrypt credentials.

## Resource test boundary

The 1 GiB test measures the core alone, without optional CLIs, local inference,
Paperclip, Chromium or other add-ons. It is not a claim that those fit on a 1 GB VPS,
and does not measure a provider round trip. Container limits do not include the
host kernel, Docker daemon, OS services or a full VPS installation. A clean real
1 GB VPS test remains a separate acceptance check; this environment is not one.
The local build uses a temporary Dockerfile mounting the managed proxy CA solely
for npm/pip installation; the CA is not committed or baked into the image.

## Exit Gate 0 decision

**Not yet passed: external acceptance remains.** The local backend/CLI suite passed
**161 tests**. PR #15's integrated CI and main CI passed. The signed candidate run
linked above succeeded for main commit `1cdc570559e5f94bb091cfed9c3f167cade85c3f`:
GitHub issued provenance and verified source, CLI and checksum subjects. Its
`signed-release` artifact contains the candidate archive and verification bundle;
retain a copy before GitHub's artifact retention expires. No production release
tag was created. A clean real 1 GB VPS deployment and live owner subscription/
add-on telemetry acceptance remain unverified: this task has no such VPS or
owner-authenticated subscription volume. Documentation and container limits do
not substitute for those checks.

The missing original checklist is no longer a blocker. Remaining external evidence
is explicit above; no item should be labelled complete based on an unrun check.
