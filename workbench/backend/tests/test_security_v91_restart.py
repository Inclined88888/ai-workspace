"""v9.1 security across a restart: what lived only in memory is gone and fails closed, nothing private is on disk,
and an approval you have not answered yet still works (its arguments were encrypted, not lost)."""

import asyncio
import json

from conftest import SCRIPT, tool_call
from test_computer import FakeCompanion, chat, paired  # noqa: F401  (fixture)

TEXT = "Meeting notes: the vault code is 7781"


def restart():
    """What a process restart does to Computer Use state: memory is cleared, then recovery runs."""
    from app import computer, missions, vision

    computer._pending_args.clear()
    computer._transient_results.clear()
    computer._shots.clear()
    computer._events.clear()
    vision._targets.clear()
    vision._last_share.clear()
    missions.recover()
    computer.recover()


def test_a_queued_action_is_not_replayed_from_disk_after_a_restart(client, paired):  # noqa: F811
    from app import computer, db

    dev = db.one("SELECT id FROM computer_devices WHERE revoked = 0")
    aid = db.run("INSERT INTO computer_actions(device_id, kind, args, risk, status, created_at) VALUES (?, 'type_text', ?, 'write', 'queued', ?)",
                 dev["id"], json.dumps({"app": "Notes", "text_length": len(TEXT)}), db.now())
    restart()
    with FakeCompanion(client, paired) as comp:
        assert computer.next_action(db.one("SELECT * FROM computer_devices WHERE id = ?", dev["id"])) is None
        assert comp.seen == []  # the companion never received the old action
    row = db.one("SELECT status, args, result FROM computer_actions WHERE id = ?", aid)
    assert row["status"] in ("cancelled", "failed") and TEXT not in json.dumps(row)


def test_screenshots_and_visual_targets_do_not_survive_a_restart(client, paired):  # noqa: F811
    from fastapi import HTTPException

    from app import computer, db, vision

    vision._targets["vstale"] = {"app": "Notes", "window_id": 1, "bbox": [0, 0, 10, 10], "nx": 0.5, "ny": 0.5, "captured_at": 1,
                                 "label": "x", "expires": 1e18, "conversation_id": None, "mission_id": None, "device_id": 1}
    computer._shots[1] = (1e18, "data:image/png;base64,AAAA")
    restart()
    try:
        vision.target("vstale")
        raise AssertionError("a target survived the restart")
    except HTTPException as e:
        assert e.status_code == 409
    assert computer._shots == {}
    dump = json.dumps(db.all("SELECT * FROM computer_actions")) + json.dumps(db.all("SELECT args, result FROM approvals"))
    assert "base64" not in dump


def test_a_pending_approval_still_works_after_a_restart_and_types_exactly_once(client, paired):  # noqa: F811
    from app import db

    client.put("/api/computer/settings", json={"mode": "ask"})
    # one companion throughout: it is a separate program and keeps running while ROOKS restarts
    with FakeCompanion(client, paired) as comp:
        SCRIPT[:] = [tool_call("computer_type", {"text": TEXT, "app": "Notes"}, "t"), {"role": "assistant", "content": "Waiting."}]
        ev, _ = chat(client, "type my notes")
        ap = next(e for e in ev if e["type"] == "approval")
        assert comp.seen == []
        raw = db.one("SELECT args FROM approvals WHERE id = ?", ap["id"])["args"]
        assert raw.startswith("enc:") and TEXT not in raw
        restart()
        assert TEXT in next(a for a in client.get("/api/approvals").json() if a["id"] == ap["id"])["args"]  # still visible to decide
        assert client.post(f"/api/approvals/{ap['id']}", json={"approve": True}).json()["status"] == "approved"
        assert [s["args"]["text"] for s in comp.seen] == [TEXT]
        assert client.post(f"/api/approvals/{ap['id']}", json={"approve": True}).status_code == 409  # never twice
    row = db.one("SELECT args, result FROM computer_actions WHERE kind = 'type_text' ORDER BY id DESC LIMIT 1")
    assert TEXT not in json.dumps(row)
    SCRIPT.clear()


def test_an_action_in_flight_at_restart_is_uncertain_and_blocks_a_blind_repeat(client, paired):  # noqa: F811
    from app import computer, db

    with FakeCompanion(client, paired, reply=lambda kind, args: (True, {})):
        asyncio.run(computer.request("observe", {"app": "Notes"}, timeout=5))  # a look before, so the repeat check has a baseline
    dev = db.one("SELECT id FROM computer_devices WHERE revoked = 0")
    key = computer._canon("type_text", {"app": "Notes", "text": TEXT})
    db.run("INSERT INTO computer_actions(device_id, kind, args, risk, status, idem_key, created_at, dispatched_at) "
           "VALUES (?, 'type_text', '{}', 'write', 'dispatched', ?, ?, ?)", dev["id"], key, db.now(), db.now())
    restart()
    assert db.one("SELECT status FROM computer_actions WHERE idem_key = ? ORDER BY id DESC LIMIT 1", key)["status"] == "uncertain"
    from fastapi import HTTPException
    with FakeCompanion(client, paired) as comp:
        try:
            asyncio.run(computer.request("type_text", {"app": "Notes", "text": TEXT}, timeout=5))
            raise AssertionError("a blind repeat was allowed")
        except HTTPException as e:
            assert e.status_code == 409 and "Observe the screen first" in e.detail
        assert comp.seen == []
