import asyncio

from conftest import SCRIPT, events, tool_call


def test_interrupted_turn_does_not_break_the_conversation(client):
    """The browser closes the stream (Stop button, closed tab) right after the model asked for a tool."""
    from app import assistant

    cid = assistant.new_conversation("web")
    SCRIPT[:] = [tool_call("list_tasks", {}, "orphan")]

    async def interrupt():
        gen = assistant.run_turn(assistant.Ctx(channel="web", conversation_id=cid), "list my tasks")
        async for e in gen:
            if e["type"] == "tool_call":
                await gen.aclose()  # what Starlette does when the client disconnects
                return

    asyncio.run(interrupt())
    SCRIPT[:] = [{"role": "assistant", "content": "still here"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello again", "conversation_id": cid}))
    assert not [e for e in ev if e.get("type") == "error" or "error" in e], ev
    assert next(e for e in ev if e["type"] == "message")["content"] == "still here"


def test_an_interrupted_turn_is_closed_in_the_run_log(client):
    from app import assistant, db

    cid = assistant.new_conversation("web")
    SCRIPT[:] = [tool_call("list_tasks", {}, "x")]

    async def interrupt():
        gen = assistant.run_turn(assistant.Ctx(channel="web", conversation_id=cid), "list")
        async for e in gen:
            if e["type"] == "tool_call":
                await gen.aclose()
                return

    asyncio.run(interrupt())
    turn = db.one("SELECT * FROM actions WHERE kind = 'turn' AND conversation_id = ?", cid)
    assert turn["outcome"] == "interrupted" and "closed" in turn["failure_reason"]
    calls = db.all("SELECT kind FROM actions WHERE parent_action_id = ?", turn["id"])
    assert [c["kind"] for c in calls] == ["model_call"]  # the model asked for a tool; the tool never ran
