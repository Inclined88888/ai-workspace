"""Fast polling responses must not starve other requests on the app event loop."""

import asyncio
import threading


def test_empty_fast_poll_yields_to_other_requests(client, monkeypatch):
    from app import telegram

    monkeypatch.setattr(telegram, "token", lambda: "123:abc" if threading.current_thread() is threading.main_thread() else "")
    monkeypatch.setattr(telegram, "_status", {})
    calls = 0
    heartbeat = False
    heartbeat_when_stopped = None

    async def fake_api(_client, method, **_params):
        nonlocal calls, heartbeat_when_stopped
        if method == "getMe":
            asyncio.get_running_loop().call_soon(mark_heartbeat)
            return {"username": "test"}
        assert method == "getUpdates"
        calls += 1
        if calls == 100:
            heartbeat_when_stopped = heartbeat
            raise asyncio.CancelledError
        return []

    monkeypatch.setattr(telegram, "_api", fake_api)

    async def run():
        nonlocal heartbeat
        try:
            await telegram.loop()
        except asyncio.CancelledError:
            pass

    def mark_heartbeat():
        nonlocal heartbeat
        heartbeat = True

    asyncio.run(run())
    assert heartbeat_when_stopped, "a fast Telegram poll monopolized the event loop"
