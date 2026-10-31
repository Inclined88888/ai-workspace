"""Voice: dictation, hands-free conversation and spoken approvals (feat/v02-anywhere-voice).

Speech to text, best available first (setting ``voice_stt``: auto | local | cloud | browser):
* local  - faster-whisper on this computer, when it is installed (pip install faster-whisper). Nothing leaves the machine.
* cloud  - the Whisper endpoint of a connected Groq (free) or OpenAI key, or the connection named in ``voice_stt_ref``.
* browser - the page's own Web Speech / OS dictation. The backend answers 409 and the page uses it.

Text to speech: the system voices through the page (speechSynthesis) by default; ``voice_tts = cloud`` uses the
``/audio/speech`` endpoint of a connected OpenAI key (or ``voice_tts_ref``).

Spoken approvals never bypass the approval card: :func:`spoken_decision` only turns a clear "yes" into an approval,
and the page shows the same card the click path uses. Anything hedged or unclear is asked again.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import config, db, features_v02
from .security import decrypt, require_user

router = APIRouter(prefix="/api/voice", dependencies=[Depends(require_user)])

MAX_AUDIO = 100 * 1024 * 1024          # one upload (a meeting chunk or a whole imported recording)
CLOUD_LIMIT = 24 * 1024 * 1024          # Groq and OpenAI accept 25 MB per request; longer audio is split with FFmpeg

DEFAULTS = {
    "voice_stt": "auto",
    "voice_tts": "system",
    "voice_language": "",               # "" = detect
    "voice_name": "",                   # system voice name chosen in the page
    "voice_rate": "1",
    "voice_wake_phrase": "hey workspace",
    "voice_local_model": "base",
    "voice_cloud_voice": "alloy",
}


# ---------------------------------------------------------------------------------------------------------------------
# Spoken approvals
# ---------------------------------------------------------------------------------------------------------------------
_NEGATIVE = {"no", "nope", "nah", "don't", "dont", "not", "never", "stop", "cancel", "wait", "hold", "deny", "denied",
             "reject", "abort", "negative", "wrong", "undo"}
_HEDGE = {"maybe", "perhaps", "probably", "possibly", "but", "unless", "later", "depends", "think", "guess", "sure?",
          "what", "why", "how", "which", "who", "when", "hmm", "um", "umm", "uh", "er", "erm", "if", "change", "edit",
          "first", "instead", "actually", "kind", "sort", "might", "should", "could", "would"}
_AFFIRM = {"yes", "yeah", "yep", "yup", "yea", "approve", "approved", "confirm", "confirmed", "affirmative"}
_ACTION_PHRASES = ("send it", "do it", "go ahead", "post it", "pay it", "book it", "delete it", "run it", "submit it",
                   "save it", "go for it", "proceed")
_FILLER = {"please", "ok", "okay", "sure", "fine", "it", "that", "that's", "thats", "now", "the", "this", "email", "message",
           "go", "ahead", "send", "do", "post", "pay", "book", "delete", "run", "submit", "save", "for", "proceed",
           "right", "correct", "absolutely", "definitely", "thanks", "thank", "you", "i", "approve", "is", "good", "looks",
           "and", "on", "one", "number", "request", "yes,"}


def _words(text: str) -> list[str]:
    t = text.lower().replace("’", "'")
    return re.findall(r"[a-z']+|\?", t)


def spoken_decision(text: str) -> str:
    """'approve' only for a clear yes ("yes, send it", "approve", "go ahead"); 'deny' for any no; else 'unclear'.

    Deliberately strict: a negative anywhere wins ("yes... no wait" is a no), hedges and questions are unclear, long
    or rambling answers are unclear, and "okay" or "sure" alone are not a yes.
    """
    words = _words(text)
    if not words:
        return "unclear"
    if any(w in _NEGATIVE for w in words) or re.search(r"\bhold on\b|\bnot yet\b", " ".join(words)):
        return "deny"
    if "?" in words or any(w in _HEDGE for w in words) or len(words) > 8:
        return "unclear"
    joined = " ".join(words)
    affirmed = any(w in _AFFIRM for w in words) or any(re.search(rf"\b{p}\b", joined) for p in _ACTION_PHRASES)
    if not affirmed:
        return "unclear"
    # Every other word must be plain agreement; anything else ("yes to the second one") might change what is approved
    if any(w not in _AFFIRM and w not in _FILLER for w in words):
        return "unclear"
    return "approve"


def approval_prompt(effect: str, tool: str) -> str:
    what = (tool or "do this").replace("_", " ").strip()
    return f"I need your OK to {what}. It's on screen. Say \"yes, send it\" to go ahead, or \"no\" to cancel."


# ---------------------------------------------------------------------------------------------------------------------
# Settings and engines
# ---------------------------------------------------------------------------------------------------------------------
def settings() -> dict:
    return {k.removeprefix("voice_"): db.get_setting(k, v) for k, v in DEFAULTS.items()}


def local_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def _connections() -> list[dict]:
    return db.all("SELECT * FROM connections WHERE enabled = 1 ORDER BY id")


def _by_ref(ref: str) -> tuple[dict, str] | None:
    cid, _, model = (ref or "").partition("/")
    if not cid.isdigit() or not model:
        return None
    c = db.one("SELECT * FROM connections WHERE id = ? AND enabled = 1", int(cid))
    return (c, model) if c else None


def cloud_stt() -> tuple[dict, str] | None:
    """(connection, model) for cloud speech to text, or None. Groq first: its Whisper is free."""
    picked = _by_ref(db.get_setting("voice_stt_ref", "") or "")
    if picked:
        return picked
    conns = _connections()
    for host, model in (("api.groq.com", "whisper-large-v3-turbo"), ("api.openai.com", "gpt-4o-mini-transcribe")):
        for c in conns:
            if host in c["base_url"] and c["secret_enc"]:  # a key for that host (the adapter layer decides how it is sent)
                return c, model
    return None


def cloud_tts() -> tuple[dict, str] | None:
    picked = _by_ref(db.get_setting("voice_tts_ref", "") or "")
    if picked:
        return picked
    for c in _connections():
        if "api.openai.com" in c["base_url"] and c["secret_enc"]:
            return c, "gpt-4o-mini-tts"
    return None


def engines() -> dict:
    stt, tts = cloud_stt(), cloud_tts()
    return {
        "local": local_available(),
        "cloud": {"name": stt[0]["name"], "model": stt[1]} if stt else None,
        "cloud_voice": {"name": tts[0]["name"], "model": tts[1]} if tts else None,
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


def _pick(prefer: str = "") -> str:
    """Which engine transcribes now: 'local', 'cloud' or 'browser' (the page does it)."""
    mode = prefer or settings()["stt"]
    if mode == "browser":
        return "browser"
    if mode in ("auto", "local") and local_available():
        return "local"
    if mode == "local":  # asked for on-device only: never fall back to the cloud
        return "browser"
    return "cloud" if cloud_stt() else "browser"


_whisper = None


def _local_transcribe(path: Path, language: str) -> dict:
    global _whisper
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    if _whisper is None:
        root = config.DATA_DIR / "whisper"
        root.mkdir(parents=True, exist_ok=True)
        _whisper = WhisperModel(settings()["local_model"] or "base", device="cpu", compute_type="int8", download_root=str(root))
    segs, info = _whisper.transcribe(str(path), language=language or None, vad_filter=True)
    out = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs]
    return {"text": " ".join(s["text"] for s in out).strip(), "segments": out, "language": info.language, "engine": "local"}


async def _cloud_once(conn: dict, model: str, data: bytes, filename: str, language: str) -> dict:
    form = {"model": model, "response_format": "verbose_json" if "whisper" in model else "json"}
    if language:
        form["language"] = language
    key = decrypt(conn["secret_enc"])
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(f"{conn['base_url'].rstrip('/')}/audio/transcriptions", data=form,
                         files={"file": (filename or "audio.webm", data)}, headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message") or r.text
        except ValueError:
            msg = r.text
        raise HTTPException(502, f"{conn['name']} could not transcribe the audio: {str(msg)[:300]}")
    j = r.json()
    segs = [{"start": round(float(s.get("start", 0)), 2), "end": round(float(s.get("end", 0)), 2), "text": str(s.get("text", "")).strip()}
            for s in j.get("segments") or []]
    return {"text": str(j.get("text", "")).strip(), "segments": segs, "language": j.get("language", language), "engine": "cloud"}


def _split(path: Path, minutes: int = 10) -> list[Path]:
    """Long recordings in ~10 minute mono 64 kbps MP3 pieces (each well under the 25 MB cloud limit)."""
    out = path.parent / "parts"
    out.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-ac", "1", "-b:a", "64k",
                    "-f", "segment", "-segment_time", str(minutes * 60), str(out / "part%03d.mp3")], check=True, timeout=1800)
    return sorted(out.glob("part*.mp3"))


async def transcribe(data: bytes, filename: str = "audio.webm", language: str = "", prefer: str = "") -> dict:
    """Text (and timed segments when the engine gives them) for one piece of audio."""
    if not data:
        raise HTTPException(400, "No audio received")
    if len(data) > MAX_AUDIO:
        raise HTTPException(413, "That recording is over 100 MB. Split it or import the meeting's transcript file instead.")
    engine = _pick(prefer)
    if engine == "browser":
        raise HTTPException(409, "No speech engine here yet. The app uses your computer's own dictation instead. "
                                 "For better results add a free Groq key in Models, or install offline speech.")
    language = language or settings()["language"]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / (Path(filename).name or "audio.webm")
        p.write_bytes(data)
        if engine == "local":
            import asyncio

            return await asyncio.to_thread(_local_transcribe, p, language)
        conn, model = cloud_stt()  # type: ignore[misc]
        if len(data) <= CLOUD_LIMIT:
            return await _cloud_once(conn, model, data, p.name, language)
        if not shutil.which("ffmpeg"):
            raise HTTPException(413, "This recording is too long to send in one go. Install FFmpeg (Files page -> Install) "
                                     "and try again, or import the meeting's transcript file.")
        texts, segs, offset = [], [], 0.0
        for part in _split(p):
            r = await _cloud_once(conn, model, part.read_bytes(), part.name, language)
            texts.append(r["text"])
            segs += [{**s, "start": s["start"] + offset, "end": s["end"] + offset} for s in r["segments"]]
            offset = segs[-1]["end"] if segs else offset + 600
        return {"text": " ".join(texts).strip(), "segments": segs, "language": language, "engine": "cloud"}


async def speak_cloud(text: str) -> bytes:
    picked = cloud_tts()
    if not picked:
        raise HTTPException(409, "No cloud voice connected. Using your computer's own voice.")
    conn, model = picked
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{conn['base_url'].rstrip('/')}/audio/speech",
                         json={"model": model, "voice": settings()["cloud_voice"] or "alloy", "input": text[:4000], "response_format": "mp3"},
                         headers={"Authorization": f"Bearer {decrypt(conn['secret_enc'])}"})
    if r.status_code >= 400:
        raise HTTPException(502, f"{conn['name']} could not speak that ({r.status_code}). Using your computer's own voice.")
    return r.content


# ---------------------------------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------------------------------
@router.get("/status")
def status():
    e = engines()
    s = settings()
    engine = _pick()
    return {
        "settings": s,
        "engines": e,
        "engine": engine,
        # Wake word listens all the time, so it only runs on this computer (offline Whisper or the OS's own dictation)
        "wake_ok": engine != "cloud" or e["local"],
        "features": {f: features_v02.describe(f) for f in ("voice_dictation", "voice_conversation")},
    }


class SettingsIn(BaseModel):
    stt: str | None = Field(None, pattern="^(auto|local|cloud|browser)$")
    tts: str | None = Field(None, pattern="^(system|cloud)$")
    language: str | None = Field(None, max_length=10)
    name: str | None = Field(None, max_length=200)
    rate: str | None = Field(None, pattern=r"^(0\.[5-9]|1(\.\d)?|1\.5|2)$")
    wake_phrase: str | None = Field(None, max_length=60)
    local_model: str | None = Field(None, pattern="^(tiny|base|small|medium|large-v3|turbo)$")
    cloud_voice: str | None = Field(None, max_length=40)
    stt_ref: str | None = Field(None, max_length=200)
    tts_ref: str | None = Field(None, max_length=200)


@router.put("/settings")
def save_settings(data: SettingsIn):
    for k, v in data.model_dump(exclude_none=True).items():
        db.set_setting(f"voice_{k}", v.strip().lower() if k == "wake_phrase" else v)
    return status()


@router.post("/transcribe")
async def transcribe_route(request: Request, lang: str = "", engine: str = "", name: str = "audio.webm"):
    """Raw audio in the body (webm, ogg, wav, mp3, m4a). Free: dictation."""
    data = await request.body()
    return await transcribe(data, name, lang, engine if engine in ("local", "cloud") else "")


class SpeakIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.post("/speak")
async def speak(data: SpeakIn):
    return Response(await speak_cloud(data.text), media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


class DecisionIn(BaseModel):
    text: str = Field("", max_length=1000)


@router.post("/decision")
def decision(data: DecisionIn):
    return {"decision": spoken_decision(data.text)}


class SpokenApprovalIn(BaseModel):
    utterance: str = Field(..., max_length=1000)


async def answer_by_voice(aid: int, utterance: str, channel: str) -> dict:
    """Re-checks the words here (the page's reading is never trusted), then answers like the card's buttons do."""
    from . import actions, assistant

    if not features_v02.allowed("voice_conversation"):
        raise HTTPException(402, "Spoken approvals are part of Pro. Tap Approve on the card instead.")
    a = actions.approval(aid)
    d = spoken_decision(utterance)
    if d == "unclear":
        return {"decision": "unclear", "status": a["status"], "heard": utterance,
                "say": "Sorry, I need a clear yes or no. " + approval_prompt(a.get("effect", ""), a.get("tool", ""))}
    res = await assistant.decide(aid, d == "approve", channel=channel)
    return {"decision": d, "heard": utterance, **({"status": res.get("status"), "result": res.get("result")} if isinstance(res, dict) else {})}


@router.post("/approvals/{aid}")
async def approve_by_voice(aid: int, data: SpokenApprovalIn):
    return await answer_by_voice(aid, data.utterance, "web")


@router.get("/approval-prompt/{aid}")
def prompt_for(aid: int):
    from . import actions

    a = actions.approval(aid)
    return {"say": approval_prompt(a.get("effect", ""), a.get("tool", "")), "status": a["status"]}


# Phone app (mobile/): the same spoken approval, with the paired device's token
def _device_router() -> APIRouter:
    from .mobile import require_device

    r = APIRouter(prefix="/api/mobile/voice")

    @r.post("/approvals/{aid}")
    async def device_approve(aid: int, data: SpokenApprovalIn, _: dict = Depends(require_device)):
        return await answer_by_voice(aid, data.utterance, "mobile")

    @r.post("/transcribe")
    async def device_transcribe(request: Request, lang: str = "", name: str = "audio.m4a", _: dict = Depends(require_device)):
        return await transcribe(await request.body(), name, lang)

    @r.get("/status")
    def device_status(_: dict = Depends(require_device)):
        return {"engine": _pick(), "features": {f: features_v02.describe(f) for f in ("voice_dictation", "voice_conversation")}}

    @r.post("/decision")
    def device_decision(data: DecisionIn, _: dict = Depends(require_device)):
        return {"decision": spoken_decision(data.text)}

    return r


device = _device_router()


def dumps(o) -> str:  # small helper for tests and logs
    return json.dumps(o, ensure_ascii=False)
