"""Visual targeting: the image test gate, consent, local-only, approvals, confidence, no fallback, no leaks."""

import base64
import json
import struct
import zlib

import pytest

from conftest import CALLS, SCRIPT, VISION, VISION_CALLS, events, tool_call
from test_computer import FakeCompanion, paired  # noqa: F401  (fixture)


def tiny_png():
    row = b"\x00" + b"\xff\xff\xff" * 8
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * 8)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def reply(kind, args):
    if kind == "capture":
        return True, {"image": tiny_png(), "window": {"id": 7, "app": args["app"], "title": "Doc", "bbox": [100, 50, 800, 600]},
                      "image_size": [8, 8], "captured_at": 1e12, "redacted": 1, "redaction": "password fields blacked out"}
    return True, {"active_app": args.get("app", ""), "windows": [], "elements": []} if kind == "observe" else {"after": {"clicked_at": [500, 350]}}


@pytest.fixture()
def vis(client, paired):  # noqa: F811
    client.paired_token = paired
    VISION.clear()
    VISION_CALLS.clear()
    yield client
    client.put("/api/computer/vision", json={"mode": "off", "local_only": False})
    VISION.clear()


def enable(client, mode="ask", consent=False):
    client.put("/api/computer/vision", json={"model": f"{client.pid}/fake-model"})
    assert client.post("/api/computer/vision/check").json()["check"]["ok"]
    return client.put("/api/computer/vision", json={"mode": mode, "consent": consent})


def test_model_must_pass_the_image_test(vis):
    vis.put("/api/computer/vision", json={"model": f"{vis.pid}/fake-model"})
    assert vis.put("/api/computer/vision", json={"mode": "ask"}).status_code == 400  # not tested yet
    VISION["answer"] = lambda text, m: "Blue"
    r = vis.post("/api/computer/vision/check").json()
    assert not r["check"]["ok"] and "cannot be trusted" in r["check"]["error"]
    assert vis.put("/api/computer/vision", json={"mode": "ask"}).status_code == 400
    VISION.clear()
    assert enable(vis).status_code == 200
    assert VISION_CALLS[-1]["max_tokens"] == 32  # the test itself is capped


def test_cloud_needs_consent_and_local_only_refuses_cloud(vis, monkeypatch):
    from app import vision

    real = vision.destination
    monkeypatch.setattr(vision, "destination", lambda ref: {**real(ref), "local": False, "label": "Fake (fake-model) at api.example.com, a cloud service"})
    r = enable(vis)
    assert r.status_code == 400 and "would be sent to Fake (fake-model) at api.example.com, a cloud service" in r.json()["detail"]
    assert vis.put("/api/computer/vision", json={"mode": "ask", "consent": True}).status_code == 200
    assert vis.get("/api/computer").json()["vision"]["consent"]["destination"].startswith("Fake")
    assert vis.put("/api/computer/vision", json={"local_only": True}).json()  # turning local-only on
    ok, why = vision.usable()
    assert not ok and "Local models only" in why
    # changing the model resets mode and consent
    vis.put("/api/computer/vision", json={"local_only": False, "model": f"{vis.pid}/other-model"})
    s = vis.get("/api/computer").json()["vision"]
    assert s["mode"] == "off" and s["consent"] == {}


def test_locate_asks_first_then_click_goes_through_the_companion(vis):
    enable(vis, "ask")
    VISION["answer"] = lambda text, m: json.dumps({"found": True, "x": 0.5, "y": 0.5, "confidence": 0.92, "label": "Add row"})
    with FakeCompanion(vis, vis.paired_token, reply=reply) as comp:
        SCRIPT[:] = [tool_call("computer_locate", {"app": "Numbers", "description": "the Add row button"}, "l"), {"role": "assistant", "content": "waiting"}]
        ev = events(vis.post("/api/assistant/chat", json={"message": "add a row"}))
        ap = next(e for e in ev if e["type"] == "approval")
        assert "sends it to Fake" in next(a["reason"] for a in vis.get("/api/approvals").json() if a["id"] == ap["id"])
        assert comp.seen == []  # nothing captured before approval
        r = vis.post(f"/api/approvals/{ap['id']}", json={"approve": True}).json()
        assert r["status"] == "approved" and '"found": true' in r["result"] and "base64" not in r["result"]
        tid = json.loads(r["result"])["target"]
        assert vis.get("/api/computer").json()["screen_shared"]["app"] == "Numbers"
        SCRIPT[:] = [tool_call("computer_click_visual", {"target": tid, "app": "Numbers"}, "c"), {"role": "assistant", "content": "done"}]
        # Visual targets are scoped to the conversation that captured the window.
        original_cid = next(e["conversation_id"] for e in ev if e["type"] == "start")
        ev = events(vis.post("/api/assistant/chat", json={"message": "click it", "conversation_id": original_cid}))
        res = next(e for e in ev if e["type"] == "tool_result")
        assert res["ok"], res
        click = comp.seen[-1]
        assert click["kind"] == "click_visual" and click["args"]["bbox"] == [100, 50, 800, 600] and click["args"]["nx"] == 0.5
    # the chat model never saw an image, and nothing logged one
    chat_calls = [c for c in CALLS if c.get("tools")]
    assert chat_calls and not any("image_url" in json.dumps(c) or "base64" in json.dumps(c) for c in chat_calls)
    logs = json.dumps(vis.get("/api/audit").json()) + json.dumps(vis.get("/api/tool-runs").json()) + json.dumps(vis.get("/api/computer/history").json())
    assert "base64" not in logs and "screen shared" in logs


