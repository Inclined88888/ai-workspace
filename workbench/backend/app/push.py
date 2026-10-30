"""Push notifications to paired phones through Expo's push service. Off until the owner turns it on.

Two events notify: an action is waiting for approval, and a run finished while nobody watched it (schedules, company
agents, chats started from the phone). Nothing is sent unless all of these hold:
  * the setting `mobile_push_enabled` is "1" (Settings -> Phone, or PUT /api/mobile/settings),
  * a paired device registered an Expo push token (the app does this after the user allows notifications),
  * the phone app was built with push credentials (Expo project id; an APNs key for iOS, FCM for Android).
EXPO_ACCESS_TOKEN is optional (only needed when "enhanced push security" is on in the Expo project).

A notification carries no secrets and no message content: a title, a short line, and ids the app opens.
Delivery goes through httpx, so every send appears in the egress audit log (it leaves the local network).
"""

import asyncio
import logging
import os

import httpx

from . import db

log = logging.getLogger("push")
EXPO_URL = os.getenv("EXPO_PUSH_URL", "https://exp.host/--/api/v2/push/send")
_tasks: set[asyncio.Task] = set()


def enabled() -> bool:
    return db.get_setting("mobile_push_enabled") == "1"


def tokens() -> list[str]:
    return [r["push_token"] for r in db.all("SELECT push_token FROM devices WHERE push_token != ''")]


def messages(title: str, body: str, data: dict) -> list[dict]:
    """The Expo push payloads for every registered phone, or none when push is off."""
    if not enabled():
        return []
    return [{"to": t, "title": title[:80], "body": body[:160], "data": data, "sound": "default", "priority": "high"}
            for t in tokens()]


async def _send(batch: list[dict]) -> None:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if os.getenv("EXPO_ACCESS_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['EXPO_ACCESS_TOKEN']}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(EXPO_URL, json=batch, headers=headers)
            if r.status_code >= 400:
                log.warning("Expo push refused: HTTP %s", r.status_code)
    except httpx.HTTPError as e:  # a phone notification must never break the run that triggered it
        log.warning("Expo push failed: %s", e.__class__.__name__)


def notify(title: str, body: str, **data) -> int:
    """Queue a notification to every phone. Returns how many were queued (0 when off). Never raises."""
    try:
        batch = messages(title, body, data)
        if not batch:
            return 0
        loop = asyncio.get_running_loop()
        task = loop.create_task(_send(batch))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return len(batch)
    except Exception as e:  # noqa: BLE001 - no running loop, database busy: skip the notification, keep the run
        log.warning("push skipped: %s", e.__class__.__name__)
        return 0


def approval_needed(action_id: int, tool: str) -> int:
    return notify("Approval needed", f"{tool.replace('_', ' ')} is waiting for you", kind="approval", id=action_id)


def run_finished(turn_id: int | None, outcome: str, conversation_id: int | None, channel: str) -> int:
    if channel not in ("mobile", "schedule", "agent"):
        return 0  # the person is at the desk watching it
    label = "Run finished" if outcome in ("backed", "no_claim", "fixed") else "Run needs a look"
    return notify(label, f"{outcome.replace('_', ' ')} · {channel}", kind="run", id=turn_id, conversation_id=conversation_id)
