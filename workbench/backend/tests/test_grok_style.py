"""feat/grok-style: teammates, groups, vault, outbox, pages, channels and the encrypted phone link. Offline."""

import asyncio
import json
import time

import pytest

from conftest import CALLS, SCRIPT, events, tool_call


@pytest.fixture(autouse=True)
def quiet_learning(monkeypatch):
    from app.adapt import knowledge

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(knowledge, "model_json", nothing)


def mate(c, **kw):
    r = c.post("/api/teammates", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_default_teammate_templates_sections(client):
    c = client
    lst = c.get("/api/teammates").json()
    assert any(t["is_default"] for t in lst["teammates"])
    assert {t["key"] for t in lst["templates"]} >= {"personal", "work", "study", "trade"}
    t = mate(c, template="trade", name="Trade office")
    assert t["role"].startswith("Office manager") and "packs" in t["tools"] and t["section_id"]
    assert c.post("/api/teammates", json={"name": "trade office"}).status_code == 409
    secs = {s["name"] for s in c.get("/api/teammates").json()["sections"]}
    assert {"Personal", "Work"} <= secs
    t2 = c.patch(f"/api/teammates/{t['id']}", json={"section": "Jobs", "avatar": "🔧", "color": "leaf"}).json()
    assert t2["avatar"] == "🔧" and t2["color"] == "leaf"
    d = next(x for x in lst["teammates"] if x["is_default"])
    assert c.delete(f"/api/teammates/{d['id']}").status_code == 400


def test_teammate_chat_persona_memory_and_tool_scope(client):
    c = client
    t = mate(c, name="Scholar", persona="Explains like a patient tutor.", tools=["web", "packs"])
    c.post(f"/api/teammates/{t['id']}/notes", json={"content": "Student is in year 11 chemistry"})
    CALLS.clear()
    ev = events(c.post(f"/api/teammates/{t['id']}/chat", json={"message": "help me with moles"}))
    assert ev[0]["teammate_id"] == t["id"]
    body = CALLS[-1]
    system = body["messages"][0]["content"]
    assert "you are Scholar" in system and "patient tutor" in system and "year 11 chemistry" in system
    names = {x["function"]["name"] for x in body["tools"]}
    from app import assistant

    full = {x.name for x in assistant.TOOLS}
    assert names <= full and "draft_message" in names
    assert "send_email" not in names and "use_computer" not in names and "list_files" not in names
    assert "create_note" in names  # ungrouped tools stay
    cid = ev[0]["conversation_id"]
    detail = c.get(f"/api/teammates/{t['id']}").json()
    assert any(x["id"] == cid for x in detail["conversations"])
    assert detail["computer"]["browser_profile"] == f"tm-{t['id']}"
    # another teammate's conversation can't be continued here
    other = mate(c, name="Other")
    assert c.post(f"/api/teammates/{other['id']}/chat", json={"message": "x", "conversation_id": cid}).status_code == 404
    # its own memory stays its own
    CALLS.clear()
    events(c.post(f"/api/teammates/{other['id']}/chat", json={"message": "hi"}))
    assert "year 11 chemistry" not in CALLS[-1]["messages"][0]["content"]


def test_draft_before_send_and_secure_form(client, monkeypatch):
    c = client
    t = mate(c, name="Mailer")
    SCRIPT[:] = [tool_call("draft_message", {"channel": "email", "to": "jane@example.com", "subject": "Quote", "body": "Hi Jane"}),
                 tool_call("request_secure_form", {"kind": "login", "label": "Supplier portal", "reason": "to check orders"}, "c2"),
                 {"role": "assistant", "content": "Drafted it and asked for the login."}]
    ev = events(c.post(f"/api/teammates/{t['id']}/chat", json={"message": "draft a quote email to jane and get the portal login"}))
    drafts = [e for e in ev if e["type"] == "draft"]
    forms = [e for e in ev if e["type"] == "secure_form"]
    assert drafts and drafts[0]["status"] == "draft" and drafts[0]["recipients"] == "jane@example.com"
    assert forms and {f["id"] for f in forms[0]["fields"]} >= {"username", "password"}
    did = drafts[0]["id"]
    c.patch(f"/api/outbox/{did}", json={"body": "Hi Jane, quote attached."})
    sent = []

    async def fake_send(to, subject, body, in_reply_to="", account_id=None):
        sent.append((to, subject, body))
        return {"sent": True, "from": "me@example.com"}

    from app import assistant

    monkeypatch.setattr(assistant, "_send_email", fake_send)
    r = c.post(f"/api/outbox/{did}/send")
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert sent == [("jane@example.com", "Quote", "Hi Jane, quote attached.")]
    assert c.post(f"/api/outbox/{did}/send").status_code == 409  # never twice
    # fill the secure form: the secret never goes back to the model or the list
    fid = forms[0]["id"]
    r = c.post(f"/api/vault/forms/{fid}", json={"values": {"site": "https://supplier.example", "username": "bob@trade.au",
                                                          "password": "hunter2-very-secret"}}).json()
    assert r["form"]["status"] == "filled"
    listing = json.dumps(c.get("/api/vault").json())
    assert "hunter2" not in listing, listing[:600]
    from app.teammates import vault

    assert "hunter2" not in json.dumps(vault.for_model(None))
    assert c.post(f"/api/vault/{r['item']['id']}/reveal").json()["values"]["password"] == "hunter2-very-secret"
    assert c.post(f"/api/vault/forms/{fid}", json={"values": {}}).status_code == 409


def test_vault_card_checks(client):
    c = client
    assert c.post("/api/vault", json={"kind": "card", "label": "Bad", "values": {"number": "4242 4242 4242 4241"}}).status_code == 400
    v = c.post("/api/vault", json={"kind": "card", "label": "Visa", "values": {"number": "4242 4242 4242 4242", "cvc": "123",
                                                                              "expiry": "12/29"}}).json()
    assert v["secret_hints"]["number"] == "•••• 4242" and "123" not in json.dumps(v)


def test_group_mentions_handoff_and_no_outbound_tools(client):
    c = client
    a = mate(c, name="Lead")
    b = mate(c, name="Research desk")
    g = c.post("/api/groups", json={"members": [a["id"], b["id"]]}).json()
    from app.teammates import groups

    assert groups.mentions("hey @research desk and @Lead", g["members"]) == [b["id"], a["id"]]
    SCRIPT[:] = [{"role": "assistant", "content": "On it. @Research desk can you find three suppliers?"},
                 {"role": "assistant", "content": "Found three suppliers."}]
    CALLS.clear()
    post = groups.post(g["id"], "Find me tile suppliers", background=False)
    assert post["targets"] == [a["id"]]
    asyncio.run(groups.run_round(g["id"], post["targets"]))
    msgs = c.get(f"/api/groups/{g['id']}").json()["messages"]
    assert [m["author_id"] for m in msgs] == [None, a["id"], b["id"]]
    assert msgs[2]["handoff_from"] == a["id"]
    for call in CALLS:
        names = {x["function"]["name"] for x in call.get("tools") or []}
        assert "send_email" not in names and "draft_message" in names and "request_secure_form" not in names
    r = c.post("/api/reactions", json={"scope": "group", "ref": msgs[1]["id"], "emoji": "👍"}).json()
    assert r["reactions"] == ["👍"]
    assert c.post("/api/reactions", json={"scope": "group", "ref": 1, "emoji": "💩"}).status_code == 400


def test_templates_export_import_no_secrets(client):
    c = client
    t = mate(c, name="Exporter", persona="Tidy", tools=["mail"])
    c.post(f"/api/teammates/{t['id']}/notes", json={"content": "private fact"})
    r = c.post(f"/api/teammates/{t['id']}/routines", json={"text": "Every weekday at 8am, summarise my inbox"})
    assert r.status_code == 200, r.text
    tpl = c.get(f"/api/teammates/{t['id']}/template").json()
    assert tpl["kind"] == "ai-workspace.teammate" and "notes" not in tpl and tpl["routines"]
    new = c.post("/api/teammates/import", json=tpl).json()
    assert new["name"] == "Exporter 2" and new["tools"] == ["mail"] and new["routines_to_review"]
    assert c.post("/api/teammates/import", json={"kind": "other"}).status_code == 400
    assert c.get(f"/api/teammates/{t['id']}").json()["routines"]


def test_pages_export_and_share(client):
    c = client
    p = c.post("/api/pages", json={"title": "Weekly report", "body": "# Weekly\n\n| Job | Due |\n|---|---|\n| SS-42 | Fri |\n\n<script>x</script>"}).json()
    h = c.get(f"/api/pages/{p['id']}/export.html").text
    assert "<table>" in h and "SS-42" in h and "<script>" not in h and "Content-Security-Policy" in h
    s = c.post(f"/api/pages/{p['id']}/share?on=true").json()
    assert s["shared"]
    from fastapi.testclient import TestClient

    from app import main

    anon = TestClient(main.app)
    assert anon.get(s["share_path"]).status_code == 200
    c.post(f"/api/pages/{p['id']}/share?on=false")
    assert anon.get(s["share_path"]).status_code == 404
    assert anon.get("/api/pages").status_code == 401


def test_slack_inbound_answers_only_allowed_users(client, monkeypatch):
    from app.teammates import channels

    posted = []

    async def fake_post(channel, text, thread_ts=None):
        posted.append((channel, text))

    monkeypatch.setattr(channels, "slack_post", fake_post)
    client.put("/api/channels/slack", json={"allowed_users": "U123"})
    asyncio.run(channels.handle_slack_event({"type": "message", "channel_type": "im", "user": "U999", "channel": "D1", "text": "hi", "ts": "1.1"}))
    assert "private" in posted[-1][1]
    SCRIPT[:] = [tool_call("send_email", {"to": "x@example.com", "subject": "s", "body": "b"}), {"role": "assistant", "content": "Asked."}]
    asyncio.run(channels.handle_slack_event({"type": "message", "channel_type": "im", "user": "u123", "channel": "D1", "text": "email x", "ts": "1.2"}))
    assert "Nothing is sent until you press Send" in posted[-1][1]
    from app import db

    assert db.one("SELECT outcome FROM actions WHERE tool = 'send_email' ORDER BY id DESC LIMIT 1")["outcome"] in ("held", "drafted")
    n = len(posted)
    asyncio.run(channels.handle_slack_event({"type": "message", "channel_type": "im", "user": "U123", "channel": "D1", "text": "email x", "ts": "1.2"}))
    assert len(posted) == n  # a Slack retry never runs twice
    assert client.put("/api/channels/slack", json={"bot_token": "nope"}).status_code == 400
    st = client.get("/api/channels").json()
    assert st["slack"]["manifest"]["settings"]["socket_mode_enabled"] and st["whatsapp"]["available"] is False


def _pair(client):
    from app import db, mobile

    code = client.post("/api/mobile/pair/start").json()["code"]
    r = client.post("/api/mobile/pair/claim", json={"code": code, "name": "Test phone"})
    assert r.status_code == 200, r.text
    return r.json()["device_token"]


def test_sealed_phone_link_round_trip_and_replay(client):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from app.teammates import phonelink as pl

    tok = _pair(client)
    phone = X25519PrivateKey.generate()
    pub = pl.b64e(phone.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    reg = client.post("/api/mobile/link/keys", json={"phone_pub": pub}, headers={"Authorization": f"Bearer {tok}"}).json()
    key = pl.derive(phone, reg["computer_pub"], reg["mailbox"])
    env = pl.seal(key, reg["mailbox"], "p2c", {"seq": 1, "ts": time.time(), "token": tok, "method": "GET", "path": "/api/mobile/teammates"})
    assert tok not in json.dumps(env)
    out = client.post("/api/mobile/link/sealed", json=env).json()
    res = pl.open_(key, reg["mailbox"], "c2p", out)
    assert res["status"] == 200 and res["body"]["teammates"]
    assert client.post("/api/mobile/link/sealed", json=env).status_code == 409  # replay
    bad = dict(env, c=env["c"][:-4] + "AAAA")
    assert client.post("/api/mobile/link/sealed", json=bad).status_code == 400
    env2 = pl.seal(key, reg["mailbox"], "p2c", {"seq": 2, "ts": time.time(), "token": tok, "method": "GET", "path": "/api/settings"})
    assert pl.open_(key, reg["mailbox"], "c2p", client.post("/api/mobile/link/sealed", json=env2).json())["status"] == 403


def test_phone_files_handoff(client, tmp_path):
    tok = _pair(client)
    h = {"Authorization": f"Bearer {tok}"}
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "a.txt").write_text("hello")
    (tmp_path / ".secret").write_text("x")
    client.put("/api/link", json={"shared_folders": [str(tmp_path)]})
    root = client.get("/api/mobile/files/browse", headers=h).json()
    assert root["folders"][0]["path"] == str(tmp_path)
    inside = client.get("/api/mobile/files/browse", params={"path": str(tmp_path)}, headers=h).json()
    assert [f["name"] for f in inside["folders"]] == ["plans"] and inside["files"] == []
    assert client.get("/api/mobile/files/download", params={"path": str(tmp_path / "plans" / "a.txt")}, headers=h).content == b"hello"
    assert client.get("/api/mobile/files/browse", params={"path": "/etc"}, headers=h).status_code == 403
    assert client.get("/api/mobile/files/download", params={"path": str(tmp_path / "plans" / ".." / ".." )}, headers=h).status_code in (403, 404)
    hid = client.post("/api/mobile/link/handoff", json={"draft": "finish the quote"}, headers=h).json()["id"]
    assert any(x["id"] == hid for x in client.get("/api/link/handoffs").json())
    client.post(f"/api/link/handoffs/{hid}/pick-up")
    assert not any(x["id"] == hid for x in client.get("/api/link/handoffs").json())
    assert client.put("/api/link", json={"relay_url": "http://insecure"}).status_code == 400


def test_teammates_only_send_through_drafts(client, monkeypatch):
    """Even if a teammate calls send_email (hidden from it), nothing is sent: it becomes a draft the user sends."""
    from app import assistant, db

    sent = []

    async def fake_send(*a, **k):
        sent.append(a)
        return {"sent": True}

    monkeypatch.setattr(assistant, "_send_email", fake_send)
    t = mate(client, name="Sender")
    CALLS.clear()
    SCRIPT[:] = [tool_call("send_email", {"to": "boss@example.com", "subject": "Done", "body": "All done"}),
                 {"role": "assistant", "content": "Drafted."}]
    ev = events(client.post(f"/api/teammates/{t['id']}/chat", json={"message": "email my boss"}))
    assert "send_email" not in {x["function"]["name"] for x in CALLS[0]["tools"]}
    assert sent == []
    d = [e for e in ev if e["type"] == "draft"]
    assert d and d[0]["recipients"] == "boss@example.com" and d[0]["subject"] == "Done"
    assert db.one("SELECT outcome FROM actions WHERE tool = 'send_email' ORDER BY id DESC LIMIT 1")["outcome"] == "drafted"
    client.post(f"/api/outbox/{d[0]['id']}/send")
    assert len(sent) == 1


def test_relay_once_registers_with_account_then_answers_sealed(client, monkeypatch):
    """The computer side against a fake of the ai-workspace-cloud relay API (same routes, offline)."""
    import asyncio

    import httpx
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from app.teammates import phonelink as pl

    monkeypatch.setattr(pl, "_account_token", lambda: "acct-session")
    tok = _pair(client)
    phone = X25519PrivateKey.generate()
    pub = pl.b64e(phone.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    reg = client.post("/api/mobile/link/keys", json={"phone_pub": pub}, headers={"Authorization": f"Bearer {tok}"}).json()
    mb, key = reg["mailbox"], pl.derive(phone, reg["computer_pub"], reg["mailbox"])
    boxes: dict = {}
    seen: list = []

    def relay(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        seen.append((req.method, req.url.path, auth))
        if req.url.path == "/relay/v1/mailboxes":
            assert auth == "Bearer acct-session"
            body = json.loads(req.content)
            boxes[body["mailbox"]] = {"token": body["token"], "to-computer": [env] if body["mailbox"] == mb else [], "to-phone": []}
            return httpx.Response(200, json={"ok": True})
        _, _, _, m, d = req.url.path.split("/")
        b = boxes.get(m)
        if not b or auth != f"Bearer {b['token']}":
            return httpx.Response(404)
        if req.method == "GET":
            out, b[d] = b[d], []
            return httpx.Response(200, json={"messages": out})
        b[d].append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    env = pl.seal(key, mb, "p2c", {"seq": 1, "ts": time.time(), "token": tok, "method": "GET", "path": "/api/mobile/teammates"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as c:
            return await pl.relay_once(c, "https://relay.test"), await pl.relay_once(c, "https://relay.test")

    assert asyncio.run(go())[1] == 1  # first round creates the mailboxes, second answers
    res = pl.open_(key, mb, "c2p", boxes[mb]["to-phone"][0])
    assert res["status"] == 200 and res["body"]["teammates"]
    assert all(a != "Bearer acct-session" for (_, p, a) in seen if p != "/relay/v1/mailboxes")


def test_history_survives_reload_and_choices_come_from_a_tool(client):
    c = client
    t = mate(c, name="Planner")
    SCRIPT[:] = [tool_call("offer_choices", {"question": "Which day?", "options": "Monday | Tuesday | Monday | Friday"}),
                 {"role": "assistant", "content": "Pick a day for the site visit."}]
    ev = events(c.post(f"/api/teammates/{t['id']}/chat", json={"message": "book a site visit"}))
    ch = [e for e in ev if e["type"] == "choices"]
    assert ch and ch[0]["options"] == ["Monday", "Tuesday", "Friday"] and ch[0]["question"] == "Which day?"
    cid = next(e for e in ev if e["type"] == "start")["conversation_id"]
    SCRIPT[:] = [{"role": "assistant", "content": "1. Tuesday\n2. Friday\nWhich one?"}]  # a list in text is NOT a choice card
    ev2 = events(c.post(f"/api/teammates/{t['id']}/chat", json={"message": "Monday", "conversation_id": cid}))
    assert not [e for e in ev2 if e["type"] == "choices"]
    # a reload: the pane rebuilds the chat from the server
    assert any(x["id"] == cid for x in c.get(f"/api/teammates/{t['id']}").json()["conversations"])
    h = c.get(f"/api/teammates/{t['id']}/conversations/{cid}").json()
    roles = [(m["role"], m["text"]) for m in h["messages"]]
    assert roles == [("user", "book a site visit"), ("assistant", "Pick a day for the site visit."), ("user", "Monday"),
                     ("assistant", "1. Tuesday\n2. Friday\nWhich one?")]
    assert h["messages"][1]["choices"]["options"] == ["Monday", "Tuesday", "Friday"] and "choices" not in h["messages"][3]
    other = mate(c, name="Planner two")
    assert c.get(f"/api/teammates/{other['id']}/conversations/{cid}").status_code == 404
