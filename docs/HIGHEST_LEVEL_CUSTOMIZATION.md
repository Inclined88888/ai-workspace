# Highest-level customization (2026-10-30)

Goal: make the existing reliability features (receipts, approvals, run log, budgets, skill/memory) the visible product. Stop claiming done without verification. Keep the core modular monolith. Make it fun (sharp, dry, no fluff) and the best.

## System prompt (ready to apply)

See `docs/PROMPT_PATCH.md` for the exact replacement block. It adds:
- Sharp, direct personality with zero babysitting.
- Never claim success without receipt.
- Explicit goal confirmation at the end.
- Call out loops and false claims.

Restore `workbench/backend/app/assistant.py` from main, then apply the patch.

## UI changes (frontend) — next to build

1. In Assistant.tsx, surface tool receipts inline under each tool call (verified / held / not verified).
2. Add a right-side panel (or expandable) with:
   - Live session cost (sum from usage on this conversation).
   - Hard stop button that aborts the current stream.
   - Skills list (from existing skills endpoint).
   - Memory facts with forget.
3. Collapse sidebar navigation toward: Chat, Library, Activity (runs), Connections, Settings.
4. Status chips: Verified, Local preferred, No babysitting (last turns had zero interventions and all receipts passed).
5. Avatar or status line that reflects state (focused, waiting approval, stuck, done).

## Next code steps (priority order)

1. Apply the system prompt patch (docs/PROMPT_PATCH.md).
2. Surface receipts and control strip in the Assistant UI.
3. One tool registry: generate MCP and OpenAPI from TOOLS so policy applies everywhere.
4. Keyless search default (SearXNG or bundled).
5. Model capability probe on connection add; refuse tools on failing models.
6. Summarizing compaction that pins goal + open items + pending approvals.
7. Session cost limit + hard abort.
8. Goal verification step after the model claims completion.

## What not to do

Do not add more add-ons by default. Do not expand Company or Compare in the main nav. Do not weaken the approval boundary after untrusted content.

This file is the source of truth for the next focused changes.
