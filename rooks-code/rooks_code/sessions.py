"""Sessions that survive, and context that never overflows.

Every session (messages, model, provider, cwd, usage) is one JSONL file under
~/.local/share/ai-workspace/rooks/sessions/ (XDG_DATA_HOME respected). `rooks --continue` resumes the newest for the
current folder, `rooks --session ID` a given one, `/sessions` lists them, `/resume ID` switches.

Context compaction: the model's context window comes from the provider's /models (context_length, when the provider
lists one), else a small built-in table, else 128k. Past 75% of it, the oldest turns are summarised into one message
by the model itself; the system prompt, the last 6 turns and every unfinished assistant tool_call/tool pair survive
intact. `/compact` forces it. A one-line notice is shown.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

KEEP_TURNS = 6              # newest messages always kept verbatim
TRIGGER = 0.75              # compact when the estimate passes this share of the context window
DEFAULT_CONTEXT = 128_000   # tokens, when the provider does not say and the model is unknown

# model id (lower cased prefix) -> context tokens. Small built-in table; the provider's /models wins when it lists one.
CONTEXT_TOKENS = {
    "gpt-4.1": 1_000_000, "gpt-4o": 128_000, "gpt-5": 400_000, "o4-mini": 200_000,
    "claude-sonnet-4": 200_000, "claude-opus-4": 200_000, "claude-haiku": 200_000, "claude-3": 200_000,
    "gemini-2.5": 1_000_000, "gemini-2": 1_000_000, "gemini-1.5": 2_000_000,
    "grok-4": 256_000, "grok-3": 131_072,
    "deepseek-chat": 128_000, "deepseek-reasoner": 128_000,
    "qwen2.5": 131_072, "qwen3": 131_072, "llama-3": 131_072, "llama3": 131_072,
    "mistral-large": 128_000, "mistral": 32_000,
    "kimi": 256_000, "moonshot": 256_000, "nemotron": 131_072,
}


def context_window(model: str, provider_listed: dict | None = None) -> int:
    """Context tokens for a model: the provider's /models context_length, else the table, else 128k."""
    if provider_listed:
        for m in provider_listed.get("data", []):
            if m.get("id") == model and m.get("context_length"):
                return int(m["context_length"])
    bare = model.split("/")[-1].lower()
    for prefix in sorted(CONTEXT_TOKENS, key=len, reverse=True):
        if bare.startswith(prefix):
            return CONTEXT_TOKENS[prefix]
    return DEFAULT_CONTEXT


def estimate_tokens(messages: list[dict], system: str = "") -> int:
    """~4 chars per token, the heuristic the budget check already uses."""
    return (len(system) + sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)) // 4


def sessions_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "ai-workspace" / "rooks" / "sessions"


class SessionStore:
    def __init__(self, root: Path, provider: str, model: str):
        self.id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
        self.root, self.provider, self.model = root, provider, model
        self.created = time.time()
        d = sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{self.id}.jsonl"
        self._write({"type": "meta", "id": self.id, "provider": provider, "model": model,
                     "cwd": str(root), "created": self.created})

    def _write(self, obj: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def save_message(self, msg: dict) -> None:
        self._write({"type": "message", "message": msg})


def _read(path: Path) -> tuple[dict, list[dict]]:
    meta, messages = {}, []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("type") == "meta":
                meta = obj
            elif obj.get("type") == "message":
                messages.append(obj["message"])
    except (OSError, ValueError):
        pass
    return meta, messages


def load(session_id: str) -> tuple[dict, list[dict]]:
    return _read(sessions_dir() / f"{session_id}.jsonl")


def list_all(root: Path | None = None) -> list[dict]:
    out = []
    d = sessions_dir()
    for f in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta, msgs = _read(f)
        if meta and (root is None or meta.get("cwd") == str(root)):
            out.append({"id": meta.get("id", f.stem), "provider": meta.get("provider", ""), "model": meta.get("model", ""),
                        "cwd": meta.get("cwd", ""), "created": meta.get("created", 0), "messages": len(msgs)})
    return out


def newest_for(root: Path) -> tuple[dict, list[dict]] | None:
    found = list_all(root)
    return load(found[0]["id"]) if found else None


def _is_pair_complete(messages: list[dict], i: int) -> bool:
    """True when the assistant message at i has every one of its tool_calls answered by a following tool message."""
    m = messages[i]
    if m.get("role") != "assistant" or not m.get("tool_calls"):
        return True
    want = {c["id"] for c in m["tool_calls"]}
    got = {x.get("tool_call_id") for x in messages[i + 1:] if x.get("role") == "tool"}
    return want <= got


def split_for_compaction(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """(old to summarise, recent to keep). The recent window is the last KEEP_TURNS messages, moved forward until no
    assistant tool_call/tool pair is split (a pair never straddles the boundary)."""
    if len(messages) <= KEEP_TURNS:
        return [], list(messages)
    cut = len(messages) - KEEP_TURNS
    while cut > 0 and not _is_pair_complete(messages, cut - 1):
        cut -= 1
    return messages[:cut], messages[cut:]


SUMMARY_PROMPT = ("Summarise this conversation so far for the model that continues it. Keep: what the user asked, "
                  "decisions made, files changed (paths), errors seen, and anything still to do. Plain text, terse.")


def maybe_compact(agent, notice) -> bool:
    """Compact when the estimate passes 75% of the context window. notice() prints the one line. Returns True on compact."""
    window = context_window(agent.s.model)
    if estimate_tokens(agent.messages, agent.system) < window * TRIGGER:
        return False
    return compact(agent, notice)


def compact(agent, notice) -> bool:
    """Summarise the oldest turns into one message. System prompt, last KEEP_TURNS and every open tool pair survive."""
    old, keep = split_for_compaction(agent.messages)
    if not old:
        return False
    notice(f"compacting: summarising the oldest {len(old)} messages "
           f"(~{estimate_tokens(agent.messages, agent.system):,} of {context_window(agent.s.model):,} tokens)")
    text = "\n\n".join(
        (f"{m.get('role')}: " + (m.get('content') or ""))[:2000] if isinstance(m.get('content'), str)
        else f"{m.get('role')}: " + json.dumps(m)[:2000] for m in old)
    try:
        r = agent.model.complete(SUMMARY_PROMPT, [{"role": "user", "content": text}], [])
        summary = (r.text or "").strip() or "Earlier turns were summarised."
    except Exception:
        summary = "Earlier turns were summarised (summary call failed; the recent context below is authoritative)."
    agent.messages[:] = [{"role": "user", "content": f"[summary of the conversation so far]\n{summary}"}] + keep
    return True
