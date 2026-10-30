"""The smart router: one model choice ("auto") that works on whatever the user connected, cloud first.

* Per task: a fast model for small jobs (summaries, titles, memory extraction) and a strong one for agentic work
  (chat with tools, research, the browser, Rooks Code). "auto:fast" and "auto:strong" force a tier.
* Fallback: a 429, a used-up daily quota, a 5xx, a timeout or a rejected key moves the same request to the next
  connection, in the user's order. Nothing is retried on the same provider while it is cooling down.
* Daily-limit aware: counts answered requests per provider day (Gemini resets at midnight Pacific, others at 00:00
  UTC), reads the limit headers providers send (Groq: requests per day; OpenAI, Anthropic, OpenRouter: per minute),
  and parks a provider until its reset after a daily-limit 429.
* Every answer says which provider and model answered, what was skipped and why, and what is left where known.
* Vision aware: a request with screenshots (Rooks Desk, Rooks Browser) goes to a model that can see. Free ones: Gemini
  Flash, Groq's Llama 4 and Qwen 3.8 vision models, OpenRouter's free Gemma 3 / Llama 4 / Qwen-VL. When no connected
  model can see, the images are taken out and the model is told so, and callers fall back to the accessibility text.

Model refs stay "<connection_id>/<model>" everywhere else; "auto" is resolved here, call by call.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException

from . import db
from .adapters import CAPTURE, ProviderError

AUTO = {"auto": None, "auto:fast": "fast", "auto:strong": "strong"}
AUTO_LABELS = {"auto": "Auto (picks per task)", "auto:fast": "Auto · fast", "auto:strong": "Auto · strong"}
STRONG_CHANNELS = {"research", "browser", "rooks-code", "rooks-browser", "rooks-desk", "agent", "company"}
LONG_PROMPT_CHARS = 12_000

# What the router knows about each provider. Model lists are preferences matched as exact names or prefixes against what
# the provider actually lists for the user; anything missing falls through to the next entry, then to a name heuristic.
PROFILES: list[tuple[str, dict]] = [
    ("generativelanguage.googleapis.com", {
        "label": "Google Gemini", "free": True, "reset_tz": "America/Los_Angeles",
        "note": "Google doesn't report what's left. Free limits are per project, per model; see AI Studio.",
        "fast": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-2.0-flash-lite"],
        "strong": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.0-flash"],
        "vision": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.0-flash"]}),
    ("api.groq.com", {
        "label": "Groq", "free": True, "reset_tz": "UTC", "header_window": "day",
        "fast": ["openai/gpt-oss-20b", "llama-3.1-8b-instant"],
        "strong": ["openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct", "llama-3.3-70b-versatile"],
        "vision": ["qwen/qwen3.8", "meta-llama/llama-4-maverick", "meta-llama/llama-4-scout", "meta-models/muse-spark"]}),
    ("openrouter.ai", {
        "label": "OpenRouter", "free": True, "reset_tz": "UTC", "only_free": True, "daily_cap": 50,
        "note": "Free models: 50 requests a day (1,000 once you've bought $10 of credits).",
        "fast": ["openai/gpt-oss-20b:free", "google/gemma-3-27b-it:free", "meta-llama/llama-3.3-70b-instruct:free"],
        "strong": ["openai/gpt-oss-120b:free", "qwen/qwen3-coder:free", "deepseek/deepseek-chat-v3.1:free", "z-ai/glm-4.5-air:free"],
        "vision": ["google/gemma-3-27b-it:free", "meta-llama/llama-4-maverick:free", "qwen/qwen2.5-vl-72b-instruct:free"]}),
    ("api.openai.com", {
        "label": "OpenAI", "reset_tz": "UTC",
        "fast": ["gpt-6-mini", "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"],
        "strong": ["gpt-6.1-sol", "gpt-6.1", "gpt-6", "gpt-5.1", "gpt-5", "gpt-4.1"]}),
    ("api.anthropic.com", {"label": "Anthropic", "reset_tz": "UTC", "fast": ["claude-haiku"], "strong": ["claude-sonnet"]}),
    ("api.x.ai", {"label": "xAI", "reset_tz": "UTC", "fast": ["grok-4-fast", "grok-3-mini"], "strong": ["grok-4"]}),
    ("api.mistral.ai", {"label": "Mistral", "reset_tz": "UTC", "fast": ["mistral-small-latest"],
                        "strong": ["mistral-large-latest", "mistral-medium-latest"]}),
    ("api.deepseek.com", {"label": "DeepSeek", "reset_tz": "UTC", "fast": ["deepseek-chat"], "strong": ["deepseek-chat"]}),
]
_SMALL = re.compile(r"(mini|nano|lite|small|haiku|instant|fast|flash|tiny|\b[0-9](\.[0-9])?b\b|[:-][0-9](\.[0-9])?b\b|-[0-9]{1,2}b\b)", re.I)
# Model names that take images. Matched loosely; text-only exceptions first.
_BLIND = re.compile(r"(gpt-oss|coder|codestral|deepseek-(chat|reasoner|r1|v3)|kimi|glm-4\.5|qwq|llama-3\.[0-3]-|mixtral|"
                    r"embed|whisper|tts|instant)", re.I)
_SEES = re.compile(r"(gemini|gemma-3|gpt-4o|gpt-4\.1|gpt-5|gpt-6|\bo[34]\b|claude|grok-4|grok-2-vision|llama-4|llama3\.2-vision|"
                   r"-vl|vl[:-]|qwen3\.[5-9]|llava|pixtral|mistral-(medium|large|small)|muse-spark|minicpm-v|moondream)", re.I)
_SKIP = re.compile(r"(embed|whisper|tts|guard|moderation|rerank|vision-preview|image|audio|transcribe|realtime|search)", re.I)


def is_auto(ref: str | None) -> bool:
    return (ref or "") in AUTO


def profile(conn: dict) -> dict:
    return next((p for host, p in PROFILES if host in conn["base_url"]), {"label": conn["name"], "reset_tz": "UTC"})


def settings() -> dict:
    try:
        s = json.loads(db.get_setting("router", "{}") or "{}")
    except ValueError:
        s = {}
    return {"order": s.get("order") or [], "disabled": s.get("disabled") or [], "models": s.get("models") or {},
            "local_fallback": bool(s.get("local_fallback", False))}


def save_settings(new: dict) -> dict:
    cur = settings()
    for k in ("order", "disabled", "models", "local_fallback"):
        if k in new and new[k] is not None:
            cur[k] = new[k]
    db.set_setting("router", json.dumps(cur))
    return cur


def _rank(conn: dict) -> int:
    if conn["auth_kind"] == "oauth_subscription":
        return 0  # already paid for by the user's plan
    if conn["auth_kind"] == "local_ollama":
        return 9
    if profile(conn).get("free"):
        return 1
    return 2 if conn["auth_kind"] == "api_key" else 3


def ordered_connections() -> list[dict]:
    """Enabled connections in routing order: the user's order, then subscriptions, free tiers, paid keys, servers, local."""
    s = settings()
    order = {int(c): i for i, c in enumerate(s["order"])}
    conns = db.all("SELECT * FROM connections WHERE enabled = 1 AND auth_kind != 'session_scraping_forbidden' ORDER BY id")
    return sorted(conns, key=lambda c: (order.get(c["id"], 1000), _rank(c), c["id"]))