def test_session_mode_low_confidence_and_risky_labels(vis):
    enable(vis, "session")
    with FakeCompanion(vis, vis.paired_token, reply=reply):
        VISION["answer"] = lambda text, m: json.dumps({"found": True, "x": 0.4, "y": 0.4, "confidence": 0.5, "label": "Save"})
        SCRIPT[:] = [tool_call("computer_locate", {"app": "Numbers", "description": "Save"}, "l"), {"role": "assistant", "content": "ok"}]
        ev = events(vis.post("/api/assistant/chat", json={"message": "find save"}))
        assert not [e for e in ev if e["type"] == "approval"]  # session: no per-screenshot approval
        assert "Not sure enough" in next(e for e in ev if e["type"] == "tool_result")["preview"]
        VISION["answer"] = lambda text, m: json.dumps({"found": True, "x": 1.7, "y": 0.4, "confidence": 0.99, "label": "Save"})
        SCRIPT[:] = [tool_call("computer_locate", {"app": "Numbers", "description": "Save"}, "l2"), {"role": "assistant", "content": "ok"}]
        ev = events(vis.post("/api/assistant/chat", json={"message": "find save"}))
        assert "outside the window" in next(e for e in ev if e["type"] == "tool_result")["preview"]
        VISION["answer"] = lambda text, m: json.dumps({"found": True, "x": 0.2, "y": 0.2, "confidence": 0.95, "label": "Send invoice"})
        SCRIPT[:] = [tool_call("computer_locate", {"app": "Numbers", "description": "send"}, "l3"), {"role": "assistant", "content": "ok"}]
        ev = events(vis.post("/api/assistant/chat", json={"message": "find send"}))
        tid = json.loads(next(e for e in ev if e["type"] == "tool_result")["preview"])["target"]
        SCRIPT[:] = [tool_call("computer_click_visual", {"target": tid, "app": "Numbers"}, "c"), {"role": "assistant", "content": "ok"}]
        ev = events(vis.post("/api/assistant/chat", json={"message": "click it"}))
        assert [e["name"] for e in ev if e["type"] == "approval"] == ["computer_click_visual"]  # "Send" read on screen


def test_no_fallback_and_blocked_apps_are_never_captured(vis):
    enable(vis, "session")
    vis.put("/api/computer/settings", json={"app_rules": {"Mail": "blocked"}})
    with FakeCompanion(vis, vis.paired_token, reply=reply) as comp:
        SCRIPT[:] = [tool_call("computer_locate", {"app": "Mail", "description": "Send"}, "l"), {"role": "assistant", "content": "ok"}]
        ev = events(vis.post("/api/assistant/chat", json={"message": "find it"}))
        assert "blocked" in next(e for e in ev if e["type"] == "tool_result")["preview"] and comp.seen == []
    vis.put("/api/computer/settings", json={"app_rules": {}})
    from app import vision

    def boom(text, m):
        raise RuntimeError("down")
    n = len(VISION_CALLS)
    VISION["answer"] = lambda text, m: (_ for _ in ()).throw(RuntimeError("down"))
    with FakeCompanion(vis, vis.paired_token, reply=reply):
        SCRIPT[:] = [tool_call("computer_locate", {"app": "Numbers", "description": "x"}, "l"), {"role": "assistant", "content": "ok"}]
        events(vis.post("/api/assistant/chat", json={"message": "find"}))
    assert {c["model"] for c in VISION_CALLS[n:]} <= {"fake-model"}  # only the chosen model ever got the screenshot
    assert vision.settings()["model"].endswith("/fake-model")
