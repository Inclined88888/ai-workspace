"""Missions: plan review, step execution with verification, approvals, idempotent recovery, stalls, budgets, cancel."""

import asyncio
import json
import time

from conftest import SCRIPT


def plan(*steps):
    return json.dumps({"steps": [{"title": t, "done_when": d, "needs_tool": n} for t, d, n in steps]})


class Model:
    """Answers each kind of call: planning, verification, the final report, and step turns (from a per-step script)."""

    def __init__(self, plan_json, steps, verdicts=None):
        self.plan, self.steps, self.verdicts = plan_json, steps, verdicts or {}
        self.calls = []

    def __call__(self, msgs):
        system = msgs[0]["content"] if msgs[0]["role"] == "system" else ""
        if system.startswith("You plan missions"):
            return {"role": "assistant", "content": self.plan}
        if system.startswith("You check whether"):
            step = msgs[1]["content"].split("\n")[0].removeprefix("Step: ")
            v = self.verdicts.get(step, "done")
            return {"role": "assistant", "content": json.dumps({"verdict": v, "evidence": f"checked {step}", "missing": "" if v == "done" else "nothing created"})}
        if system.startswith("Write the final answer"):
            return {"role": "assistant", "content": "All finished."}
        prompt = msgs[-1]["content"] if msgs[-1]["role"] == "user" else next(m["content"] for m in reversed(msgs) if m["role"] == "user")
        step = prompt.split("Now do step ")[1].split("\n")[0].split(": ", 1)[1] if "Now do step " in prompt else ""
        script = self.steps.get(step, [])
        # how many assistant replies this step turn has produced so far
        last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
        n = sum(1 for m in msgs[last_user:] if m["role"] == "assistant")
        self.calls.append((step, n))
        if n < len(script):
            name, args = script[n]
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{time.monotonic_ns()}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        return {"role": "assistant", "content": f"Finished: {step}"}


