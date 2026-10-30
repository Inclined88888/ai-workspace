# Team lead (feat/team-lead)

Assign work to your staff, track it, and let the assistant chase it. Works for any team: a tradie crew, an office, a
law firm, a lab, a studio.

## What works now (backend, tested offline)
- **Team**: add people with role, skills, weekly hours, work days, leave/away dates and how to reach them (email, text,
  Telegram, Slack, their private link, or the app with a Team seat). Contact details are encrypted on this computer.
- **Assign in plain English**: "give Sam the Smith St quote by Friday, urgent" -> task for Sam due Friday, urgent, linked
  to the matching Project. Assistant tools: `team_assign`, `team_status`, `team_today_plan`, `team_update_task`.
- **Suggestions with reasons**: who should do it (skills, how booked they are, whether they work that day), rebalancing
  when someone is over 100% booked, and "next up" when someone finishes. You approve each one, or switch on auto-assign.
- **Messages to members (outbox)**: the first message to a person on each channel always waits for your OK. After that,
  assignment messages go by themselves only if you switch on "Auto-notify my team for assignments", and nudges only with
  "Auto-nudge". The daily plan always waits for you. Email goes from your connected mailbox, Telegram through your bot,
  Slack through a webhook; text messages and member links become "copy and send it yourself" until a provider is set up.
- **Replies update tasks**: "done", "50%", "half done", "on it", "blocked: waiting on parts", with `[T12]` to pick the
  task (or their only open task). Unclear replies are shown to you, never guessed. `POST /api/team/inbound`.
- **Tracking**: late, at-risk (due soon, not far along), stuck (blocked or no update for N days) and idle people. You get
  a notice on the desktop list and a phone push (when phone notifications are on), once a day per item, plus a daily or
  weekly digest. A nudge draft is queued for the member.
- **Views (API)**: board by person and status with workload, person page, today's plan for everyone, digest.
- **Phone (API)**: `/api/mobile/team` board, approve suggestions and messages, update tasks, notices.
- **Cloud (ai-workspace-cloud PR #1)**: members with a seat see and update only their own tasks; members with no app get
  a private, revocable member link page. `POST /api/team/sync` pushes tasks and pulls their updates (also every 10 min).
- **Plans**: `team_tasks` is Team/Business; your own "My tasks" list is free. Nothing is locked until billing is live.
- **Privacy**: members only see their own tasks. No tracking of anyone's devices, screens, apps or location.
- **Data model**: `app/team/store.py` is the only file that touches storage; `person_entity` / `task_entity` map members
  and tasks onto the Person/Task entities of feat/memory-core so storage can move there.

## Not done yet
- Desktop pages (Team board, workload chart, timeline/Gantt, person page, plan screen) and the phone screens: API only.
- Polling your inbox for email replies (the parser and `/api/team/inbound` are there; the mailbox watcher isn't wired),
  Telegram inbound from members, an SMS provider (Twilio) for sending/receiving texts.
- Jira / Asana / Trello adapters and linking tasks to Sites records; calendar-based leave detection.
- Inviting members from the app (the existing cloud team invites in Your plan work), screenshots.
