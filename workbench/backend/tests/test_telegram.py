import asyncio

from conftest import SCRIPT


def test_telegram_only_answers_allowlisted_users(client, monkeypatch):
    from app import db, telegram

    client.put("/api/assistant/settings", json={"telegram_token": "123:abc", "telegram_allowed_users": "555, 777"})
    assert telegram.allowed() == {555, 777}
    assert db.get_setting("telegram_home_chat") == "555"
    assert db.get_setting("secret:TELEGRAM_BOT_TOKEN") != "123:abc"  # stored encrypted
    assert telegram.token() == "123:abc"

    sent: list[tuple[int, str]] = []

    async def fake_api(client_, method, **params):
        if method == "sendMessage":
            sent.append((params["chat_id"], params["text"]))
        return {}

    monkeypatch.setattr(telegram, "_api", fake_api)
    asyncio.run(telegram._handle(None, {"chat": {"id": 999}, "from": {"id": 999}, "text": "hi"}))
    assert "private" in sent[-1][1] and "999" in sent[-1][1]

    SCRIPT[:] = [{"role": "assistant", "content": "Hello from Telegram"}]
    asyncio.run(telegram._handle(None, {"chat": {"id": 555}, "from": {"id": 555}, "text": "hello"}))
    assert sent[-1] == (555, "Hello from Telegram")
    conv = db.one("SELECT * FROM conversations WHERE channel = 'telegram' AND external_id = '555'")
    assert conv  # same assistant, stored like any other conversation

    asyncio.run(telegram._handle(None, {"chat": {"id": 555}, "from": {"id": 555}, "text": "/new"}))
    assert db.one("SELECT COUNT(*) AS n FROM conversations WHERE channel = 'telegram'")["n"] == 2
    client.put("/api/assistant/settings", json={"telegram_token": ""})
    assert telegram.token() == ""
