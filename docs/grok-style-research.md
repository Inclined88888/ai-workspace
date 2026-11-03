# Grok Bot: look and features, mapped to AI Workspace (Rooks)

Research date: 30 Oct 2026 (Brisbane). Public sources only:
- docs.x.ai/grok-bot/overview, /computer-and-apps, /skills-routines-and-automations, /approvals-security-and-privacy,
  /security-faq, /chat-and-collaboration, /team-bots, /shared-bots, /settings-and-notifications
- x.ai/news/introducing-grok-bot (launch post, 11-12 Aug 2026)
- The Verge (12 Aug 2026), marcabraham.com product review (16 Aug 2026), The Aigentic hands-on
Inspiration only: no Grok Bot name, logo, wordmark or artwork goes into the product. Our product is AI Workspace / Rooks.

## 1. The look

| Element | What Grok Bot does (public docs, reviews, screenshots) | Notes for us |
|---|---|---|
| Layout | 3 panes: left sidebar of Bots and chats, centre conversation, right info panel for the open Bot/chat. | We had a single long nav list + one page. New "Teammates" view gives the 3-pane layout; the old nav stays for everything else. |
| Sidebar | "+" button at top (new Bot, group chat, browse shared Bots, templates). Bots listed by name with avatar; group chats; sections the user can make and reorder. | Sections ("Personal", "Work", user-made), drag-free reorder via menu. |
| Bot header | Click the Bot's name at the top of the chat for its details (role, sharing, Slack). | Name + avatar + role line in chat header; click opens info pane. |
| Right info pane | Tabs for the conversation/Bot: Overview (role, memory, connectors), Routines (each with "When to run" in words, last 20 runs), Media/files, Computer (live preview; click to watch clicks/typing, take over), Members (group chats). | Overview / Routines / Files / Computer / Members tabs, collapsible. |
| Messages | Plain assistant messages (no heavy bubbles), user bubbles right; reply-to a message; react to a message; mention with @ (Bot, group, routine, app). | Light editorial bubbles; tapback reactions; @mention autocomplete. |
| Cards | Editable draft card for email/Slack (recipients + body, Send / Discard); approval card ("Allow once / Always allow / Never / Deny once (Esc)"); question/choice widgets with options; secure forms for secrets/logins (value never shown in chat); product, shipment and booking cards; voice memo player. | Draft-before-send, approval, choice widget, secure form, reaction bar. |
| Working state | Status line while it works, steps it is taking, "send another instruction while work is in progress", a moving/glowing border on the computer view while the Bot drives it. | We already have thinking shimmer, step timeline, edge glow (#33); reuse them. |
| Type & spacing | Neutral sans, generous whitespace, quiet monochrome chrome with one accent; soft rounded cards. | Keep Sunshine editorial (Bodoni Moda display + Hanken Grotesk) and theme variables; no purple gradients. |
| Mobile | Same Bots on iPhone/Android; Bot profile with "Add routine" that pre-fills "Set up a routine to"; approvals from phone. | Mirror in Expo app. |

## 2. Features, mapped

Legend: HAVE = already in ai-workspace integration/v0.2; PART = partly; NEW = built on feat/grok-style; DOC = documented follow-up.

| Grok Bot feature | ai-workspace before | feat/grok-style |
|---|---|---|
| Multiple named Bots (teammates) with role, persona, avatar, own memory | PART: one assistant, global memory, Projects with memory (#36) | NEW: Teammates with name, emoji/colour avatar, role, persona, memory scope (own / shared "about you" facts), allowed tools, model; created from templates |
| Sidebar sections | NO | NEW: sections, move Bot to section |
| Group chats with several Bots, @mention | NO | NEW: group chats; @Name routes the message to that teammate; no mention = the lead (first member) answers; approvals/drafts/secret forms never appear in a group (same rule as Grok Bot) |
| Bots message each other / hand off | NO | NEW: "hand to @Name" in group; DOC for autonomous bot-to-bot messaging |
| Routines (schedule or event) per Bot | HAVE: automations + schedules (#34), not per assistant | NEW: routines tab per teammate (automation with owner teammate id), "Add routine" pre-fills "Set up a routine to" |
| Learn from demonstration → skill | HAVE: Show me how (#29) | Reused |
| Persistent computer per Bot (own screen, browser, files) | PART: Rooks Desk + Rooks Browser (one shared session) | NEW: per-teammate browser profile dir and working folder, Computer tab with live view + take over; DOC: isolated VM/container per teammate |
| Local-computer policy (Ask every time / Always / Never) | HAVE: approvals per risky action | Reused; approval card offers Allow once / Deny |
| Auto-review safety check | PART: approvals, skill scan, egress log | NEW: risk rules on outgoing actions (send/post/pay/delete need approval every time; inbound channel messages can never trigger a send) |
| Connectors / plugins catalog | PART: Connect mail & calendar, skills hub, MCP server | Teammate picks which connectors it may use |
| Vault (logins, cards, addresses) + secure in-chat forms | PART: keychain for provider keys only | NEW: Vault (OS keychain when available, else encrypted DB), items listed masked, in-chat secure form card; secret values never enter chat, logs or the model |
| Draft-before-send for email/Slack/messages | PART: mail drafts, follow-up drafts | NEW: Outbox of drafts with editable card, Send / Discard; every outgoing message (mail, Slack, Telegram) goes through it |
| Chat channels (Slack, X Chat; others in market: Telegram, iMessage, WhatsApp) | PART: Telegram gateway (one assistant) | NEW: Slack (Socket Mode app token + bot token, wizard with manifest), Telegram wizard per teammate; WhatsApp: DOC (Cloud API needs Meta business verification) ; iMessage: DOC |
| Pages / canvases (shareable reports, dashboards) | PART: live documents (#34) export | NEW: Pages: save a reply or live doc as a page, local self-contained HTML export, share link on LAN; edit title/body |
| Voice chat, voice memos | HAVE: Talk, dictation (#35) | Reused |
| Bot templates you can share | NO | NEW: export/import teammate template JSON (no secrets, no memory unless chosen) + built-ins (Personal, Work, Study, Trade office, Researcher, Legal desk) |
| Team Bots / shared Bots | NO (Team plan in #32 is billing only) | DOC |
| Mobile parity | PART: phone app chats, approvals, runs | NEW: Teammates list + chat on phone, multiple computers, offline queue, file browser, handoff |
| Tag @bot on X | NO | Out of scope |

## 3. Phone-to-desktop link (beyond Telegram)

| Idea | Before | feat/grok-style |
|---|---|---|
| Direct encrypted link | LAN + optional relay URL, device token over HTTP | NEW: per-device X25519 keypair at pairing; messages sealed end to end (NaCl box) so the relay sees only ciphertext; LAN first, relay fallback |
| Encrypted relay | none | NEW in ai-workspace-cloud branch: store-and-forward mailbox `/relay/v1/{mailbox}` holding opaque ciphertext only |
| Lock-screen approvals, Face ID for risky | push + in-app approvals | NEW: notification categories with Approve/Deny actions; risky ones require local auth (expo-local-authentication) before Approve |
| Live job progress (Live Activities / Dynamic Island) | none | Scaffold + docs (needs native build / EAS) |
| Handoff both ways | ask-bar handoff on desktop | NEW: "Continue on computer" / "Continue on phone" (handoff record with conversation + draft) |
| Share sheet | /api/mobile share endpoint (#34) | Scaffold + docs for native extension |
| Browse computer's files from phone | none | NEW: read-only browse of allowed folders (Files roots) with download/summarise |
| Multiple computers | one Workbench per pairing | NEW: phone keeps a list of paired computers and switches |
| Offline queue | none | NEW: phone queues messages/tasks while offline and flushes them in order |
| Wake-on-LAN, Apple Watch | none | DOC |
