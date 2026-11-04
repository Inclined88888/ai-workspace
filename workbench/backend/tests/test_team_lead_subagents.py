"""B1 team lead over AI sub-agents: one chat request splits into sub-tasks, each runs on its own read-only sub-agent on
the Jobs board, and one synthesis comes back to the chat. The same run tree is readable from the web and the terminal."""

import time

import pytest

from conftest import SCRIPT, events, tool_call


@pytest.fixture(autouse=True)
def _clean():
    from app import db

    before = db.get_setting("learning_enabled", None)
    yield
    from app.team import lead

    if before is None:
        db.run("DELETE FROM settings WHERE key = 'learning_enabled'")
    else:
        db.set_setting("learning_enabled", before)

    SCRIPT[:] = []  # unused scripted replies must not leak into later tests
    for t in list(lead._watchers):
        t.cancel()


def _reply(msgs):
    """The scripted model: answers each sub-agent by its sub-task, and the lead's synthesis from the reports."""
    first = msgs[0]["content"] if msgs[0]["role"] == "user" else (msgs[1]["content"] if len(msgs) > 1 else "")
    text = "\n".join(m.get("content") or "" for m in msgs if m["role"] in ("system", "user"))
    if "Your sub-agents reported" in text:
        assert "[S1]" in text and "[S2]" in text and "Postgres is the default" in text and "SQLite fits one machine" in text
        return {"role": "assistant", "content": "SYNTH: S1 says Postgres is the default; S2 says SQLite fits one machine."}
    if "Your sub-task: compare Postgres" in text:
        return {"role": "assistant", "content": "Postgres is the default for multi-user servers."}
    if "Your sub-task: compare SQLite" in text:
        return {"role": "assistant", "content": "SQLite fits one machine and needs no server."}
    if msgs[-1]["role"] == "tool" and "team_run" in (msgs[-1].get("content") or ""):
        return {"role": "assistant", "content": "Two sub-agents are on it."}
    return {"role": "assistant", "content": f"UNEXPECTED {first[:80]}"}


def wait_for(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return fn()


def test_two_subagents_run_in_parallel_and_the_lead_synthesizes_into_the_chat(client):
    from app import db

    db.set_setting("learning_enabled", "0")  # learning calls would take scripted replies meant for the run
    SCRIPT[:] = [tool_call("team_dispatch", {"goal": "pick a database", "subtasks": ["compare Postgres", "compare SQLite"]}),
] + [_reply] * 4  # S1, S2, the lead's synthesis, the chat's closing line
    ev = events(client.post("/api/assistant/chat", json={"message": "research which database to use, split it up"}))
    cid = ev[0]["conversation_id"]
    res = next(e for e in ev if e["type"] == "tool_result" and e["name"] == "team_dispatch")
    assert res["ok"], res
    rid = int(res["preview"].split('"team_run": ')[1].split(",")[0])

    run = wait_for(lambda: (r := client.get(f"/api/team/lead/{rid}").json())["status"] in ("done", "failed") and r)
    assert run, "the run never finished"
    assert run["status"] == "done", run
    assert [s["id"] for s in run["subtasks"]] == ["S1", "S2"]
    assert all(s["status"] == "done" for s in run["subtasks"])
    assert run["subtasks"][0]["result"].startswith("Postgres") and run["subtasks"][1]["result"].startswith("SQLite")
    assert run["synthesis"].startswith("SYNTH:")
    assert len({s["job_id"] for s in run["subtasks"]}) == 2  # two sub-agents, two jobs, two conversations
    assert len({s["conversation_id"] for s in run["subtasks"]}) == 2

    # posted back into the chat that asked
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert any(m["role"] == "assistant" and f"Team run #{rid}" in m["content"] and "SYNTH:" in m["content"] for m in msgs)

    # the same tree from the terminal (bridge key) and in the web list
    token = client.post("/api/cli-token").json()["token"]
    term = client.get(f"/v1/rooks/team/lead/{rid}", headers={"Authorization": f"Bearer {token}"}).json()
    assert term == run
    assert client.get(f"/v1/rooks/team/lead/{rid}").status_code == 401
    assert any(r["id"] == rid for r in client.get("/api/team/lead").json())


def test_subagents_only_get_read_only_tools(client):
    from app import assistant

    tools = assistant.available_tools(assistant.Ctx(channel="subagent", conversation_id=0))
    assert tools and all(t.effect == "read" for t in tools)
    names = {t.name for t in tools}
    assert not names & {"send_email", "create_task", "team_dispatch", "remember", "save_skill", "run_python"}


def test_dispatch_needs_a_goal_and_two_to_six_subtasks(client):
    from fastapi import HTTPException

    from app.team import lead

    with pytest.raises(HTTPException):
        lead.dispatch("goal", ["only one"])
    with pytest.raises(HTTPException):
        lead.dispatch("", ["a", "b"])
    with pytest.raises(HTTPException):
        lead.dispatch("goal", [str(i) for i in range(7)])