def _routable(conns: list[dict]) -> list[dict]:
    s = settings()
    disabled = {int(c) for c in s["disabled"]}
    cloud = [c for c in conns if c["auth_kind"] != "local_ollama" and c["id"] not in disabled]
    local = [c for c in conns if c["auth_kind"] == "local_ollama" and c["id"] not in disabled]
    # Cloud first ("no pressure on the computer"); a local model only when the user allows it or nothing else exists
    return cloud + (local if (s["local_fallback"] or not cloud) else [])


def _match(models: list[str], prefs: list[str]) -> str | None:
    for p in prefs:
        if p in models:
            return p
        hits = sorted(m for m in models if m.startswith(p))
        if hits:
            return hits[-1]  # highest version of that family
    return None


def choose_model(conn: dict, models: list[str], tier: str) -> str | None:
    override = (settings()["models"].get(str(conn["id"])) or {}).get(tier)
    if override and (override in models or not models):
        return override
    prof = profile(conn)
    if prof.get("only_free"):
        models = [m for m in models if m.endswith(":free")]
    usable = [m for m in models if not _SKIP.search(m)]
    if not usable:
        return None
    if conn["auth_kind"] == "local_ollama":
        from . import config

        return config.DEFAULT_MODEL if config.DEFAULT_MODEL in usable else usable[0]
    pick = _match(usable, prof.get(tier) or []) or _match(usable, prof.get("strong" if tier == "fast" else "fast") or [])
    if pick:
        return pick
    small = [m for m in usable if _SMALL.search(m)]
    big = [m for m in usable if not _SMALL.search(m)]
    pool = (small or big) if tier == "fast" else (big or small)
    return sorted(pool)[-1]


