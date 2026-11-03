"""Assistant tools for the team lead (feat/team-lead). Assigning is local; the message to the member goes through the
outbox, which waits for the lead's OK unless they turned on auto-notify."""

from __future__ import annotations

from . import engine, outbox, parse, store


def register_tools() -> None:
    from .. import assistant

    if "team_assign" in assistant.TOOL_MAP:
        return
    S, I = assistant.S, assistant.I
    T = assistant.Tool

    def assign(request: str) -> dict:
        p = parse.assignment(request)
        m = p.pop("member")
        t = store.add_task({"title": p["title"], "due": p["due"], "priority": p["priority"], "project_id": p["project_id"],
                            "project": p["project"], "assignee_id": m["id"] if m else None, "source": "chat", "mine": False})
        if m:
            msg = outbox.assignment(t["id"])
            return {"task": t, "assigned_to": m["name"], "message_status": msg["status"], "note": msg["why"], "page": "/team"}
        s = engine.suggest_assignee(t)
        sug = engine.add_suggestion("assign", t["id"], s["member"]["id"], s["reason"]) if s else None
        return {"task": t, "assigned_to": None, "suggestion": sug and sug["headline"], "reason": s and s["reason"], "page": "/team"}

    def status(person: str = "") -> dict:
        b = engine.board()
        m = store.find_member(person) if person else None
        ts = [t for t in b["tasks"] if not m or t["assignee_id"] == m["id"]]
        return {"counts": b["counts"], "at_risk": [{"id": t["id"], "title": t["title"], "risk": t["risk"]["label"]} for t in ts if t["risk"]],
                "people": [{"name": x["name"], "load": x["load"]["label"], "open": x["load"]["open"]} for x in b["members"]
                           if not m or x["id"] == m["id"]]}

    def update(task_id: int, update: str) -> dict:
        p = parse.reply(update)
        t = store.task(int(task_id))
        store.add_update(t["id"], "leader", status=p["status"], percent=p["percent"], note=p["note"])
        return store.task(t["id"])

    tools = [
        T("team_assign", "Team lead: create a task for a team member from one plain-English sentence, e.g. 'give Sam the Smith St "
          "quote by Friday'. If nobody is named it suggests who should do it. The message to the member waits for the user's OK.",
          {"request": S("the sentence")}, assign, ("request",), effect="write"),
        T("team_status", "Team lead: how the team is going - task counts, late/stuck/at-risk tasks and each person's workload.",
          {"person": S("optional name")}, status),
        T("team_today_plan", "Team lead: what everyone should do today (draft; the user approves sending it).", {},
          lambda: engine.today_plan()),
        T("team_update_task", "Team lead: record progress on a team task, e.g. 'done', '50%', 'blocked: waiting on parts'.",
          {"task_id": I(), "update": S()}, update, ("task_id", "update"), effect="write"),
    ]
    for t in tools:
        assistant.TOOLS.append(t)
        assistant.TOOL_MAP[t.name] = t
