"""feat/v02-core: the few places Projects, Action history and Checked answers plug into the assistant.

Shared files call only these functions (one commented line each), so other v0.2 branches merge cleanly:
- main.py: `v02_hooks.install(app)` adds the routers, the tools and settings tracking;
- assistant.system_prompt: `parts.extend(v02_hooks.context(ctx, user_text))`;
- assistant.run_tool: `snap = v02_hooks.before_tool(...)` before a tool runs and `v02_hooks.after_tool(...)` after it worked.
Every hook swallows its own errors: a chat turn never fails because of them.
"""

from __future__ import annotations

import logging

log = logging.getLogger("v02")


def context(ctx, user_text: str) -> list[str]:
    out: list[str] = []
    try:
        from . import projects

        cid = getattr(ctx, "conversation_id", None)
        if getattr(ctx, "channel", "") in ("web", "mobile", "cli", "telegram", "schedule", "agent") and cid:
            p, why = projects.link_conversation(cid, user_text or "")
            if p:
                ctx.project_id = p["id"]
                out.append(projects.context_block(p, why))
    except Exception as e:  # noqa: BLE001
        log.warning("project context skipped: %s", e)
    try:
        from . import checked

        out.extend(checked.context(ctx))
    except Exception as e:  # noqa: BLE001
        log.debug("check mode skipped: %s", e)
    return out


def before_tool(name: str, args: dict) -> dict:
    try:
        from . import history

        return history.before_tool(name, args)
    except Exception:  # noqa: BLE001
        return {}


def after_tool(name: str, args: dict, result, ctx, snap: dict) -> None:
    try:
        from . import history

        aid = ctx.receipts[-1].get("action_id") if getattr(ctx, "receipts", None) else None
        history.after_tool(name, args, result, ctx, snap, aid)
    except Exception as e:  # noqa: BLE001
        log.warning("history hook failed: %s", e)


def register_tools() -> None:
    from . import assistant, projects

    have = {t.name for t in assistant.TOOLS}
    for t in projects.build_tools(assistant.Tool, assistant.S, assistant.I):
        if t.name not in have:
            assistant.TOOLS.append(t)
            assistant.TOOL_MAP[t.name] = t


def install(app) -> None:
    from . import checked, history, projects

    app.include_router(projects.router)
    app.include_router(history.router)
    app.include_router(checked.router)
    history.install()
    register_tools()
