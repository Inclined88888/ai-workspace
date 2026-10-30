"""Background team checks (feat/team-lead): every 10 minutes pull member updates, look for late/stuck work, digest."""

import asyncio
import logging

log = logging.getLogger("team.loop")


async def loop() -> None:
    from . import connect, engine, store

    while True:
        await asyncio.sleep(600)
        try:
            store.ensure()
            if not store.members():
                continue
            await asyncio.to_thread(connect.pull_updates)
            await asyncio.to_thread(engine.check)
            await asyncio.to_thread(engine.maybe_digest)
        except Exception as e:  # noqa: BLE001 - the loop never dies
            log.warning("team check failed: %s", e.__class__.__name__)
