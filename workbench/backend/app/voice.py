"""Voice conversations with ROOKS: speech in, speech out, on top of the ordinary assistant (same tools, projects,
approvals and memory).

Speech recognition and speech, each one of:
  * browser (the default, nothing to install): the browser's own speech recognition and voices. Note that some
    browsers send microphone audio to their maker's speech service to recognise it (Chrome sends it to Google);
    ROOKS says so before the first session.
  * provider: an OpenAI compatible endpoint you connected (POST /audio/transcriptions and /audio/speech), local (for
    example a Whisper or Kokoro server on your network) or cloud (OpenAI and others).
A cloud provider needs your consent naming where audio goes; "local only" refuses cloud speech providers, and so does
a local-only project. Audio is passed straight through and never stored or logged. Transcripts are the
conversation's messages: you choose at the end of a session whether to keep them.
Turn-taking is half duplex: ROOKS listens, then answers; speaking while it talks interrupts it (barge-in). This is not
full duplex streaming voice.
"""

import json

import httpx
from fastapi import HTTPException

from . import audit, db, llm

MAX_AUDIO = 10 * 1024 * 1024
SPOKEN_STYLE = ("This is a voice conversation: your reply will be spoken aloud. Answer in short plain sentences, no "
                "markdown, lists, links or code. Ask one question at a time when you need something. If an action needs "
                "approval, say so plainly.")


def settings() -> dict:
    try:
        s = json.loads(db.get_setting("voice", "") or "{}")
    except ValueError:
        s = {}
    out = {"stt": s.get("stt", "browser"), "stt_model": s.get("stt_model", ""), "tts": s.get("tts", "browser"),
           "tts_model": s.get("tts_model", ""), "tts_voice": s.get("tts_voice", ""), "local_only": bool(s.get("local_only")),
           "consent": s.get("consent", {}), "browser_notice_seen": bool(s.get("browser_notice_seen"))}
    out["stt_destination"] = _destination(out["stt_model"]) if out["stt"] == "provider" else {}
    out["tts_destination"] = _destination(out["tts_model"]) if out["tts"] == "provider" else {}
    return out


def _destination(ref: str) -> dict:
    if not ref:
        return {}
    from .vision import destination

    return destination(ref)


def configure(changes: dict) -> dict:
    cur = settings()
    s = {k: cur[k] for k in ("stt", "stt_model", "tts", "tts_model", "tts_voice", "local_only", "consent", "browser_notice_seen")}
    for k, v in changes.items():
        if v is None or k not in s:
            continue
        if k in ("stt", "tts") and v not in ("browser", "provider"):
            raise HTTPException(400, f"{k} is browser or provider.")
        if k in ("stt_model", "tts_model") and v:
            llm.split_ref(v)
            s["consent"] = {kk: vv for kk, vv in s["consent"].items() if kk != k}  # a new destination needs new consent
        s[k] = v
    if changes.get("consent_for"):
        ref = s.get(changes["consent_for"], "")
        if ref:
            s["consent"][changes["consent_for"]] = {"ref": ref, "destination": _destination(ref).get("label", ""), "at": db.now()}
    db.set_setting("voice", json.dumps(s))
    audit.record("user", "voice settings", "", f"listening: {s['stt']}, speaking: {s['tts']}")
    return settings()


def _check(kind: str, project_id: int | None) -> tuple[dict, str]:
    """(provider, model) for this kind, after the privacy checks."""
    from . import projects

    s = settings()
    key = f"{kind}_model"
    if s[kind] != "provider" or not s[key]:
        raise HTTPException(409, f"Voice {'recognition' if kind == 'stt' else 'speech'} uses the browser; no provider is set.")
    dest = _destination(s[key])
    if not dest.get("local"):
        if s["local_only"]:
            raise HTTPException(403, f"Voice is set to local providers only, and {dest.get('label')}.")
        if projects.local_only(project_id):
            raise HTTPException(403, f"This project is local only, and {dest.get('label')}. Use the browser or a local voice provider.")
        if s["consent"].get(key, {}).get("ref") != s[key]:
            raise HTTPException(403, f"Audio would go to {dest.get('label')}. Agree to that in Settings, Voice, first.")
    return llm.split_ref(s[key])


async def transcribe(audio: bytes, mime: str, project_id: int | None = None) -> str:
    if not audio:
        raise HTTPException(400, "No audio.")
    if len(audio) > MAX_AUDIO:
        raise HTTPException(413, "That recording is too long.")
    p, model = _check("stt", project_id)
    headers = {k: v for k, v in llm._headers(p).items() if k.lower() != "content-type"}
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a"}.get(mime.split(";")[0], "webm")
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{p['base_url'].rstrip('/')}/audio/transcriptions", headers=headers,
                             files={"file": (f"speech.{ext}", audio, mime.split(";")[0] or "audio/webm")}, data={"model": model})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{p['name']} could not be reached for speech recognition ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise HTTPException(502, f"{p['name']} did not recognise the audio ({r.status_code}): {r.text[:200]}")
    try:
        return str(r.json().get("text", "")).strip()
    except ValueError:
        return r.text.strip()


async def speak(text: str, project_id: int | None = None) -> tuple[bytes, str]:
    p, model = _check("tts", project_id)
    s = settings()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{p['base_url'].rstrip('/')}/audio/speech", headers=llm._headers(p),
                             json={"model": model, "input": text[:4000], "voice": s["tts_voice"] or "alloy", "response_format": "mp3"})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{p['name']} could not be reached for speech ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise HTTPException(502, f"{p['name']} could not speak that ({r.status_code}): {r.text[:200]}")
    return r.content, r.headers.get("content-type", "audio/mpeg")


def end_session(cid: int, keep: bool) -> dict:
    """keep false: the transcript (this conversation's messages) is deleted. What was done in it, such as tasks
    created, stays."""
    c = db.one("SELECT id FROM conversations WHERE id = ?", cid)
    if not c:
        raise HTTPException(404, "Conversation not found")
    if not keep:
        db.run("DELETE FROM messages WHERE conversation_id = ?", cid)
        db.run("DELETE FROM conversations WHERE id = ?", cid)
    audit.record("user", "voice session ended", f"#{cid}", "transcript kept" if keep else "transcript deleted")
    return {"kept": keep}
