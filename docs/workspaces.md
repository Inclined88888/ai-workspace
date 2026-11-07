# Professional workspaces

Say once what kind of work you do. The assistant then works that way everywhere: in Chat, in the `rooks` terminal and
in your chat apps (Telegram and the others).

| Workspace | Tier | What it adds |
|---|---|---|
| General | Free | Nothing extra. The default. |
| Trade | Pro | The Trade pack (quotes, invoices, payment reminder drafts). New projects are jobs. |
| Legal | Pro | The Legal pack (document checks, clause library, timelines). New projects are matters. Answers say where they came from and that they are not legal advice. |
| Study and research | Free | The Study & research pack (flashcards, worked maths, references, lab notebook). New projects are courses. |

Tiers are the packs' own tiers (`FEATURE_TIERS` in `workbench/backend/app/features_v02.py`). No new paid feature was
added.

## Where to choose

* **App:** sidebar, **More → Your work**, or search for "Your work".
* **The workspace's own screen:** on the card in use, **Open … tools** opens the Trade quotes and invoices, the Legal
  checks and clauses, or the Study flashcards, maths, references and lab notebook. The old Packs gallery address now
  opens Your work.
* **Terminal:** `rooks work` lists them, `rooks work show` shows the one in use, `rooks work use ID` chooses one.
  Inside a session, `/work` does the same.

The app and the terminal read and write the same setting, so a choice made in one shows up in the other.

## What it does and does not do

* **One brain.** A workspace does not split anything. You keep one memory, one list of mailboxes, one chat history and
  one set of skills. Projects are still where you keep each client, job, matter or course apart.
* **Choosing a workspace adds its pack.** Every skill in the pack goes through the same security check as skills from
  GitHub. If the plan does not include it (Pro), or a skill fails the check, nothing changes: the workspace you had
  stays in use.
* **Going back to General keeps the pack and your work in it.** Your quotes, clauses, flashcards and notes stay.
  Removing a pack is a separate step (`rooks pack remove ID`).
* **If a pack is removed later,** the assistant is told its tools are not there, so it says so instead of promising
  them. Choosing the workspace again adds them back.

## How it works

* `workbench/backend/app/workspaces.py` holds the four workspaces and the `professional_workspace` setting. An unknown
  stored value reads as General.
* `assistant.system_prompt` adds one line for the workspace in use (nothing for General). Chat, chat apps, the
  company of agents and the terminal's shared loop all use that prompt.
* The terminal's own loop (`ROOKS_SHARED_LOOP=0`) gets the same line from `GET /v1/rooks/context`
  (`workspace_context`).

### API

| Route | What it does |
|---|---|
| `GET /api/workspaces` | All four, with `active`, `tier`, `allowed` (plan check) and `pack_added` |
| `GET /api/workspaces/active` | The one in use |
| `POST /api/workspaces/active` `{"id": "trade"}` | Choose one. `402` with the upgrade card when the plan does not include it, `409` when a skill fails the security check, `404` for an unknown id |
| `GET /v1/rooks/workspaces` | Same list for the terminal (terminal access token) |
| `POST /v1/rooks/workspaces/ID` | Choose one from the terminal, with the same rules |

Tests: `workbench/backend/tests/test_workspaces.py`, `rooks-code/tests/test_work_cli_e2e.py`.
