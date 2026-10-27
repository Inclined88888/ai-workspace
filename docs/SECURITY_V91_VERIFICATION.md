# ROOKS v9.1 verification — 27 October 2026

Base security commit: b8aa8069d950e59b4d0c77e9ae1a355753154efd.
Scope: security/rooks-v9-1 only. Main and development/rooks-v9 must remain unchanged.

## Changes in this continuation

- Recheck vision mode, model, consent/session validity, project membership and local-only policy after the capture await, before transmission.
- Cancel undispatched actions when the caller is cancelled; dispatched actions become uncertain. Clear pending arguments, waiter and transient result.
- Reject mission visual targets used outside their originating mission, and reject non-finite or out-of-range confidence.
- Classify locality by the endpoint, not the Ollama protocol label. Public Ollama endpoints no longer qualify as local/free for automatic verification.
- Update DOMPurify to 3.4.17, React Router DOM to 6.30.6 and Vite to 6.4.5 with matching lockfile.
- Add 14 lifecycle/locality regression cases; update the free-provider fixture to use an actual localhost URL.

## Concrete verification

Executed locally on macOS with Python 3.12:
- Original backend suite: 166 passed.
- Final backend suite: 180 passed, three dependency deprecation warnings.
- Companion suite with optional openpyxl/python-docx installed: 28 passed.
- Frontend typecheck and production build: passed. Build reports its existing large-chunk warning.
- npm audit after updates: zero critical/high, two moderate entries (React Router and React Router DOM).
- git diff --check: passed.

The backend tests use a local scripted model server and fake companion; companion macOS adapter tests mock the scripting layer. These results do not validate a real vision model, real desktop automation or a deployment.

## Original security-review disposition

1–2: Existing branch keeps action results transient, preserving a 400 KB capture without SQLite persistence or truncation. Regression passed.
3–4: Existing project gates cover vision and embedding indexing/querying. This continuation strengthens capture-time rechecks and endpoint classification. Regressions passed.
5: Existing branch gates project_note after outside content and excludes model-generated mission report text from durable episode memory. Inspected; broader injection resistance is not proven.
6: Existing branch removes raw action payloads from action rows, encrypts desktop approvals, redacts tool-call arguments in conversation history and offers opt-in legacy cleanup. This is not a promise that ordinary chat/tool results contain no sensitive data.
7: Existing branch makes paid provider checks opt-in, bounded, budget-guarded and accounted. This continuation removes the public-Ollama automatic-check exception.
8: Network gate remains defense in depth, with documented proxy/tunnel limitations. Pairing and device tokens remain required. Deployment isolation is UNVERIFIED.
9: Companion checksums do not authenticate releases. Signed provenance remains OPEN; no signing keys or release trust policy were invented.
10: Start/reservation transitions are transactional on the existing branch. Uncapped missions and unknown prices still weaken monetary limits; this remains OPEN.
11: Existing targets are random, expiring, conversation/mission/device scoped. This continuation closes the missing-mission scope case.

## Remaining dependency findings and limits

Two moderate React Router audit entries remain; npm proposes a major upgrade to 7.18.4. A router migration and browser regression suite remain OPEN. The audit records affected-package advisories, not proof of exploitability in this application.

Original advisory/release sources checked:
- https://github.com/advisories/GHSA-2w69-qvjg-hvjx
- https://github.com/cure53/DOMPurify/releases
- https://github.com/vitejs/vite/security/advisories/GHSA-p9ff-h696-f583

No real providers were called, no production database was cleaned, and no deployment or update package was applied.
Security decisions, implementation and verification were performed by Codex. Workers/Hermes were unavailable; no private data was delegated.
