"""What ROOKS can do, in plain words, with each capability's real state right now.

Every status here is computed, never assumed: a capability is "ready" only when what it needs is configured (a
provider, a paired computer, a key) and its permission is not off. "Last worked" and "last problem" come from the
tool log. Things ROOKS does not do are listed as "not available yet", so nothing unavailable looks like it works.

Statuses: ready | needs_setup | off (you turned it off in Permissions) | optional (an add-on that is not installed)
| not_built.
"""

from . import config, db, permissions


def _last(tools: list[str]) -> dict:
    if not tools:
        return {}
    marks = ",".join("?" * len(tools))
    ok = db.one(f"SELECT MAX(at) AS at FROM tool_runs WHERE ok = 1 AND tool IN ({marks})", *tools)["at"]
    bad = db.one(f"SELECT at, tool, error FROM tool_runs WHERE ok = 0 AND tool IN ({marks}) ORDER BY id DESC LIMIT 1", *tools)
    return {"last_ok": ok, "last_error": ({"at": bad["at"], "tool": bad["tool"], "error": bad["error"][:200]} if bad and (not ok or bad["at"] > ok) else None)}


def _perm(tools: list[str]) -> dict:
    groups = sorted({permissions.TOOL_GROUP[t] for t in tools if t in permissions.TOOL_GROUP})
    pol = permissions.policy()
    modes = {g: pol.get(g, "on") for g in groups}
    return {"groups": [{"id": g, "label": permissions.GROUPS[g][0], "mode": m} for g, m in modes.items()],
            "off": bool(modes) and all(m == "off" for m in modes.values())}


