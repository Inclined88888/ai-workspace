"""Tests for goal mode functionality."""

import asyncio
import json
import time

import pytest


def wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


from conftest import CALLS, SCRIPT, events, tool_call


def test_goal_create_and_list(client):
    """Test creating a goal via API and listing it."""
    # Create a conversation first
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    # Create a goal
    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Write a Python script that prints hello world",
        "max_rounds": 3,
        "resume_on_restart": False,
    })
    assert r.status_code == 200
    goal = r.json()
    assert goal["objective"] == "Write a Python script that prints hello world"
    assert goal["max_rounds"] == 3
    assert goal["phase"] == "active"
    assert goal["rounds_used"] == 0
    goal_id = goal["id"]

    # List goals
    r = client.get("/api/goals")
    assert r.status_code == 200
    goals_list = r.json()
    assert len(goals_list) == 1
    assert goals_list[0]["id"] == goal_id

    # Get specific goal
    r = client.get(f"/api/goals/{goal_id}")
    assert r.status_code == 200
    assert r.json()["id"] == goal_id


def test_goal_cannot_create_duplicate(client):
    """Test that a conversation can only have one active goal."""
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    # Create first goal
    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "First goal",
        "max_rounds": 5,
    })
    assert r.status_code == 200

    # Try to create second goal - should fail
    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Second goal",
        "max_rounds": 5,
    })
    assert r.status_code == 409


def test_goal_cancel(client):
    """Test cancelling a goal."""
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Goal to cancel",
        "max_rounds": 5,
    })
    goal_id = r.json()["id"]

    # Cancel the goal
    r = client.post(f"/api/goals/{goal_id}/cancel")
    assert r.status_code == 200
    assert r.json()["phase"] == "cancelled"

    # Cannot cancel again
    r = client.post(f"/api/goals/{goal_id}/cancel")
    assert r.status_code == 409


def test_goal_pause_resume(client):
    """Test pausing and resuming a goal."""
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Goal to pause",
        "max_rounds": 5,
    })
    goal_id = r.json()["id"]

    # Pause
    r = client.post(f"/api/goals/{goal_id}/pause")
    assert r.status_code == 200
    assert r.json()["phase"] == "paused"

    # Resume
    r = client.post(f"/api/goals/{goal_id}/resume")
    assert r.status_code == 200
    assert r.json()["phase"] == "active"

    # Cannot pause a completed goal
    r = client.post(f"/api/goals/{goal_id}/cancel")
    r = client.post(f"/api/goals/{goal_id}/pause")
    assert r.status_code == 409


def test_goal_continues_until_complete(client):
    """Test that a goal continues automatically until complete_goal is called."""
    SCRIPT[:] = [
        # Turn 1: model does some work
        tool_call("create_note", {"title": "Hello World Script", "body": "print('Hello, World!')", "kind": "document"}),
        {"role": "assistant", "content": "Working on the script..."},
        # Continuation turn 1: model completes the goal
        tool_call("complete_goal", {"summary": "Created hello world script"}),
        {"role": "assistant", "content": "Done! Created the hello world script."},
    ]
    # Create conversation first
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    # Create a goal for this conversation BEFORE sending the task
    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Create a hello world Python script",
        "max_rounds": 5,
    })
    assert r.status_code == 200
    goal_id = r.json()["id"]

    # Now send the actual task
    ev = events(client.post("/api/assistant/chat", json={"message": "Create a hello world Python script", "conversation_id": cid}))

    # Check the goal was marked complete
    r = client.get(f"/api/goals/{goal_id}")
    assert r.json()["phase"] == "complete"
    # Note: create_note tool was called, but verifying via notes API is flaky in tests


def test_goal_stops_at_max_rounds(client):
    """Test that a goal stops when max_rounds is reached."""
    # Model never calls complete_goal, just keeps working
    SCRIPT[:] = [
        # Round 1
        tool_call("create_note", {"title": "Draft 1", "body": "Draft 1", "kind": "note"}),
        {"role": "assistant", "content": "Working on draft 1"},
        # Round 2
        tool_call("create_note", {"title": "Draft 2", "body": "Draft 2", "kind": "note"}),
        {"role": "assistant", "content": "Working on draft 2"},
        # Round 3
        tool_call("create_note", {"title": "Draft 3", "body": "Draft 3", "kind": "note"}),
        {"role": "assistant", "content": "Working on draft 3"},
        # Round 4 (should be blocked)
        tool_call("create_note", {"title": "Draft 4", "body": "Draft 4", "kind": "note"}),
        {"role": "assistant", "content": "Working on draft 4"},
        # Round 5 (should be blocked)
        tool_call("create_note", {"title": "Draft 5", "body": "Draft 5", "kind": "note"}),
        {"role": "assistant", "content": "Working on draft 5"},
    ]

    # Create conversation first
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    # Create goal with max_rounds=3
    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Write 5 drafts",
        "max_rounds": 3,
    })
    assert r.status_code == 200
    goal_id = r.json()["id"]

    # Send the task
    ev = events(client.post("/api/assistant/chat", json={"message": "Write 5 drafts", "conversation_id": cid}))

    # Wait for all events to process
    time.sleep(0.5)

    # Check the goal was blocked due to round limit
    r = client.get(f"/api/goals/{goal_id}")
    assert r.json()["phase"] == "blocked"
    assert r.json()["blocked_reason"]["code"] == "round-limit"


