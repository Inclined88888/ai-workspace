"""Team lead (feat/team-lead): allocate and track staff tasks, auto-updates, nudges and the daily plan.

Modules:
  store     the data adapter (SQLite tables today; maps onto the Person/Task entities of feat/memory-core)
  parse     plain-English assignment and reply parsing ("give Sam the Smith St quote by Friday", "50%", "blocked: parts")
  engine    suggestions (who should do it, rebalancing, next task), risk detection, today's plan, digest, workload
  outbox    every outgoing message to a team member, with the approval rules
  connect   cloud task sync, member links, email/Telegram/SMS replies, Sites, Jira/Asana/Trello, calendar
  routes    HTTP API (desktop + phone);  tools: assistant tools;  loop: background checks
"""