def sees(model: str) -> bool:
    """Whether a model takes images, by name (providers do not list it)."""
    return bool(_SEES.search(model)) and not _BLIND.search(model)


def has_images(messages: list[dict]) -> bool:
    return any(isinstance(m.get("content"), list) and any(isinstance(p, dict) and p.get("type") in ("image_url", "input_image", "image")
                                                          for p in m["content"]) for m in messages)


def strip_images(messages: list[dict]) -> list[dict]:
    """The same conversation with every image replaced by a short note, for a model that cannot see."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            parts, dropped = [], 0
            for p in c:
                if isinstance(p, dict) and p.get("type") in ("image_url", "input_image", "image"):
                    dropped += 1
                else:
                    parts.append(p)
            if dropped:
                parts.append({"type": "text", "text": f"[{dropped} image(s) left out: the model answering cannot see images. "
                                                      "Work from the text, e.g. the accessibility element list.]"})
            m = {**m, "content": parts}
        out.append(m)
    return out


def choose_vision_model(conn: dict, models: list[str], tier: str) -> str | None:
    """A model on this connection that can see: the user's choice if it sees, the profile's picks, then any by name."""
    first = choose_model(conn, models, tier)
    if first and sees(first):
        return first
    prof = profile(conn)
    if prof.get("only_free"):
        models = [m for m in models if m.endswith(":free")]
    return _match(models, prof.get("vision") or []) or next((m for m in sorted(models, reverse=True) if sees(m) and not _SKIP.search(m)), None)


def pick_tier(ref: str, tools: list | None, messages: list[dict], channel: str) -> str:
    forced = AUTO.get(ref)
    if forced:
        return forced
    size = sum(len(m.get("content") or "") if isinstance(m.get("content"), str) else 2000 for m in messages)
    return "strong" if tools or channel in STRONG_CHANNELS or size > LONG_PROMPT_CHARS else "fast"


# Quota and cool-down state
def _day(conn: dict, now: float | None = None) -> str:
    tz = ZoneInfo(profile(conn).get("reset_tz", "UTC"))
    return datetime.fromtimestamp(now or time.time(), tz).strftime("%Y-%m-%d")


def next_reset(conn: dict, now: float | None = None) -> float:
    tz = ZoneInfo(profile(conn).get("reset_tz", "UTC"))
    local = datetime.fromtimestamp(now or time.time(), tz)
    return (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()


def state(conn: dict) -> dict:
    row = db.one("SELECT * FROM router_state WHERE connection_id = ?", conn["id"])
    today = _day(conn)
    if not row:
        db.run("INSERT INTO router_state(connection_id, day) VALUES (?, ?)", conn["id"], today)
        row = db.one("SELECT * FROM router_state WHERE connection_id = ?", conn["id"])
    if row["day"] != today:  # a new quota day: counts start again; a daily-limit park has expired by definition
        db.run("UPDATE router_state SET day = ?, requests = 0, tokens = 0, remaining_requests = CASE WHEN window = 'day' "
               "THEN NULL ELSE remaining_requests END WHERE connection_id = ?", today, conn["id"])
        row = db.one("SELECT * FROM router_state WHERE connection_id = ?", conn["id"])
    return row


def _int(v) -> int | None:
    try:
        return int(float(str(v).split(",")[0]))
    except (TypeError, ValueError):
        return None


def note_ok(conn: dict, headers: dict, usage: dict) -> dict:
    state(conn)
    h = headers or {}
    lim = _int(h.get("x-ratelimit-limit-requests") or h.get("anthropic-ratelimit-requests-limit") or h.get("x-ratelimit-limit"))
    rem = _int(h.get("x-ratelimit-remaining-requests") or h.get("anthropic-ratelimit-requests-remaining") or h.get("x-ratelimit-remaining"))
    tlim = _int(h.get("x-ratelimit-limit-tokens") or h.get("anthropic-ratelimit-tokens-limit"))
    trem = _int(h.get("x-ratelimit-remaining-tokens") or h.get("anthropic-ratelimit-tokens-remaining"))
    window = profile(conn).get("header_window", "minute") if lim is not None else ""
    tokens = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    db.run("UPDATE router_state SET requests = requests + 1, tokens = tokens + ?, last_ok_at = ?, last_error = '', "
           "cooldown_until = 0, cooldown_reason = '', "
           "limit_requests = COALESCE(?, limit_requests), remaining_requests = COALESCE(?, remaining_requests), "
           "limit_tokens = COALESCE(?, limit_tokens), remaining_tokens = COALESCE(?, remaining_tokens), "
           "window = CASE WHEN ? != '' THEN ? ELSE window END WHERE connection_id = ?",
           tokens, db.now(), lim, rem, tlim, trem, window, window, conn["id"])
    if rem == 0 and window == "day":
        _cool(conn, next_reset(conn), "daily limit reached")
    return quota(conn)


def _cool(conn: dict, until: float, reason: str) -> None:
    db.run("UPDATE router_state SET cooldown_until = ?, cooldown_reason = ?, last_error = ? WHERE connection_id = ?",
           until, reason, reason, conn["id"])


_DAILY = re.compile(r"(per ?day|perday|daily|\brpd\b|\btpd\b|requests per day|tokens per day|usage_limit_exceeded|"
                    r"usage_unavailable|free_tier_requests)", re.I)
_RATE = re.compile(r"(resource_exhausted|rate.?limit|quota|too many requests|429)", re.I)


def classify(err: Exception) -> tuple[str, float, bool]:
    """(reason for the user, seconds to park the provider, whether the next provider should be tried)."""
    upstream = getattr(err, "upstream", None)
    kind = getattr(err, "kind", "http")
    after = getattr(err, "retry_after", None)
    body = getattr(err, "body", "") or str(getattr(err, "detail", ""))
    status = getattr(err, "status_code", 500)
    if "insufficient_quota" in body:
        return "out of credit", 3600, True
    if upstream == 429 or kind == "quota" or (upstream is None and _RATE.search(body) and status != 401):
        if _DAILY.search(body):
            return "daily limit reached", -1, True  # -1: park until the provider's reset
        return "busy (rate limited)", after if after is not None else 30, True
    if upstream in (401, 403) or status == 401:
        return ("needs you to sign in again" if status == 401 and upstream is None else "key rejected"), 3600, True
    if upstream == 404:
        return "model not available", 0, True
    if kind in ("timeout", "unreachable") or (upstream or 0) >= 500 or status in (502, 503, 504):
        return "not responding", after if after is not None else 30, True
    return "refused the request", 0, True


def note_fail(conn: dict, err: Exception) -> str:
    state(conn)
    reason, park, _ = classify(err)
    if park == -1:
        after = getattr(err, "retry_after", None)
        until = time.time() + after if after and after > 60 else next_reset(conn)
        _cool(conn, until, reason)
    elif park:
        _cool(conn, time.time() + park, reason)
    else:
        db.run("UPDATE router_state SET last_error = ? WHERE connection_id = ?", reason, conn["id"])
    return reason


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def quota(conn: dict) -> dict:
    row = state(conn)
    prof = profile(conn)
    out = {"used_today": row["requests"], "tokens_today": row["tokens"], "free": bool(prof.get("free")),
           "resets_at": _iso(next_reset(conn)), "note": prof.get("note", "")}
    if row["limit_requests"] is not None:
        out.update(limit=row["limit_requests"], remaining=row["remaining_requests"], window=row["window"] or "minute")
    elif prof.get("daily_cap"):
        out.update(limit=prof["daily_cap"], remaining=max(0, prof["daily_cap"] - row["requests"]), window="day", estimated=True)
    if row["limit_tokens"] is not None:
        out.update(limit_tokens=row["limit_tokens"], remaining_tokens=row["remaining_tokens"])
    if row["cooldown_until"] > time.time():
        out.update(cooling_until=_iso(row["cooldown_until"]), cooling_reason=row["cooldown_reason"])
    return out


def _cooling(conn: dict) -> tuple[bool, str]:
    row = state(conn)
    return row["cooldown_until"] > time.time(), row["cooldown_reason"]


def candidates(tier: str, vision: bool = False) -> list[tuple[dict, str]]:
    """(connection, model) to try in order. Parked providers are left out, unless nothing else is left and the park
    was for a short, transient reason. vision: only models that can see."""
    from . import llm

    ready, parked = [], []
    for c in _routable(ordered_connections()):
        try:
            models = [m["model"] for m in llm.list_models_for(c["id"])]
        except HTTPException:
            models = []
        model = choose_vision_model(c, models, tier) if vision else choose_model(c, models, tier)
        if not model:
            continue
        cooling, reason = _cooling(c)
        (parked if cooling else ready).append((c, model, reason))
    transient = [(c, m) for c, m, r in parked if r in ("busy (rate limited)", "not responding")]
    return [(c, m) for c, m, _ in ready] or transient


def resting() -> list[dict]:
    """Providers the router is passing over right now because they're parked, so the answer can say so."""
    out = []
    for c in _routable(ordered_connections()):
        row = state(c)
        if row["cooldown_until"] > time.time():
            out.append({"provider": c["name"], "reason": row["cooldown_reason"], "until": _iso(row["cooldown_until"])})
    return out