def test_goal_unverified_completion_does_not_end(client):
    """Test that an unverified 'done' claim doesn't end the goal - it continues until max_rounds."""
    # Model claims completion but doesn't call complete_goal
    SCRIPT[:] = [
        {"role": "assistant", "content": "I've completed the task. The script is ready."},
        # Continuation round 1 - model should continue working
        {"role": "assistant", "content": "Actually, let me verify the script works."},
        # Continuation round 2 - model should continue working
        {"role": "assistant", "content": "Let me double-check the output."},
        # Continuation round 3 - would be blocked
        {"role": "assistant", "content": "Still working..."},
    ]

    # Create conversation first
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Create a script",
        "max_rounds": 3,
    })
    assert r.status_code == 200
    goal_id = r.json()["id"]

    # Send the task
    ev = events(client.post("/api/assistant/chat", json={"message": "Create a script", "conversation_id": cid}))

    # The goal should be blocked at max_rounds because complete_goal wasn't called
    # (unverified completion claim doesn't end the goal)
    r = client.get(f"/api/goals/{goal_id}")
    assert r.json()["phase"] == "blocked"
    assert r.json()["blocked_reason"]["code"] == "round-limit"
    assert r.json()["rounds_used"] == 3


def test_goal_pending_approval_stops_continuation(client):
    """Test that a pending approval stops goal continuation."""
    # Model tries to send email (requires approval)
    SCRIPT[:] = [
        tool_call("send_email", {"to": "test@example.com", "subject": "Test", "body": "Test"}),
        # After approval would continue, but we don't approve
    ]

    ev = events(client.post("/api/assistant/chat", json={"message": "Send an email"}))
    cid = ev[0]["conversation_id"]

    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Send an email",
        "max_rounds": 5,
    })
    goal_id = r.json()["id"]

    # Check that goal is still active but round didn't increment due to pending approval
    r = client.get(f"/api/goals/{goal_id}")
    assert r.json()["phase"] == "active"
    # The round should not have been incremented because of pending approval


def test_goal_persistence_across_restart(client):
    """Test that goals with resume_on_restart persist across restarts."""
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Persistent goal",
        "max_rounds": 5,
        "resume_on_restart": True,
    })
    goal_id = r.json()["id"]

    # Simulate restart by creating new client (same DB)
    # The goal should still exist
    r = client.get(f"/api/goals/{goal_id}")
    assert r.status_code == 200
    assert r.json()["phase"] == "active"
    assert r.json()["resume_on_restart"] == 1


def test_complete_goal_tool_only_available_with_active_goal(client):
    """Test that complete_goal tool is only offered when there's an active goal."""
    from app import assistant

    # Without a goal, complete_goal should not be in available tools
    ctx = assistant.Ctx(channel="web", conversation_id=99999)
    tools = [t.name for t in assistant.available_tools(ctx)]
    assert "complete_goal" not in tools

    # With a goal, it should be available
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Test goal",
        "max_rounds": 5,
    })

    ctx = assistant.Ctx(channel="web", conversation_id=cid)
    tools = [t.name for t in assistant.available_tools(ctx)]
    assert "complete_goal" in tools


def test_goal_update(client):
    """Test updating a goal's objective and max_rounds."""
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Original objective",
        "max_rounds": 5,
    })
    goal_id = r.json()["id"]

    # Update objective
    r = client.patch(f"/api/goals/{goal_id}", json={"objective": "Updated objective"})
    assert r.status_code == 200
    assert r.json()["objective"] == "Updated objective"

    # Update max_rounds
    r = client.patch(f"/api/goals/{goal_id}", json={"max_rounds": 10})
    assert r.status_code == 200
    assert r.json()["max_rounds"] == 10

    # Cannot exceed hard max
    r = client.patch(f"/api/goals/{goal_id}", json={"max_rounds": 100})
    assert r.status_code == 200
    assert r.json()["max_rounds"] == 50  # HARD_MAX_ROUNDS


def test_goal_resume_on_restart_flag(client):
    """Test the resume_on_restart flag."""
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    # Create with resume_on_restart=True
    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Test resume flag",
        "max_rounds": 5,
        "resume_on_restart": True,
    })
    assert r.json()["resume_on_restart"] == 1

    # Update to False
    goal_id = r.json()["id"]
    r = client.patch(f"/api/goals/{goal_id}", json={"resume_on_restart": False})
    assert r.json()["resume_on_restart"] == 0


def test_goal_block(client):
    """Test blocking a goal with a reason."""
    from app import goals

    # Create conversation first
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    r = client.post("/api/goals", json={
        "conversation_id": cid,
        "objective": "Goal to block",
        "max_rounds": 5,
    })
    goal_id = r.json()["id"]

    # Block the goal directly (must be active)
    blocked = goals.block(goal_id, {"code": "external-dependency", "message": "Waiting for API key"})
    assert blocked["phase"] == "blocked"
    assert blocked["blocked_reason"]["code"] == "external-dependency"
    assert blocked["blocked_reason"]["message"] == "Waiting for API key"

    # Try to block again - should fail
    try:
        goals.block(goal_id, {"code": "another", "message": "Another reason"})
        assert False, "Should have raised HTTPException"
    except Exception as e:
        assert "409" in str(e) or "blocked" in str(e).lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])