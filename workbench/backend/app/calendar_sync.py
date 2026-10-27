"""Calendar sync: ICS subscriptions (read only) and CalDAV (read and create)."""

import uuid
from datetime import date, datetime, timedelta, timezone

import caldav
import httpx
import icalendar
import recurring_ical_events
from fastapi import HTTPException

from . import db
from .security import decrypt

WINDOW_PAST = timedelta(days=60)
WINDOW_FUTURE = timedelta(days=365)


def _iso(value) -> tuple[str, bool]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds"), False
    if isinstance(value, date):
        return value.isoformat(), True
    raise ValueError(f"Unsupported date value {value!r}")


def _rows(components) -> list[dict]:
    rows = []
    for ev in components:
        start = ev.get("DTSTART")
        if start is None:
            continue
        s, all_day = _iso(start.dt)
        end = ev.get("DTEND")
        if end is not None:
            e, _ = _iso(end.dt)
        elif ev.get("DURATION") is not None:
            e, _ = _iso(start.dt + ev["DURATION"].dt)
        else:
            e, _ = _iso(start.dt + (timedelta(days=1) if all_day else timedelta(hours=1)))
        uid = f'{ev.get("UID", uuid.uuid4())}|{s}'
        rows.append({
            "uid": uid,
            "title": str(ev.get("SUMMARY", "")),
            "start": s,
            "end": e,
            "all_day": int(all_day),
            "location": str(ev.get("LOCATION", "")),
            "description": str(ev.get("DESCRIPTION", ""))[:5000],
        })
    return rows


def _expand(cal: icalendar.Calendar) -> list:
    now = datetime.now(timezone.utc)
    return list(recurring_ical_events.of(cal).between(now - WINDOW_PAST, now + WINDOW_FUTURE))


def _dav_calendar(c: dict) -> caldav.Calendar:
    client = caldav.DAVClient(url=c["url"], username=c["username"] or None, password=decrypt(c["password_enc"]) or None, timeout=30)
    return client.calendar(url=c["url"])


def _fetch(c: dict) -> list[dict]:
    if c["kind"] == "ics":
        url = c["url"].replace("webcal://", "https://", 1)
        if not url.startswith(("http://", "https://")):
            raise ValueError("ICS URL must start with http(s):// or webcal://")
        r = httpx.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return _rows(_expand(icalendar.Calendar.from_ical(r.content)))
    if c["kind"] == "caldav":
        now = datetime.now(timezone.utc)
        comps = []
        for obj in _dav_calendar(c).search(start=now - WINDOW_PAST, end=now + WINDOW_FUTURE, event=True, expand=True):
            comps.extend(sc for sc in obj.icalendar_instance.walk("VEVENT"))
        return _rows(comps)
    return []


def sync(cid: int) -> dict:
    c = db.one("SELECT * FROM calendars WHERE id = ?", cid)
    if not c:
        raise HTTPException(404, "Calendar not found")
    if c["kind"] == "local":
        return {"synced": 0}
    try:
        rows = _fetch(c)
    except Exception as e:  # noqa: BLE001 - any remote failure is recorded on the calendar
        db.run("UPDATE calendars SET last_error = ? WHERE id = ?", str(e)[:500], cid)
        raise HTTPException(502, f"Sync failed: {e}") from e
    with db.tx() as t:
        t.execute("DELETE FROM events WHERE calendar_id = ?", (cid,))
        t.executemany(
            "INSERT OR REPLACE INTO events(calendar_id, uid, title, start, end, all_day, location, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(cid, r["uid"], r["title"], r["start"], r["end"], r["all_day"], r["location"], r["description"]) for r in rows],
        )
        t.execute("UPDATE calendars SET last_synced = ?, last_error = NULL WHERE id = ?", (db.now(), cid))
    return {"synced": len(rows)}


def sync_all() -> None:
    for c in db.all("SELECT id FROM calendars WHERE enabled = 1 AND kind != 'local'"):
        try:
            sync(c["id"])
        except HTTPException:
            pass


def discover(url: str, username: str, password: str) -> list[dict]:
    try:
        client = caldav.DAVClient(url=url, username=username or None, password=password or None, timeout=30)
        return [{"name": cal.name or str(cal.url), "url": str(cal.url)} for cal in client.principal().calendars()]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"CalDAV discovery failed: {e}") from e


def create_remote(c: dict, title: str, start: str, end: str, all_day: bool, location: str, description: str) -> str:
    uid = f"{uuid.uuid4()}@ai-workspace"
    ev = icalendar.Event()
    ev.add("uid", uid)
    ev.add("summary", title)
    ev.add("dtstamp", datetime.now(timezone.utc))
    if all_day:
        ev.add("dtstart", date.fromisoformat(start[:10]))
        ev.add("dtend", date.fromisoformat(end[:10]))
    else:
        ev.add("dtstart", datetime.fromisoformat(start))
        ev.add("dtend", datetime.fromisoformat(end))
    if location:
        ev.add("location", location)
    if description:
        ev.add("description", description)
    cal = icalendar.Calendar()
    cal.add("prodid", "-//ROOKS//Workbench//EN")
    cal.add("version", "2.0")
    cal.add_component(ev)
    try:
        _dav_calendar(c).save_event(cal.to_ical().decode())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"CalDAV create failed: {e}") from e
    return uid