def any_routable() -> bool:
    return bool(_routable(ordered_connections()))


def _meta(conn: dict, model: str, tier: str, tried: list[dict]) -> dict:
    return {"provider": conn["name"], "label": profile(conn).get("label", conn["name"]), "connection_id": conn["id"], "model": model,
            "ref": f"{conn['id']}/{model}", "tier": tier, "fallbacks": tried, "subscription": conn["auth_kind"] == "oauth_subscription",
            "resting": [r for r in resting() if r["provider"] != conn["name"] and r["provider"] not in {t["provider"] for t in tried}]}


def _all_failed(tried: list[dict]) -> HTTPException:
    if not tried:
        return HTTPException(400, "No cloud model is connected yet, or every provider is resting after its limit. "
                                  "Add a free Gemini or Groq key under Models.")
    parts = "; ".join(f"{t['provider']} ({t['model']}): {t['reason']}" for t in tried)
    status = 429 if all(t["reason"] in ("daily limit reached", "busy (rate limited)") for t in tried) else 502
    return HTTPException(status, f"Every connected provider failed. {parts}. Add another free provider under Models, or wait for a reset.")


def _channel(trace: dict | None) -> str:
    return (trace or {}).get("channel", "")


async def chat(ref: str, messages: list[dict], tools: list[dict] | None = None, trace: dict | None = None,
               schema: dict | None = None) -> dict:
    from . import llm

    tier = pick_tier(ref, tools, messages, _channel(trace))
    tried: list[dict] = []
    vision = has_images(messages)
    plan = [(c, m, messages) for c, m in candidates(tier, vision=True)] if vision else []
    # then text only: no images, nobody can see them, or every model that can see is out of quota
    text = strip_images(messages) if vision else messages
    seen = {(c["id"], m) for c, m, _ in plan}
    plan += [(c, m, text) for c, m in candidates(tier) if (c["id"], m) not in seen]
    for conn, model, msgs in plan:
        box: dict = {}
        token = CAPTURE.set(box)
        try:
            res = await llm.chat_one(f"{conn['id']}/{model}", msgs, tools, trace, schema)
        except (ProviderError, HTTPException) as e:
            tried.append({"provider": conn["name"], "model": model, "reason": note_fail(conn, e)})
            continue
        finally:
            CAPTURE.reset(token)
        res["router"] = {**_meta(conn, model, tier, tried), "quota": note_ok(conn, box.get("headers", {}), res.get("usage") or {})}
        if vision:
            res["router"]["vision"] = msgs is messages  # False: the images were left out
        return res
    raise _all_failed(tried)


