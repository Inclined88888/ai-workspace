# ROOKS v11: rich answers, discovery and a calmer interface

Branch `development/rooks-ui-v11`, created from `development/rooks-v10` (`7a5e591`). Requirement statuses are items
118 to 129 of `docs/ROADMAP.md`. The existing frontend was improved in place; nothing was rewritten from scratch and no
working screen was replaced by a mockup.

## Rich answers

When the assistant uses a tool, the answer can show the result as a table, chart or cards instead of only prose.

**Where the structure comes from.** The model never writes it. After a tool runs, `backend/app/blocks.py` builds
typed blocks from that tool's real result, field by field. A chart can only show numbers that exist in ROOKS (for
example the usage ledger); nothing is estimated or filled in to make a picture. A failed tool produces no blocks.
No extra model call is made for layout.

**Schema (version 1).**

| Block | Shows | Built from |
|---|---|---|
| `records` | Cards for tasks, events, notes, missions, projects, files, emails, memories, schedules, research | List and search tools |
| `table` | Columns and rows | Model comparison, project status, usage by model |
| `kpis` | A few headline numbers | Usage totals, project status |
| `chart` | Bar, line or donut | Usage by day and by model (the ledger only) |
| `sources` | Pages read, with links | Web search and page reading |
| `list` | Short labelled items | Smaller tool results |
| `notice` | A plain message | Empty or partial results |

Each block carries at most 25 items, strings are capped, and only `http` and `https` links survive. Blocks travel on
the existing `tool_result` stream event as an extra `blocks` field, so older clients ignore them. Reopening a
conversation rebuilds the blocks from the stored tool results (`GET /api/conversations/{id}` returns them with
`blocks_version`).

**Frontend.** `src/rich/schema.ts` validates every field again and drops any block it does not know.
`src/rich/Blocks.tsx` renders through an explicit allow-list of components. Buttons on a card (open, resume, preview)
are chosen by the card's kind and record id, never by text in the data, and each goes through the normal API with
its permissions and approvals. Charts (`src/rich/Chart.tsx`) load on demand as a separate 5 kB file. Each chart has a
table view; line charts have a crosshair readout and bars show their value on hover.

**Text.** `src/rich/RichText.tsx` renders Markdown through DOMPurify, then improves the cleaned page using text nodes
only:
- code blocks get colouring and a Copy button;
- wide tables scroll inside their own box;
- web links open in a new tab without access to ROOKS;
- other link types (`mailto`, `data`, invented paths) become plain text;
- `[2]` style citations link to a source only if this turn actually read source 2.

**No images or media in rendered Markdown.** An image written by a model would load by itself, and its address
could carry conversation text to anyone. Answers, notes, documents and reports therefore show no images, video or
audio. This changes how existing notes that contained image links look: the image is not shown.

## The new chat

- Starter cards that each launch a real capability (research, a mission, a document, usage, model comparison and more).
- Copy, Regenerate, and Edit and resend on your last message. Editing removes the later part of the conversation
  (`POST /api/conversations/{id}/truncate`), and it is refused while an approval is running.
- Stop while an answer streams. The model picker sits in the composer, with attach and voice; a "Project: name"
  badge shows the project in use.
- A one-line activity summary for each answer. It opens by itself when a step failed, a workspace tool changed
  something or an approval is involved, and otherwise opens on click.
- On phones the conversation list is behind a "Show conversations" button, so the chat keeps the full screen.

New assistant tools: `get_usage`, `list_models`, `project_status` and `list_missions` read only. `start_mission` and
`start_research` always wait for your approval, and they are not offered to missions, schedules or company agents.

## What ROOKS can do

The **What ROOKS can do** page (`/capabilities`, `GET /api/capabilities`) lists every capability with its real state:
- **Ready:** it can be used now;
- **Needs setup:** for example a search key or a mail account;
- **Turned off:** by your permissions;
- **Optional add-on;**
- **Not available yet:** two-way phone calls, sending email and installable plugins.

"Ready" means configured and allowed. It does not mean a provider was just tested: ROOKS never runs a paid model to
check. Each entry shows when it last worked and its last error, taken from the real tool log. Examples on ready
entries open the Assistant with that request; nothing runs until you send it.

## Search and jump

**Ctrl K** (Cmd K on a Mac) or **Search** in the sidebar opens a palette. It covers pages, settings sections,
projects, missions, conversations, notes, documents and files. Typed text can also be asked in chat, opened in
Research, added as a task, or planned as a mission. A planned mission still waits for your approval. The palette only
navigates or creates what you see: it runs no tools and skips no approval. It works by keyboard and is announced as
a combobox to screen readers.

## Settings by category

Settings are grouped as AI models, Voice and calls, Computer Use, Connections, Memory and privacy, Automations,
Security and permissions, Usage and budgets, Appearance, and Advanced. There is no separate Notifications section:
where results are delivered (Inbox, Telegram, quiet hours) is set per automation and check-in. On wide screens a side
list follows your scroll position; on phones a jump menu replaces it. Provider checks run only when you press a
button. A stored key shows as saved until a check succeeds, never as connected; a mail account is tested before it
is saved.

## Other pages

- **Home:** a Glance row of live tiles (missions, next check-ins, AI models, spend this month, computer) above the
  attention card and the setup checklist. Optional extras are linked, not required.
- **Workers:** a flow view groups a mission's workers in columns by dependency.
- **Research:** download the report as Markdown and the evidence as CSV. A question can arrive from the palette.
- **Projects:** the detail pane no longer breaks the phone layout.
- **Computer, Automations, Projects:** only small changes in this round.

## Accessibility, phones and speed

- Light mode text, status colours and links meet WCAG AA contrast. Links inside running text are underlined.
- All form fields, icon buttons and scrolling regions have names.
- An axe-core sweep of 22 routes at desktop (light) and phone (dark) width, 44 page views, found no serious or critical
  violations. It ran with axe-core 4.10.2 from a scratch folder; axe is not a project dependency, and an automated
  sweep does not prove full WCAG 2.2 AA conformance.
- No page scrolls sideways at 390 px. Compare models keeps its sideways column strip by design.
- Reduced motion is respected through the existing appearance setting.
- Main script: 612 kB (177 kB gzipped) before, 670 kB (194 kB gzipped) after. Charts load separately. No new runtime
  dependency was added.

## Tests

| Suite | Result |
|---|---|
| Backend (pytest) | 248 passed, including `test_blocks.py` (4) and `test_capabilities.py` (1) |
| Rich answers in the browser (tables, charts, cards, citations, code, hostile markup) | 33 of 33 |
| Navigation, palette, settings, capabilities, keyboard, phone | 19 of 19 |
| Earlier browser suites (voice, workers, Needs You, research, v9, terminal, main UI) | all passed |
| Companion and Computer Use end to end | 28, and 38 of 38 |

All browser runs used a local fake model and fake providers. They prove the interface and the server contracts, not
how any real model or provider behaves. One run of the settings timing suite failed under load and passed on three
reruns.

## Not done

- Math notation and footnotes in answers.
- Images in answers and notes (removed on purpose, see above).
- Larger redesigns of Computer, Projects and Automations.
- Company agents still do not use workers.
