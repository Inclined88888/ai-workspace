# Projects, memory scopes, missions and model roles

## Projects

A project holds one piece of ongoing work: name, description, goals, instructions for ROOKS, decisions, open
questions, and links to its conversations, missions, tasks, notes and documents. Start a chat from the project page
(**New chat in this project**) or a mission (**New mission**). Notes and tasks ROOKS creates while working in a
project are linked to it.

At the start of every turn in a project, ROOKS gets its brief:

- goals and your instructions
- the last 10 decisions and open questions
- open tasks and recent missions
- short episodes left by finished missions

ROOKS records decisions and questions with the `project_note` tool, which only exists inside a project.

### Settings per project

| Setting | Effect |
|---|---|
| Model | Used when the conversation or mission has no model of its own |
| Monthly budget | Work in the project stops once this month's spend on its conversations reaches it |
| Local models only | Chat, missions, learning, summaries and research in the project refuse any model outside this computer or your network, including as a fallback. Moving a conversation out of the project needs an explicit confirmation |
| Keep separate | Only the project's own memories, plus who you are and how you like things done, are recalled in it |
| Permissions | Each group can be narrowed (Ask first, Off) but never widened beyond Settings |

### Archive, bin, delete

- **Archive** hides the project. Nothing new can start in it until you restore it.
- **Move to bin** keeps everything as it is, and **Restore** brings it back. A project with a running mission
  cannot be binned.
- **Delete for good** only works from the bin, with the project's exact name typed. It deletes the project's
  memories, decisions and questions. Its conversations, missions, tasks, notes and files are kept, no longer in a
  project. Nothing in the bin is removed automatically.

## Memory scopes

| Where | What is recalled |
|---|---|
| Outside any project | User wide memories only |
| In a project | The project's memories plus user wide ones |
| In a project set to keep separate | The project's memories plus user wide profile and preferences only |

Where a new memory goes: facts learned or saved in a project stay in the project; profile and preferences are user
wide. The assistant can save a fact as user wide by asking for scope `user`. Search, forget and the learning job only
see memories visible in the current scope, so a project conversation cannot read, change or delete another
project's memories. The Memory page shows everything, with the project each memory belongs to.

Recalled memories are presented to the model as remembered data, not instructions. Secrets and card numbers are
refused on every write, as before.

## Context compression

The model sees the last 40 messages of a conversation. When older messages fall out of that window (at least 6 of
them), a background job folds them into a running summary that is added to the prompt. The summary goes through the
secret redactor, and the prompt asking for it says quoted web, file and email text is data. It uses the
conversation's model and follows the project's local-only rule. Turn it off with the `context_summary` setting.
Summary quality depends on the model and has only been tested with a stand-in.

## Missions

New in this release:

| Feature | How it works |
|---|---|
| Dependencies | A step can name earlier steps it needs. If one of them failed or was skipped, the step is skipped with the reason |
| Tries per step | 1 to 5 (default 2) |
| Review unverified steps | A step that finished without evidence pauses the mission. **It is done, continue** or **Redo this step**. Unverified steps in a finished mission can also be accepted or redone |
| Budget reservation | Before a mission with a spend cap starts, its unspent cap is reserved against the monthly limit, together with other active missions' caps. If it does not fit, it does not start, and the reason gives the numbers |
| Estimate | Average spend per step of your past finished missions, times the plan's steps. Shown when there is history; an estimate, not a limit |
| Recovery | If ROOKS restarted while a computer action of the mission was in flight, the resumed step is told to observe the screen before doing anything again |
| Projects | A mission in a project plans with the project brief, follows its model rules and budget, and leaves an episode in the project's memory when it ends |

### Workflow templates

**Missions, Start from a workflow** fills a plan from a template. Templates turn on review of unverified steps.

| Template | Needs | Changes anything? |
|---|---|---|
| Clean up a spreadsheet | Companion with the folder allowed | Writes a new file |
| Check invoices against a spreadsheet | Companion | Writes one note |
| Process a document | Companion | Writes a new file |
| Draft email replies | Email connected | Saves drafts only, never sends |
| Follow up on open items | Nothing extra | Writes a note and tasks |
| Weekly report | Nothing extra | Writes a note |
| Inventory check | Companion | Writes a new CSV file |

Every permission and approval rule applies while they run. Only **Clean up a spreadsheet** has been run end to end
against a real file (companion e2e, scenario T).

## Model roles

Settings, **Model roles** chooses a model per kind of work. Each model's capabilities are listed with their evidence:

- **tested**: ROOKS sent a real request that needed it, and the model answered correctly. This covers the
  connection check with a tool attached, the image test, and the JSON test.
- **reported**: the provider's model list says so, for example context length.
- **unknown**: neither of the above.

| Role | Needs | Used automatically by |
|---|---|---|
| Chat | Tool use tested | Chat, when the conversation, agent and project have no model of their own |
| Planning | JSON test passed | Mission plans, step checks and reports, unless the mission has its own model |
| Research | JSON test, and a reported context of 32k or more | Deep Research, unless you pick a model for the run |
| Vision | Image test | Visual targeting (same setting as the Computer page) |
| Local | On this computer or your network | Local-only projects with no model of their own |
| Coding, Documents, Long context | Tools; 32k; 100k | Not used automatically yet |

A role is refused for a model without that evidence, with the reason. A role's model is used exactly: if it fails,
the work fails visibly and ROOKS does not switch to another model. The JSON test is one tiny capped request and only
runs when you press it; nothing is tested in the background.

## Tests

`workbench/backend/tests/test_projects.py` (14 tests) covers:

- isolation in both directions, keep separate, and the brief, notes and linked work
- narrowed permissions that cannot widen, local-only refusal before any request is sent, and the project budget
- archive, bin, restore and delete for good
- compression, dependencies, review and episodes, the failed prerequisite skip, budget reservations and
  restart recovery
- model roles and templates

The v9 browser suite covers the Projects page, chat in a project, templates, model roles, the vision card and phone
width. All models in these tests are stand-ins.
