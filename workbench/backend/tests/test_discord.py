"""Discord channel: DMs and @mentions reach the chosen teammate; strangers, bots and replays don't (Phase 2)."""

import asyncio
import json

from conftest import SCRIPT, tool_call

TOKEN = "MTIz.NDU2.Nzg5abc"  # shape only: three dot-separated parts
BOT = "1000"
ME = "4242"


def _msg(mid, content, author=ME, guild=None, mentions=(), bot=False):
    m = {"id": mid, "channel_id": "C1", "content": content, "type": 0, "author": {"id": author, "bot": bot},
         "mentions": [{"id": x} for x in mentions]}
    if guild:
        m["guild_id"] = guild
    return m


def _capture(monkeypatch):
    from app.teammates import channels

    posted = []

    async def fake_post(channel_id, text, reply_to=None):
        posted.append((channel_id, text, reply_to))

    monkeypatch.setattr(channels, "discord_post", fake_post)
    return channels, posted


def test_discord_answers_allowed_dms_and_mentions_only(client, monkeypatch):
    channels, posted = _capture(monkeypatch)
    assert client.put("/api/channels/discord", json={"bot_token": TOKEN, "allowed_users": ME}).status_code == 200

    asyncio.run(channels.handle_discord_message(_msg("1", "hi", author="999"), BOT))
    assert "private" in posted[-1][1] and "999" in posted[-1][1]

    n = len(posted)
    asyncio.run(channels.handle_discord_message(_msg("2", "hi", bot=True), BOT))
    asyncio.run(channels.handle_discord_message(_msg("3", "no mention here", guild="G1"), BOT))
    assert len(posted) == n  # other bots and unmentioned server chatter are ignored

    SCRIPT[:] = [{"role": "assistant", "content": "Hello from your teammate"}]
    assert asyncio.run(channels.handle_discord_message(_msg("4", f"<@{BOT}> hello", guild="G1", mentions=[BOT]), BOT)) == "Hello from your teammate"
    assert posted[-1] == ("C1", "Hello from your teammate", "4")  # in a server it replies to the message

    from app import db

    conv = db.one("SELECT * FROM conversations WHERE channel = 'discord' ORDER BY id DESC LIMIT 1")
    assert conv["external_id"] == "discord:C1"
    assert db.one("SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
                  conv["id"])["content"] == "hello"  # the mention is stripped

    n = len(posted)
    asyncio.run(channels.handle_discord_message(_msg("4", f"<@{BOT}> hello", guild="G1", mentions=[BOT]), BOT))
    assert len(posted) == n  # a replayed message never runs twice


def test_discord_turn_cannot_send_anything_without_approval(client, monkeypatch):
    channels, posted = _capture(monkeypatch)
    client.put("/api/channels/discord", json={"allowed_users": ME})
    SCRIPT[:] = [tool_call("send_email", {"to": "x@example.com", "subject": "s", "body": "b"}), {"role": "assistant", "content": "Asked."}]
    asyncio.run(channels.handle_discord_message(_msg("10", "email x"), BOT))
    assert "Nothing is sent until you press Send" in posted[-1][1] or "Waiting for your OK" in posted[-1][1]
    from app import db

    assert db.one("SELECT outcome FROM actions WHERE tool = 'send_email' ORDER BY id DESC LIMIT 1")["outcome"] in ("held", "drafted")


def test_discord_settings_validate_and_never_echo_the_token(client):
    assert client.put("/api/channels/discord", json={"bot_token": "not a token"}).status_code == 400
    assert client.put("/api/channels/discord", json={"allowed_users": "alice"}).status_code == 400
    r = client.put("/api/channels/discord", json={"bot_token": TOKEN, "allowed_users": f" {ME} , 77 "})
    assert r.status_code == 200 and r.json()["allowed_users"] == f"{ME},77"
    st = client.get("/api/channels").json()
    assert st["discord"]["configured"] and st["discord"]["steps"] and TOKEN not in json.dumps(st)
    from app import db

    assert TOKEN not in (db.get_setting("secret:DISCORD_BOT_TOKEN") or "")  # stored encrypted


def test_discord_gateway_identifies_heartbeats_and_answers(client, monkeypatch):
    import websockets

    channels, posted = _capture(monkeypatch)
    client.put("/api/channels/discord", json={"bot_token": TOKEN, "allowed_users": ME})
    seen = []

    async def gateway(ws):
        await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 50}}))
        identify = json.loads(await ws.recv())
        seen.append(identify)
        await ws.send(json.dumps({"op": 0, "s": 1, "t": "READY", "d": {"user": {"id": BOT, "username": "aiw"}, "application": {"id": "555"}}}))
        await ws.send(json.dumps({"op": 0, "s": 2, "t": "MESSAGE_CREATE", "d": _msg("20", "ping")}))
        try:
            while True:
                seen.append(json.loads(await asyncio.wait_for(ws.recv(), 2)))
        except Exception:
            pass

    async def run():
        async with websockets.serve(gateway, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            async def url(_tok):
                return f"ws://127.0.0.1:{port}"

            monkeypatch.setattr(channels, "_gateway_url", url)
            SCRIPT[:] = [{"role": "assistant", "content": "pong"}]
            task = asyncio.create_task(channels.discord_loop())
            for _ in range(100):
                if posted and any(x.get("op") == 1 for x in seen[1:]):
                    break
                await asyncio.sleep(0.05)
            task.cancel()

    asyncio.run(run())
    assert seen[0]["op"] == 2 and seen[0]["d"]["token"] == TOKEN and seen[0]["d"]["intents"] == channels.DISCORD_INTENTS
    assert any(x.get("op") == 1 for x in seen[1:])  # heartbeats
    assert posted[-1][0] == "C1" and posted[-1][1].startswith("pong") and posted[-1][2] is None  # DM: no reply reference
    st = client.get("/api/channels").json()["discord"]
    assert st["bot"] == "aiw" and "client_id=555" in st["invite_url"]