def catalog() -> dict:
    from . import calls, computer, github, search, telegram, voice  # noqa: F401 - telegram imported for parity
    from .llm import list_models

    providers = db.one("SELECT COUNT(*) AS n FROM providers WHERE enabled = 1")["n"]
    # Ready means a model the AI mode allows is working, not merely that a provider was saved
    from fastapi import HTTPException

    from .llm import effective_default
    try:
        effective_default()
        usable, why = providers > 0, ""
    except HTTPException as e:
        usable, why = False, str(e.detail)
    search_on = bool(search.provider())
    mail = db.one("SELECT COUNT(*) AS n FROM mail_accounts")["n"]
    cals = db.one("SELECT COUNT(*) AS n FROM calendars WHERE enabled = 1")["n"]
    tg = bool(db.get_setting("secret:TELEGRAM_BOT_TOKEN", ""))
    gh = bool(github.token())
    runner = bool(config.RUNNER_URL and len(config.RUNNER_TOKEN) >= 24)
    browser = bool(config.BROWSER_URL and config.BROWSER_API_KEY)
    cu = computer.settings()
    device = computer.online_device()
    v = voice.settings()
    ph = calls.phone()
    skills = db.one("SELECT COUNT(*) AS n FROM skills WHERE enabled = 1")["n"]
    files = db.one("SELECT COUNT(*) AS n FROM files")["n"]
    try:
        models = len(list_models())
    except Exception:  # noqa: BLE001 - an unreachable provider must not break the catalog
        models = 0

    def cap(cid, category, name, about, examples, tools=None, ready=True, need="", setup="", kind="built_in", note=""):
        tools = tools or []
        p = _perm(tools)
        status = "not_built" if kind == "not_built" else "optional" if kind == "optional" and not ready else \
            "off" if p["off"] else "ready" if ready else "needs_setup"
        return {"id": cid, "category": category, "name": name, "about": about, "examples": examples, "status": status,
                "kind": kind, "needs": need if status in ("needs_setup", "optional") else "", "setup": setup, "note": note,
                "permissions": p["groups"], "tools": tools, **_last(tools)}

    no_model = why if providers and why else "Connect an AI model first (a free OpenRouter or Gemini key works)."
    caps = [
        cap("chat", "Models", "Conversation with your AI models", "Ask anything in plain English; switch models per chat.",
            ["Explain this contract clause simply", "Compare the AI models I have connected"], ["list_models"], usable, no_model, "/settings#models",
            note=f"{models} model(s) available from {providers} provider(s)." if providers else ""),
        cap("web", "Research", "Web search and reading pages", "Searches the web and reads pages to answer with sources.",
            ["What changed in Australian super rules this year?", "Read this page and summarise it: https://…"], ["web_search", "read_page"],
            search_on, "Add a free Tavily or Brave key, or the search add-on.", "/settings#search"),
        cap("deep_research", "Research", "Deep Research", "Several rounds of searching and reading, an evidence table and a cited report.",
            ["Research the best accounting software for a five-person firm"], ["start_research"], search_on and usable,
            "Needs web search and an AI model.", "/research"),
        cap("notes", "Files and Documents", "Notes and documents", "Write, find and update notes and documents; download them as Markdown.",
            ["Write a one-page summary of our meeting and save it", "Find my notes about the benchtop"], ["search_notes", "read_note", "create_note", "append_to_note"]),
        cap("uploads", "Files and Documents", "Your uploaded files", "Read PDFs, Word files and text you upload, and answer questions about them.",
            ["What are the payment terms in the attached contract?"], ["search_files", "read_file"], True, note=f"{files} file(s) uploaded."),
        cap("tasks", "Productivity", "Tasks and reminders", "Create, list and complete tasks with due dates.",
            ["Remind me to send the invoice on Friday", "What tasks do I have?"], ["list_tasks", "create_task", "update_task"]),
        cap("calendar", "Productivity", "Calendar", "Read your agenda and add events.", ["Plan my week from my calendar and tasks"],
            ["get_agenda", "create_event"], cals > 0, "Subscribe to a calendar link or connect CalDAV on the Calendar page.", "/calendar"),
        cap("email", "Communication", "Email (drafts only)", "Search your mail and prepare drafts. ROOKS never sends mail.",
            ["Draft a polite follow-up to Sam about the quote"], ["search_email", "read_email", "save_email_draft"], mail > 0, "Add a mail account under Settings, Connections.", "/settings#mail"),
        cap("telegram", "Communication", "Telegram", "Chat with ROOKS from Telegram and get briefings there.", ["Send my morning briefing to Telegram"],
            [], tg, "Paste a bot token under Assistant settings.", "/assistant-settings"),
        cap("voice", "Communication", "Voice conversations", "Talk with ROOKS and hear answers; interrupt by speaking.",
            ["Press Voice in the Assistant and ask about your day"], [], True, "", "/settings#voice",
            note="Speech by your browser" if v["stt"] == "browser" else "Speech by your provider"),
        cap("phone", "Communication", "Phone calls from ROOKS", "One-way spoken briefings to your own verified number (optional, may cost money).",
            ["Set a check-in to phone me when a client replies"], [], bool(ph["provider"] and ph["verified"] and ph["enabled"]),
            "Set up and verify a phone line under Settings, Calls (needs a Twilio account).", "/settings#calls", kind="optional"),
        cap("missions", "Business Operations", "Missions and workers", "Bigger jobs planned step by step; workers take parts with their own tools.",
            ["Start a mission: compare three CRM tools and save a summary"], ["start_mission", "list_missions"], usable, no_model, "/missions"),
        cap("overview", "Business Operations", "Spending and project status", "Real totals and charts of your AI spending; where a project stands.",
            ["Show my AI spending for the last 30 days", "How is the kitchen project going?"], ["get_usage", "project_status"]),
        cap("checkins", "Automations", "Check-ins and scheduled jobs", "ROOKS checks what you choose on a schedule and tells you only what is new.",
            ["Every weekday at 8am, give me a short briefing"], ["schedule_task", "list_schedules"], True, "", "/automations"),
        cap("memory", "Productivity", "Memory", "Remembers lasting facts about you and your projects; you can review and correct them.",
            ["Remember that I prefer morning meetings"], ["remember", "search_memory", "forget"], True, "", "/assistant-memory"),
        cap("computer", "Computer Use", "Use apps on your computer", "Look at and use desktop apps, its own browser and folders you allow; always visible and stoppable.",
            ["Open my invoices spreadsheet and sort it by date"], ["computer_observe", "computer_click", "computer_type"], cu["enabled"] and device is not None,
            "Install and pair the ROOKS Companion on the Computer page, then turn Computer Use on.", "/computer"),
        cap("folders", "Computer Use", "Files and spreadsheets on your computer", "List, move and copy files; read and edit spreadsheets and documents in folders you allow.",
            ["Organise my Downloads folder by type", "Remove duplicate rows from customers.csv"], ["files_list", "files_move", "sheet_read", "sheet_update"],
            cu["enabled"] and device is not None, "Needs the paired ROOKS Companion with a folder allowed.", "/computer"),
        cap("browser_agent", "Browser", "Browser agent", "A real browser that completes multi-step goals on websites (slow).",
            ["Find the opening hours on the council website"], ["browse_web"], browser, "Install the browser add-on.", "", kind="optional"),
        cap("terminal", "Coding", "Terminal and workspace files", "Run commands and edit files in an isolated workspace container.",
            ["Write a Python script that totals invoices.csv and run it"], ["run_command", "workspace_read", "workspace_write"], runner,
            "Start the runner container (it ships with ROOKS; see the Terminal guide).", "/settings#workspace"),
        cap("github", "Coding", "GitHub", "Search and read issues and pull requests; open issues and comment (always asks first).",
            ["What are the open bugs in my repo?"], ["github_search", "github_read_issue", "github_create_issue"], gh, "Add a GitHub token under Settings, Connections.", "/settings#github"),
        cap("skills", "Custom Skills", "Saved skills", "Reusable procedures ROOKS saves and improves; listed to the model and loaded when needed.",
            ["Save how you prepared this report as a skill"], ["load_skill", "save_skill"], True, "", "/assistant-skills", note=f"{skills} skill(s) saved."),
        cap("two_way_phone", "Communication", "Two-way phone conversations", "Talking with ROOKS over a phone call.", [], kind="not_built",
            note="Not available: it would need ROOKS reachable from the internet. Calls are one-way briefings; reply in the app."),
        cap("send_email", "Communication", "Sending email", "Sending mail on your behalf.", [], kind="not_built",
            note="Not available by design: ROOKS saves drafts for you to review and send."),
        cap("plugins", "Custom Skills", "Installable plugins", "Installing third-party code as new tools.", [], kind="not_built",
            note="Not available: skills are text procedures only; no third-party code is installed."),
    ]
    return {"capabilities": caps, "counts": {s: sum(1 for c in caps if c["status"] == s) for s in ("ready", "needs_setup", "off", "optional", "not_built")}}
