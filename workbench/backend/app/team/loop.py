"""Background team checks (feat/team-lead): every 10 minutes pull member updates, look for late/stuck work, digest."""

import asyncio
import logging

log = logging.getLogger("team.loop")


def poll_replies() -> dict:
    """Member replies from the lead's inbox and texts (only when the lead switched reading on)."""
    from . import replies, sms, store

    out = {}
    for name, on, fn in (("email", store.setting("read_inbox") == "1", replies.poll_inbox), ("sms", sms.configured(), sms.poll)):
        if on:
            try:
                out[name] = fn()
            except Exception as e:  # noqa: BLE001 - mailbox offline etc.
                log.warning("team %s poll failed: %s", name, e.__class__.__name__)
    return out


async def loop() -> None:
    from . import connect, engine, store

    while True:
        await asyncio.sleep(600)
        try:
            store.ensure()
            if not store.members():
                continue
            await asyncio.to_thread(connect.pull_updates)
            await asyncio.to_thread(poll_replies)
            await asyncio.to_thread(engine.check)
            await asyncio.to_thread(engine.maybe_digest)
        except Exception as e:  # noqa: BLE001 - the loop never dies
            log.warning("team check failed: %s", e.__class__.__name__)
