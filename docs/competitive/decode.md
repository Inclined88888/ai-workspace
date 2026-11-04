# Competitive decode — learn, don’t copy

Study notes for making AI Workspace / Rooks best-in-class. **Our code stays ours.** MIT/Apache repos may be cloned under `scratch/competitors/` (gitignored). Proprietary products: docs only. Odysseus: UX only (AGPL — no code).

## Our soul

> The personal AI you **run yourself** — plain enough for someone with no IT background, strong enough to do hard terminal work, with a real browser and computer, work and personal mail, any model — **one brain**, your data, your machine.

Pillars: Yours · Plain · One brain · Life + build · Power with consent · Any model · Gets better for you · Does the hard thing.

If a change would make us feel like a Hermes clone, a Claude wrapper, or a devops console, reject it.

## Method

1. Read open source or public docs.
2. Note **soul** (feeling) and **mechanism** (how).
3. Design the same outcome on our APIs/schemas.
4. **Adjust** existing Rooks/Workbench files — never vendor their modules.
5. Integrate across Chat + `rooks` + browser (+ desk) with the same names.
6. Failing tests first; `./scripts/test-all.sh` before done.

## Terminal & harness souls

| Rival | Licence | Soul | Mechanism to learn | Adjust in ours |
|-------|---------|------|--------------------|----------------|
| Hermes | MIT | Gets better for you | Memory + skill birth/refine; session FTS; cron→channels | `learn.py`, `learning.py`, `rooks_link.py`, memory UI |
| Claude Code | Proprietary docs | Limitless terminal | Long agency loop; subagents; hooks; approvals | `agent.py`, `extras.py`, `undo.py`, `tui.py` |
| Codex CLI | Apache-2.0 | Hard walls + resume | Sandbox; session resume; exec mode | `tools.py`, `cli.py`, receipts |
| Kimi Code | MIT | Fast TUI; role-locked helpers | explore/plan/coder tool fences; web UI | `extras.py` task modes; `tui.py` modes; Browser page |
| DeepSeek harness | MIT | Swappable seams | Plugin composition (idea only) | packs/skills — not Cordis |
| OpenClaw | MIT | One brain on messengers | Gateway + shared sessions | `telegram.py`, channels, scheduler |
| Grokbot | Proprietary | Works when you walk away | Durable away jobs | Automations + browser/desk jobs |

## App / life OS souls

| Rival | Licence | Soul | Adjust in ours |
|-------|---------|------|----------------|
| ChatGPT | Product | Anyone can use it | Short nav, Chat model+mode, approvals above fold |
| Odysseus | AGPL UX only | Self-hosted life OS | Nav IA, mail work/personal, research handoff |

## Browser assistants (first-class)

| # | Rival | Licence | Soul | Adjust in ours |
|---|-------|---------|------|----------------|
| B1 | Manus | Proprietary | Cloud browser+computer, narrated “done” | Browser.tsx + desk handoff UX |
| B2 | OpenManus | MIT | Plan→browse→act; context inject | `assistant._browse` result shape; runner prompts |
| B3 | browser-use | MIT | Real Chrome, DOM+vision, CDP | `rooks-browser` browser/sites/agent |
| B4 | Odysseus research | UX only | Deep research in life OS | Research page + browse handoff |
| B5 | Kimi web / Computer Use | MIT | Web UI + OS bridge | Browser page + rooks-desk |
| B6 | ChatGPT cloud browser | Product | Pause for login; away continue | Approvals + scheduled browse |
| B7 | Grokbot browser | Product | Browser on durable PC | Self-hosted away browse |
| B8 | Computer-use peers | Docs | OS as a tool | `rooks-desk` |

## North-star suite story

Work inbox → browse pricing → `rooks` fixes code → scheduled Telegram summary — on **our** machine, one memory, plain UI.

## Study log

| Date | Studied | Our adjust |
|------|---------|------------|
| 2026-11-04 | Public harness directories + prior audit | Program plan |
| 2026-11-04 | Built Wave 0–5 slice | task modes; skill note; `/history`; mail `life_role`; browse `page_context`; nav; Chat modes; `/api/connections/doctor` |
| 2026-11-04 | Hooks + site sign-out + shared channel memory | `.rooks/hooks/before_tool`; `POST /api/site/forget` and Sites “Forget sign-in”; chat-app turns told they share Chat memory |
| 2026-11-04 | Checked the named eleven against current public sources: Hermes desktop and gateway, OpenClaw gateway, Claude Code, ChatGPT via Codex CLI, DeepSeek Harness web, Kimi Code, Odysseus, OpenManus, browser-use, Codex, Grok Bot | Home asks one sentence first, then Today. Mail, Coding, Research, and While you're away are the plain jobs. |
| 2026-11-04 | Hermes memory you can drop; Kimi chat/plan/build fences; Codex project-write wall | `/memory forget` shares the app store; `/mode` locks tools; project commands cannot write outside the folder |
