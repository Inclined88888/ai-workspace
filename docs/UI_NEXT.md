# UI next steps for fun + best

Current Assistant.tsx already shows tool steps with verified/check-failed text and approval cards. Good base.

## Immediate additions

1. **Status chips in toolbar** (next to ModelSelect):
   - Verified (green if last tool receipts passed)
   - Local preferred
   - No babysitting

2. **Right strip or expandable panel**:
   - Session cost meter (pull from usage if available, or placeholder)
   - Hard stop button (abort the stream)
   - Quick skills list
   - Quick memory list with forget

3. **Empty state fun line**:
   Change the hint to: "Sharp. Remembers what matters. Never claims done without a receipt. What do you need?"

4. **Avatar/status line** under the toolbar: "Ready" / "Waiting on approval" / "Checking receipts" / "Done, verified".

These make the reliability visible and the personality felt without adding fluff.

Apply the system prompt patch first (see PROMPT_PATCH.md), then these UI pieces.
