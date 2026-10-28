"""What the assistant may do on its own. Every tool belongs to a group with a risk level; the user sets each group to
on, ask (actions wait for approval) or off (hidden from the model and refused by the backend). Enforced here, for
every channel, so a prompt or a client cannot talk its way past it."""

import json

from fastapi import HTTPException

from . import db

READ, WRITE, CONSEQUENTIAL = "read", "write", "consequential"
MODES = ("on", "ask", "off")

# group: (label, what it covers, {tool: risk})
GROUPS: dict[str, tuple[str, str, dict[str, str]]] = {
    "notes": ("Notes and documents", "Search, read, create and add to notes", {
        "search_notes": READ, "read_note": READ, "create_note": WRITE, "append_to_note": WRITE, "project_note": WRITE}),
    "tasks": ("Tasks", "List, create and update tasks", {"list_tasks": READ, "create_task": WRITE, "update_task": WRITE}),
    "calendar": ("Calendar", "Read your agenda and create events", {
        "get_agenda": READ, "list_events": READ, "list_calendars": READ, "create_event": WRITE}),
    "email": ("Email", "Search and read mail, save drafts (never sends)", {
        "search_email": READ, "read_email": READ, "save_email_draft": WRITE}),
    "files": ("Files", "Search, read and analyse files you uploaded", {"search_files": READ, "read_file": READ, "analyze_table": READ}),
    "web": ("Web", "Search the web and read pages", {"web_search": READ, "read_page": READ}),
    "browser": ("Browser add-on", "Drive a real browser on websites", {"browse_web": CONSEQUENTIAL}),
    "memory": ("Memory", "Remember and forget facts about you", {
        "search_memory": READ, "search_history": READ, "remember": WRITE, "forget": WRITE}),
    "skills": ("Skills", "Load and save reusable procedures", {"load_skill": READ, "save_skill": WRITE}),
    "schedules": ("Schedules", "Create recurring tasks that run without you", {"list_schedules": READ, "schedule_task": CONSEQUENTIAL,
                                                                                        "create_checkin": CONSEQUENTIAL}),
    "team": ("Company team", "Delegate work to agents", {"list_team": READ, "delegate_task": WRITE}),
    "github": ("GitHub", "Search and read issues and pull requests; open issues and comment (always asks first)", {
        "github_search": READ, "github_read_issue": READ, "github_create_issue": CONSEQUENTIAL, "github_comment": CONSEQUENTIAL}),
}
GROUPS["overview"] = ("Workspace overview", "Read your spending, connected models, project status and missions", {
    "get_usage": READ, "list_models": READ, "project_status": READ, "list_missions": READ})
GROUPS["work"] = ("Missions and research", "Start missions (you approve the plan before anything runs) and research runs, which use models",
                  {"start_mission": WRITE, "start_research": WRITE})
GROUPS["terminal"] = ("Terminal", "Run commands in the isolated workspace runner (asks first by default; risky commands always ask)",
                      {"run_command": WRITE})
GROUPS["workspace"] = ("Workspace files", "List, read, create and edit files in the workspace folder (previous versions are kept)",
                       {"workspace_list": READ, "workspace_read": READ, "workspace_write": WRITE, "workspace_edit": WRITE})
GROUPS["computer"] = ("Computer Use", "Look at and use apps, ROOKS's browser and allowed folders on your paired computer", {
    "computer_observe": READ, "computer_wait_for": READ, "browser_snapshot": READ, "files_list": READ, "files_read": READ,
    "computer_open_app": WRITE, "computer_click": WRITE, "computer_type": WRITE, "computer_key": WRITE, "computer_scroll": WRITE,
    "browser_open": WRITE, "browser_click": WRITE, "browser_fill": WRITE, "browser_select": WRITE, "browser_back": WRITE,
    "files_mkdir": WRITE, "files_move": WRITE, "files_copy": WRITE, "files_write": WRITE,
    "computer_set_value": WRITE, "computer_menu": WRITE, "files_history": READ, "files_undo": WRITE, "sheet_read": READ,
    "sheet_update": WRITE, "sheet_dedupe": WRITE, "doc_read": READ, "doc_write": WRITE, "computer_locate": READ, "computer_click_visual": WRITE})
GROUPS["coding"] = ("Coding agents", "Run Codex or Claude Code on your paired computer, in a folder you allowed (always asks first)",
                    {"coding_agents": READ, "run_coding_agent": CONSEQUENTIAL})
TOOL_GROUP = {tool: g for g, (_, _, tools) in GROUPS.items() for tool in tools}
TOOL_RISK = {tool: risk for _, (_, _, tools) in GROUPS.items() for tool, risk in tools.items()}
# Actions that speak for the user to other people, or run a coding agent on their computer: approval every time,
# whatever the group's setting
ALWAYS_ASK = {"github_create_issue", "github_comment", "run_coding_agent", "create_checkin", "save_skill"}
TOOL_GROUP = {tool: g for g, (_, _, tools) in GROUPS.items() for tool in tools}
TOOL_RISK = {tool: risk for _, (_, _, tools) in GROUPS.items() for tool, risk in tools.items()}


DEFAULT_MODES = {"terminal": "ask"}  # until you choose otherwise


def policy() -> dict[str, str]:
    try:
        saved = json.loads(db.get_setting("tool_policy", "") or "{}")
    except ValueError:
        saved = {}
    return {g: saved.get(g) if saved.get(g) in MODES else DEFAULT_MODES.get(g, "on") for g in GROUPS}


def set_policy(changes: dict) -> dict[str, str]:
    cur = policy()
    for g, mode in (changes or {}).items():
        if g not in GROUPS:
            raise HTTPException(400, f"Unknown permission group: {g}")
        if mode not in MODES:
            raise HTTPException(400, f"Mode must be one of {', '.join(MODES)}")
        cur[g] = mode
    db.set_setting("tool_policy", json.dumps(cur))
    return cur


def mode(tool: str, project_id: int | None = None) -> str:
    # A tool missing from the table is treated as an action that needs approval, never as free
    g = TOOL_GROUP.get(tool)
    if not g:
        return "ask"
    if project_id:
        from .projects import stricter  # a project can only narrow what Settings allow

        return stricter(policy()[g], project_id, g)
    return policy()[g]


def allowed(tool: str, project_id: int | None = None) -> bool:
    return mode(tool, project_id) != "off"


def must_ask(tool: str, project_id: int | None = None) -> bool:
    """True when the user wants to approve this action first. Reading stays free in 'ask' mode."""
    return tool in ALWAYS_ASK or (mode(tool, project_id) == "ask" and TOOL_RISK.get(tool, WRITE) != READ)


def describe() -> list[dict]:
    cur = policy()
    return [{"id": g, "label": label, "about": about, "mode": cur[g],
             "tools": [{"name": t, "risk": r} for t, r in tools.items()]} for g, (label, about, tools) in GROUPS.items()]


def check_external(tool: str, client: str) -> None:
    """For callers outside ROOKS (Open WebUI tool server, MCP clients). They have no approval screen, so 'ask' means
    they may read but not change anything; 'off' blocks the group. Writes are audited."""
    from . import audit

    m = mode(tool)
    risk = TOOL_RISK.get(tool, WRITE)
    if m == "off" or (m == "ask" and risk != READ):
        audit.record(client, "tool refused", tool, f"permission is {m}", ok=False)
        why = "turned off" if m == "off" else "set to ask first, and this client cannot ask for approval"
        raise HTTPException(403, f"Not allowed: {tool} is {why} in ROOKS Settings, under Permissions.")
    if risk != READ:
        audit.record(client, "tool", tool)
