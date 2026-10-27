"""Voice: the browser by default, providers local or cloud with consent, local-only, audio never kept, the spoken
style, nothing learned from voice, and a voice session that creates a task and ends without keeping the transcript."""

import json

import pytest

from conftest import CALLS, SCRIPT, VOICE, events, tool_call

AUDIO = b"RIFF" + b"\x00" * 2000  # stands in for recorded speech


@pytest.fixture()
def vc(client):
    from app import db

    yield client
    db.set_setting("voice", "")
    SCRIPT.clear()


def use_provider(client, consent=False):
    ref = f"{client.pid}/fake-model"
    client.put("/api/voice", json={"stt": "provider", "stt_model": ref, "tts": "provider", "tts_model": ref})
    if consent:
        client.put("/api/voice", json={"consent_for": "stt_model"})
        client.put("/api/voice", json={"consent_for": "tts_model"})


def test_browser_is_the_default_and_needs_no_provider(vc):
    s = vc.get("/api/voice").json()
    assert s["stt"] == "browser" and s["tts"] == "browser"
    assert vc.post("/api/voice/transcribe", content=AUDIO, headers={"Content-Type": "audio/wav"}).status_code == 409


def test_local_provider_transcribes_and_speaks_and_keeps_no_audio(vc):
    from app import db

    use_provider(vc)
    VOICE["heard"] = "what is due this week"
    r = vc.post("/api/voice/transcribe", content=AUDIO, headers={"Content-Type": "audio/wav"})
    assert r.status_code == 200 and r.json()["text"] == "what is due this week" and VOICE["audio"][-1] > len(AUDIO)
    sp = vc.post("/api/voice/speak", json={"text": "Two tasks are due."})
    assert sp.status_code == 200 and sp.headers["content-type"].startswith("audio/") and sp.content.startswith(b"ID3")
    dump = json.dumps(db.all("SELECT * FROM audit_log")) + json.dumps(db.all("SELECT * FROM tool_runs"))
    assert "RIFF" not in dump and "what is due this week" not in dump


def test_cloud_needs_consent_and_local_only_refuses_it(vc, monkeypatch):
    from app import projects, vision

    real = vision.destination
    monkeypatch.setattr(vision, "destination", lambda ref: {**real(ref), "local": False, "label": "Fake at api.example.com, a cloud service"})
    use_provider(vc)
    r = vc.post("/api/voice/transcribe", content=AUDIO, headers={"Content-Type": "audio/wav"})
    assert r.status_code == 403 and "api.example.com" in r.json()["detail"]
    use_provider(vc, consent=True)
    assert vc.post("/api/voice/transcribe", content=AUDIO, headers={"Content-Type": "audio/wav"}).status_code == 200
    p = projects.create("VOICE private", local_only=True)
    cid = vc.post("/api/assistant/chat", json={"message": "hi", "project_id": p["id"]}).text
    conv = next(c for c in vc.get(f"/api/conversations?project_id={p['id']}").json())
    r = vc.post(f"/api/voice/transcribe?conversation_id={conv['id']}", content=AUDIO, headers={"Content-Type": "audio/wav"})
    assert r.status_code == 403 and "local only" in r.json()["detail"]
    vc.put("/api/voice", json={"local_only": True})
    assert vc.post("/api/voice/transcribe", content=AUDIO, headers={"Content-Type": "audio/wav"}).status_code == 403
    # a new model needs new consent
    vc.put("/api/voice", json={"local_only": False, "stt_model": f"{vc.pid}/other"})
    assert vc.get("/api/voice").json()["consent"].get("stt_model") is None
    projects.set_status(p["id"], "deleted")
    projects.purge(p["id"], "VOICE private")
    assert cid


def test_voice_session_creates_a_task_and_can_end_without_keeping_the_transcript(vc):
    SCRIPT[:] = [{"role": "assistant", "content": "You have two things due. Anything to add?"},
                 tool_call("create_task", {"title": "Call Sam about the quote", "due": "2026-11-05"}, "t"),
                 {"role": "assistant", "content": "Done, I added a task to call Sam."}]
    ev = events(vc.post("/api/assistant/chat", json={"message": "Give me my briefing", "voice": True}))
    cid = next(e["conversation_id"] for e in ev if e["type"] == "start")
    system = [c for c in CALLS if c.get("tools")][-1]["messages"][0]["content"]
    assert "voice conversation" in system and "no markdown" in system
    n_learn = sum(1 for c in CALLS if "long term memory" in c["messages"][0]["content"])
    ev = events(vc.post("/api/assistant/chat", json={"message": "Add a task to call Sam about the quote by the 5th", "conversation_id": cid, "voice": True}))
    assert any(e["type"] == "tool_result" and e["name"] == "create_task" and e["ok"] for e in ev)
    assert sum(1 for c in CALLS if "long term memory" in c["messages"][0]["content"]) == n_learn  # nothing mined from voice
    assert vc.post(f"/api/voice/sessions/{cid}/end", json={"keep": False}).json() == {"kept": False}
    assert vc.get(f"/api/conversations/{cid}").status_code == 404
    assert any(t["title"] == "Call Sam about the quote" for t in vc.get("/api/tasks").json())  # what was done stays
