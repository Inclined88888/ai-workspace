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

## Replies read for you (slice 2)
- **Your inbox**: switch on "Read my team's replies" (`read_inbox`). Every 10 minutes (or "Check now",
  `POST /api/team/replies/check`) new mail *from people on your team* is read; everything else is left alone and never
  opened. The first check only remembers what's already there. Pick the mailbox with `mail_account` (else the first one).
- **Telegram**: members who message your Telegram bot (their chat id, user id or @username saved as their Telegram
  contact) have their replies read. Everyone else still gets "This assistant is private".
- **Check before it changes a task** (`replies_review`, **on by default**): replies read automatically wait in
  "Replies to check" (`GET /api/team/replies`, `POST /api/team/replies/{id}/apply|dismiss`, also on the phone) because
  an email From line can be faked. Switch it off to apply them straight away. A reply never sends anything; a finished
  task only leads to a "next up" suggestion that still needs your OK (or your auto-assign rule).
- Replies you paste or forward through `POST /api/team/inbound` are applied straight away (you're the one doing it).

## Text messages (Twilio)
1. Make a Twilio account, buy a number that can send and receive SMS in your country.
2. In Team settings, or `PUT /api/team/sms` with `{"sid": "AC…", "token": "…", "from_number": "+61…"}`. The Auth Token is
   kept encrypted on this computer and only sent to api.twilio.com.
3. Set a member's channel to Text and add their mobile. Messages follow the same outbox rules (first one waits for you).
4. Replies are read by checking Twilio's message list every 10 minutes, so no public address or open port is needed.
   Local Australian numbers (04…) are turned into +61 when your Twilio number is Australian.
Cost: Twilio charges per message on your own account.

## Jira, Asana, Trello (your own tokens)
`PUT /api/team/trackers/{jira|asana|trello}` then `POST /api/team/trackers/{kind}/sync`.
- Jira: `site` (https://yours.atlassian.net), `email`, `token` (id.atlassian.com > Security > API tokens), optional `jql`.
- Asana: `token` (personal access token), `project` (project id).
- Trello: `key` and `token` (trello.com/app-key), `board` (board id).
Open items assigned to people on your team (matched by email or name) appear on the board with the tracker's title, due
date and status; later syncs update them, nothing is deleted on either side, and nobody is messaged because of a sync.
Not yet: writing status back to the tracker, automatic sync on a timer, OAuth sign-in (tokens only). Tested against
recorded-style fake responses only, not live accounts.

## Shared memory (feat/memory-core, PR #40)
Follows the adoption notes in docs/data-model.md §8: each member is a `person` entity (`ref=("team_members", id)`), each
task a `task` entity (`ref=("team_tasks", id)`) linked `assigned_to` its person and `part_of` its project; status updates
become `episodic` memories about the person in scope `team:<cloud team id>` (or `team:local`). The team tables stay the
working store and the graph points back to them, as memory-core asks. Builds without memory-core skip this quietly.
Tested with memory-core's knowledge package dropped in (13/13 team tests); in this branch alone that one test is skipped.

## Not done yet
- Desktop pages (Team board, workload chart, timeline/Gantt, person page, plan screen) and the phone screens: API only.
- Writing status back to Jira/Asana/Trello; linking tasks to Sites records; calendar-based leave detection.
- Inviting members from the app (the existing cloud team invites in Your plan work), screenshots.
