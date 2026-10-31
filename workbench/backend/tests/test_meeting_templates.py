"""Meeting templates steer the notes (Phase 4) without changing their shape."""

import json

from conftest import CALLS, SCRIPT

NOTES = {"summary": "Retro.", "notes": ["Went well: shipping"], "decisions": [], "actions": [{"task": "Try pairing", "owner": "Sam", "due": ""}],
         "attendees": ["Sam"], "follow_up": {"subject": "Retro", "body": "Thanks all"}}


def test_templates_are_listed_and_steer_the_prompt(client):
    t = client.get("/api/meetings/templates")
    assert t.status_code == 200 and {"general", "standup", "retro", "one_on_one", "sales"} <= {x["id"] for x in t.json()}

    m = client.post("/api/meetings/import?name=retro.txt", content=b"Sam: shipping went well. Let's try pairing.").json()
    assert client.post(f"/api/meetings/{m['id']}/summarise", json={"template": "nope"}).status_code == 422

    SCRIPT[:] = [{"role": "assistant", "content": json.dumps(NOTES)}]
    CALLS.clear()
    n = client.post(f"/api/meetings/{m['id']}/summarise", json={"template": "retro"}).json()["notes"]
    system = CALLS[-1]["messages"][0]["content"]
    assert "retrospective" in system and "Try next:" in system
    assert n["template"] == "retro" and n["actions"][0]["task"] == "Try pairing"

    # editing the notes keeps the template; a bogus one is dropped
    e = client.patch(f"/api/meetings/{m['id']}", json={"notes": {**n, "summary": "Edited"}})
    assert e.status_code == 200 and e.json()["notes"]["template"] == "retro"
    e = client.patch(f"/api/meetings/{m['id']}", json={"notes": {**n, "template": "evil"}})
    assert "template" not in e.json()["notes"]


def test_general_template_is_the_old_prompt(client):
    from app import meetings

    m = client.post("/api/meetings/import?name=x.txt", content=b"A: hello").json()
    SCRIPT[:] = [{"role": "assistant", "content": json.dumps(NOTES)}]
    CALLS.clear()
    client.post(f"/api/meetings/{m['id']}/summarise", json={})
    assert CALLS[-1]["messages"][0]["content"] == meetings.PROMPT
