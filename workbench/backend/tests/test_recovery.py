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
