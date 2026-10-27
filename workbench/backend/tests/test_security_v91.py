"""Security v9.1 regressions: test the actual request / companion path."""
import asyncio
import json

import pytest

from test_computer import FakeCompanion, paired  # noqa: F401


def test_large_capture_never_persisted_and_not_truncated(client, paired):
    from app import computer, db
    # Deliberately larger than the previous 200 KB JSON truncation.
    image = "data:image/png;base64," + "A" * 400_000
    def response(kind, args):
        return True, {"image": image, "window": {"id": 7}, "captured_at": 5}
    with FakeCompanion(client, paired, reply=response):
        result = asyncio.run(computer.request("capture", {"app": "Notes"}, timeout=5))
    assert result["ok"] and result["result"]["image"] == image
    rows = db.all("SELECT args, result FROM computer_actions WHERE kind = 'capture'")
    assert rows and all("base64" not in (r["args"] or "") and not r["result"] for r in rows)


def test_text_and_idempotency_markers_do_not_retain_content(client, paired):
    from app import computer, db
    text = "PRIVATE-DESKTOP-TEXT-SHOULD-NOT-BE-STORED"
    with FakeCompanion(client, paired):
        out = asyncio.run(computer.request("type_text", {"app": "Notes", "text": text}, timeout=5))
    assert out["ok"]
    row = db.one("SELECT args, idem_key, result FROM computer_actions WHERE kind = 'type_text' ORDER BY id DESC LIMIT 1")
    assert text not in json.dumps(row)
    assert row["result"] is None
    assert len(row["idem_key"]) == 64


def test_cloud_semantics_do_not_export_local_project(client, monkeypatch):
    from app import db, memory, llm, projects
    p = projects.create("private-v91-test", local_only=True)
    ref = "unused/model"
    monkeypatch.setattr(memory, "embed_model", lambda: ref)
    monkeypatch.setattr(llm, "is_local", lambda model: False)
    sent = []
    monkeypatch.setattr(memory, "embed", lambda texts, ref=None: sent.extend(texts) or [[1.0, 0.0] for _ in texts])
    db.run("INSERT INTO memories(content,kind,source,confidence,project_id,created_at,updated_at) VALUES ('PRIVATE-LOCAL-PROJECT-CONTENT', 'fact', 'user',1,?,?,?)", p["id"], db.now(), db.now())
    memory.index_missing()
    assert not any("PRIVATE-LOCAL-PROJECT-CONTENT" in x for x in sent)
    assert memory.semantic("PRIVATE-PROJECT-QUERY", 5, p["id"]) == []
    assert "PRIVATE-PROJECT-QUERY" not in sent


def test_vision_denies_cloud_before_capture_in_local_project(client, paired, monkeypatch):
    from app import vision, projects
    p = projects.create("local-vision-v91-test", local_only=True)
    monkeypatch.setattr(vision, "usable", lambda pid=None: (False, "local-only") if pid == p["id"] else (True, ""))
    monkeypatch.setattr(vision, "_project_id", lambda cid, mid: p["id"])
    with pytest.raises(Exception, match="local-only"):
        asyncio.run(vision.locate("Notes", "Save", 123, None))
