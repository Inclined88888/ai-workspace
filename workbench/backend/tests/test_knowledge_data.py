"""feat/memory-core data safety: backups and restore, export with files, importers."""
import io
import json
import zipfile


def test_backup_restore_and_undo(client):
    from app import knowledge

    knowledge.ensure()
    b = client.post("/api/knowledge/backups").json()
    assert b["kind"] == "manual"
    m = knowledge.remember("Backup canary: the shed key is under the pot", "profile")["memory"]
    res = client.post(f"/api/knowledge/backups/{b['name']}/restore").json()
    assert not any(x["id"] == m["id"] for x in knowledge.list_memories())
    client.post(f"/api/knowledge/backups/{res['undo_with']}/restore")  # restore can be undone
    assert any(x["id"] == m["id"] for x in knowledge.list_memories())
    assert any(x["name"] == b["name"] for x in client.get("/api/knowledge/backups").json()["backups"])
    assert client.post("/api/knowledge/backups/../../etc/restore").status_code == 404
    knowledge.forget(m["id"])


def test_export_zip_has_everything(client):
    from app import knowledge

    knowledge.remember("Export canary memory", "profile")
    client.post("/api/docs", json={"title": "Export note", "body": "hello"})
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/knowledge/export.zip").content))
    names = z.namelist()
    assert "knowledge.json" in names and "chats.json" in names
    assert any("Export canary" in m["content"] for m in json.loads(z.read("knowledge.json"))["memories"])


CHATGPT = [{"id": "c1", "title": "Deck quote", "create_time": 1700000000, "mapping": {
    "a": {"message": {"author": {"role": "user"}, "content": {"parts": ["How much for a 20m2 hardwood deck?"]}, "create_time": 1700000000}},
    "b": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["Roughly $9k-$12k installed."]}, "create_time": 1700000005}}}}]
CLAUDE = [{"uuid": "u1", "name": "Pergola", "created_at": "2024-01-01T00:00:00Z", "chat_messages": [
    {"sender": "human", "text": "Pergola footing depth in clay?", "created_at": "2024-01-01T00:00:00Z"},
    {"sender": "assistant", "text": "Usually 600mm minimum.", "created_at": "2024-01-01T00:00:05Z"}]}]


def test_importers_chatgpt_claude_obsidian(client):
    def up(source, name, data):
        return client.post(f"/api/knowledge/import/{source}", files={"file": (name, data)}).json()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("conversations.json", json.dumps(CHATGPT))
    assert up("chatgpt", "export.zip", buf.getvalue())["chats"] == 1
    assert up("chatgpt", "export.zip", buf.getvalue())["chats"] == 0  # no duplicates
    assert up("claude", "conversations.json", json.dumps(CLAUDE).encode())["chats"] == 1
    vault = io.BytesIO()
    with zipfile.ZipFile(vault, "w") as z:
        z.writestr("Vault/Suppliers.md", "Timber from [[Bunnings Trade]], hardware from [[Reece]]")
    assert up("obsidian", "vault.zip", vault.getvalue())["notes"] == 1
    hits = client.get("/api/knowledge/search", params={"q": "hardwood deck"}).json()
    assert any(h["kind"] == "message" for h in hits)
    assert client.get("/api/knowledge/entities", params={"type": "note", "q": "Reece"}).json()
    assert client.post("/api/knowledge/import/claude", files={"file": ("x.json", b"not json")}).status_code == 400


def test_phone_reads_knowledge_but_never_sensitive(client):
    from fastapi.testclient import TestClient

    from app import knowledge, main

    phone = TestClient(main.app, headers={"X-Workbench": "1"})
    start = client.post("/api/mobile/pair/start").json()
    token = phone.post("/api/mobile/pair/claim", json={"code": start["code"], "name": "kn phone", "platform": "ios"}).json()["device_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert phone.get("/api/mobile/knowledge/search?q=gate").status_code == 401
    p = knowledge.upsert_entity("project", "Phone Knowledge Job", {"status": "active"})
    knowledge.remember("Phone Knowledge Job alarm code is 4471", "project", project_id=p["id"], privacy="sensitive")
    knowledge.remember("Phone Knowledge Job skip bin arrives Monday", "project", project_id=p["id"])
    hits = phone.get("/api/mobile/knowledge/search", params={"q": "Phone Knowledge Job"}, headers=h).json()
    assert hits and not any("4471" in (x["title"] or "") for x in hits)
    assert any(e["name"] == "Phone Knowledge Job" for e in phone.get("/api/mobile/knowledge/entities?type=project", headers=h).json())
    d = phone.get(f"/api/mobile/knowledge/entities/{p['id']}", headers=h).json()
    assert "4471" not in str(d) and any(m["sensitive"] for m in d["memories"]) and any("skip bin" in m["content"] for m in d["memories"])
    assert phone.post("/api/mobile/knowledge/entities", headers=h).status_code in (404, 405)  # read-only