async def stream(ref: str, messages: list[dict], trace: dict | None = None) -> AsyncIterator[dict]:
    """Like chat, streaming. Falls back only before the first token: a half-written answer is never restarted elsewhere."""
    from . import llm

    tier = pick_tier(ref, None, messages, _channel(trace))
    tried: list[dict] = []
    for conn, model in candidates(tier):
        box: dict = {}
        token = CAPTURE.set(box)
        started = False
        failure: Exception | None = None
        try:
            async for e in llm.stream_one(f"{conn['id']}/{model}", messages, trace):
                if "error" in e:
                    if started:
                        note_fail(conn, ProviderError(502, e["error"], upstream=e.get("status"), body=e.get("body", "")))
                        yield {**e, "router": {**_meta(conn, model, tier, tried)}}
                        return
                    failure = ProviderError(502, e["error"], upstream=e.get("status"), retry_after=e.get("retry_after"),
                                            body=e.get("body", e["error"]))
                    break
                if not started and ("delta" in e or "reasoning" in e):
                    started = True
                    yield {"router": _meta(conn, model, tier, tried)}
                if "done" in e:
                    yield {**e, "router": {**_meta(conn, model, tier, tried), "quota": note_ok(conn, box.get("headers", {}), e.get("usage") or {})}}
                    return
                yield e
        except (ProviderError, HTTPException) as e:
            failure = e
        except httpx.HTTPError as e:
            failure = ProviderError(502, f"{conn['name']} unreachable: {e.__class__.__name__}", kind="unreachable")
        finally:
            CAPTURE.reset(token)
        if started:
            return
        tried.append({"provider": conn["name"], "model": model, "reason": note_fail(conn, failure or ProviderError(502, "no answer", kind="unreachable"))})
    err = _all_failed(tried)
    yield {"error": err.detail, "router": {"fallbacks": tried}}