def wait(client, mid, statuses, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        m = client.get(f"/api/missions/{mid}").json()
        if m["status"] in statuses:
            return m
        time.sleep(0.1)
    raise AssertionError(f"mission {mid} stuck in {m['status']}: {m['events'][-5:]}")


def use(model):
    SCRIPT[:] = [model] * 200


def test_plan_review_then_run_to_done(client):
    model = Model(plan(("Create a packing list note", "A note titled Packing list exists", True),
                       ("Add a reminder task", "A task to pack exists", True)),
                  {"Create a packing list note": [("create_note", {"title": "Packing list", "body": "- passport"})],
                   "Add a reminder task": [("create_task", {"title": "Pack for Tokyo"})]})
    use(model)
    try:
        m = client.post("/api/missions", json={"goal": "Get me ready for the Tokyo trip"}).json()
        m = wait(client, m["id"], ("review",))
        assert [s["title"] for s in m["plan"]] == ["Create a packing list note", "Add a reminder task"]
        assert not [c for c in model.calls if c[0]]  # nothing ran before the plan was approved
        client.post(f"/api/missions/{m['id']}/start")
        m = wait(client, m["id"], ("done", "partial", "failed"))
        assert m["status"] == "done", m["events"]
        assert [s["status"] for s in m["plan"]] == ["done", "done"]
        assert m["plan"][0]["tools"][0]["name"] == "create_note" and m["plan"][0]["tools"][0]["ok"]
        assert m["used_tool_calls"] == 2 and "All finished." in m["report"] and "**Done**" in m["report"]
        assert any(t["title"] == "Pack for Tokyo" for t in client.get("/api/tasks").json())
        assert any(e["kind"] == "done" for e in m["events"])
    finally:
        SCRIPT.clear()


def test_claimed_done_without_a_tool_is_not_trusted(client):
    """The model says it is done and the check agrees, but a step that needed a tool never used one."""
    model = Model(plan(("Look up the client's address", "The address is quoted from a tool result", True)), {})
    use(model)
    try:
        m = client.post("/api/missions", json={"goal": "Find the client's address", "auto_start": True}).json()
        m = wait(client, m["id"], ("done", "partial", "failed"))
        assert m["status"] == "partial" and m["plan"][0]["status"] == "unverified"
        assert "Not verified" in m["report"]
    finally:
        SCRIPT.clear()


def test_failed_check_retries_then_fails_with_reason(client):
    model = Model(plan(("Create the budget doc", "A document titled Budget exists", True)),
                  {"Create the budget doc": [("read_note", {"doc_id": 999999})]},
                  verdicts={"Create the budget doc": "not_done"})
    use(model)
    try:
        m = client.post("/api/missions", json={"goal": "Make a budget document", "auto_start": True}).json()
        m = wait(client, m["id"], ("done", "partial", "failed"))
        assert m["status"] == "failed" and m["plan"][0]["status"] == "failed"
        assert "Step 1 failed" in m["error"]
        assert any(e["kind"] == "retry" for e in m["events"])
        assert m["plan"][0]["attempts"] <= 2
    finally:
        SCRIPT.clear()


def test_approval_pauses_the_mission_and_resumes_after_decision(client):
    client.put("/api/permissions", json={"groups": {"tasks": "ask"}})
    model = Model(plan(("Add the follow up task", "A task named Call Sam exists", True)),
                  {"Add the follow up task": [("create_task", {"title": "Call Sam"})]})
    use(model)
    try:
        m = client.post("/api/missions", json={"goal": "Remind me to call Sam", "auto_start": True}).json()
        m = wait(client, m["id"], ("waiting_approval",))
        assert m["approvals"] and m["approvals"][0]["tool"] == "create_task"
        assert not any(t["title"] == "Call Sam" for t in client.get("/api/tasks").json())
        client.post(f"/api/approvals/{m['approvals'][0]['id']}", json={"approve": True})
        m = wait(client, m["id"], ("done", "partial", "failed"))
        assert m["status"] == "done", m["events"]
        # approved once, run once: the re-run step replays the recorded action instead of creating a second task
        assert sum(1 for t in client.get("/api/tasks").json() if t["title"] == "Call Sam") == 1
        assert any(e["kind"] == "decision" for e in m["events"])
    finally:
        client.put("/api/permissions", json={"groups": {"tasks": "on"}})
        SCRIPT.clear()


def test_denied_approval_is_reported(client):
    client.put("/api/permissions", json={"groups": {"tasks": "ask"}})
    model = Model(plan(("Add the task", "A task named Denied thing exists", True)),
                  {"Add the task": [("create_task", {"title": "Denied thing"})]}, verdicts={"Add the task": "not_done"})
    use(model)
    try:
        m = client.post("/api/missions", json={"goal": "Add a task for the denied thing", "auto_start": True}).json()
        m = wait(client, m["id"], ("waiting_approval",))
        client.post(f"/api/approvals/{m['approvals'][0]['id']}", json={"approve": False})
        m = wait(client, m["id"], ("done", "partial", "failed", "waiting_approval"), 20)
        assert any("denied" in e["text"] for e in m["events"])
        assert not any(t["title"] == "Denied thing" for t in client.get("/api/tasks").json())
    finally:
        client.put("/api/permissions", json={"groups": {"tasks": "on"}})
        client.post(f"/api/missions/{m['id']}/cancel")
        SCRIPT.clear()


def test_recovery_resumes_without_repeating_writes(client):
    """Simulate a restart in the middle of a step whose write already happened."""
    from app import db, missions

    model = Model(plan(("Write the summary note", "A note titled Weekly summary exists", True)),
                  {"Write the summary note": [("create_note", {"title": "Weekly summary", "body": "ok"})]})
    use(model)
    try:
        m = client.post("/api/missions", json={"goal": "Write my weekly summary"}).json()
        m = wait(client, m["id"], ("review",))
        mid = m["id"]
        # the write happened before the crash...
        from app import assistant

        ctx = assistant.Ctx(channel="mission", conversation_id=m["conversation_id"], mission_id=mid)
        ok, _ = asyncio.run(assistant.run_tool("create_note", json.dumps({"title": "Weekly summary", "body": "ok"}), ctx))
        assert ok
        # ...and the process died with the step running
        plan_ = m["plan"]
        plan_[0].update(status="running", attempts=1)
        db.run("UPDATE missions SET status = 'running', plan = ? WHERE id = ?", json.dumps(plan_), mid)
        missions.recover()
        assert db.one("SELECT status FROM missions WHERE id = ?", mid)["status"] == "queued"
        client.post(f"/api/missions/{mid}/pause")  # nudge: pause then start exercises the controls too
        client.post(f"/api/missions/{mid}/start")
        m = wait(client, mid, ("done", "partial", "failed"))
        assert m["status"] == "done", m["events"]
        notes = [d for d in client.get("/api/notes", params={"q": "Weekly summary"}).json() if d["title"] == "Weekly summary"] \
            if client.get("/api/notes").status_code == 200 else db.all("SELECT * FROM docs WHERE title = 'Weekly summary'")
        assert len(db.all("SELECT id FROM docs WHERE title = 'Weekly summary'")) == 1, notes
        assert any(e["kind"] == "recovered" for e in m["events"])
        assert "Already done earlier in this mission" in m["plan"][0]["tools"][0]["preview"]
    finally:
        SCRIPT.clear()


def test_budget_pauses_and_can_be_raised(client):
    model = Model(plan(("Step one", "Note one exists", True), ("Step two", "Note two exists", True)),
                  {"Step one": [("create_note", {"title": "B1"})], "Step two": [("create_note", {"title": "B2"})]})
    use(model)
    try:
        m = client.post("/api/missions", json={"goal": "Two notes on a tight budget", "auto_start": True, "budget_tool_calls": 1}).json()
        m = wait(client, m["id"], ("paused", "done", "failed"))
        assert m["status"] == "paused" and "tool call limit" in m["error"]
        assert m["plan"][0]["status"] == "done" and m["plan"][1]["status"] == "pending"
        client.put(f"/api/missions/{m['id']}/budget", json={"budget_tool_calls": 10})
        client.post(f"/api/missions/{m['id']}/start")
        assert wait(client, m["id"], ("done", "partial", "failed"))["status"] == "done"
    finally:
        SCRIPT.clear()


def test_cancel_skips_the_rest(client):
    model = Model(plan(("Only step", "x", True)), {})
    use(model)
    try:
        m = client.post("/api/missions", json={"goal": "Something to cancel"}).json()
        m = wait(client, m["id"], ("review",))
        m = client.post(f"/api/missions/{m['id']}/cancel").json()
        assert m["status"] == "cancelled" and m["plan"][0]["status"] == "skipped"
        assert client.post(f"/api/missions/{m['id']}/cancel").status_code == 409
        assert client.delete(f"/api/missions/{m['id']}").json() == {"ok": True}
    finally:
        SCRIPT.clear()


def test_plan_editing_and_validation(client):
    model = Model("not json at all", {})
    use(model)
    try:
        m = client.post("/api/missions", json={"goal": "Plan a team offsite"}).json()
        m = wait(client, m["id"], ("review",))
        assert len(m["plan"]) == 1 and any(e["kind"] == "warning" for e in m["events"])  # unusable plan: one step fallback
        r = client.put(f"/api/missions/{m['id']}/plan", json={"steps": [{"title": "Pick dates", "done_when": "Two dates proposed", "needs_tool": False}]})
        assert r.status_code == 200 and r.json()["plan"][0]["title"] == "Pick dates"
        assert client.put(f"/api/missions/{m['id']}/plan", json={"steps": []}).status_code == 422
        assert client.post("/api/missions", json={"goal": "hi"}).status_code == 422
        client.post(f"/api/missions/{m['id']}/cancel")
    finally:
        SCRIPT.clear()


def test_missions_need_approval_for_consequential_tools(client):
    from app import assistant

    ctx = assistant.Ctx(channel="mission", conversation_id=1, mission_id=1)
    assert assistant.approval_reason("schedule_task", ctx)
    assert not assistant.approval_reason("create_note", ctx)
    assert not assistant.approval_reason("schedule_task", assistant.Ctx(channel="web", conversation_id=1))


def test_judge_rules():
    from app.missions import judge

    tool_ok, tool_bad = [{"ok": True}], [{"ok": False}]
    step = {"needs_tool": True}
    assert judge(step, {"verdict": "done"}, tool_ok) == "done"
    assert judge(step, {"verdict": "done"}, []) == "unverified"
    assert judge(step, {"verdict": "done"}, tool_bad) == "retry"
    assert judge(step, {"verdict": "cannot_tell"}, tool_ok) == "unverified"
    assert judge({"needs_tool": False}, {"verdict": "done"}, []) == "done"