def status() -> dict:
    """Everything the Models page shows: the routing order, the model each tier would use, quota and cool-downs."""
    from . import llm

    s = settings()
    routable = {c["id"] for c in _routable(ordered_connections())}
    rows = []
    for c in ordered_connections():
        try:
            models = [m["model"] for m in llm.list_models_for(c["id"])]
        except HTTPException:
            models = []
        prof = profile(c)
        rows.append({"id": c["id"], "name": c["name"], "label": prof.get("label", c["name"]), "auth_kind": c["auth_kind"],
                     "free": bool(prof.get("free")), "in_router": c["id"] in routable, "disabled": c["id"] in {int(x) for x in s["disabled"]},
                     "fast": choose_model(c, models, "fast"), "strong": choose_model(c, models, "strong"), "models": models[:200],
                     "quota": quota(c), "state": c["state"], "local": c["auth_kind"] == "local_ollama"})
    return {"providers": rows, "settings": s, "tiers": {"fast": candidates("fast")[0][0]["name"] if candidates("fast") else None,
                                                         "strong": candidates("strong")[0][0]["name"] if candidates("strong") else None}}


def reset(cid: int) -> None:
    db.run("UPDATE router_state SET cooldown_until = 0, cooldown_reason = '', last_error = '' WHERE connection_id = ?", cid)
