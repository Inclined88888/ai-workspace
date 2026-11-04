"""Rooks gets better the more you use it.

Through the AI Workspace app (provider workbench) Rooks shares the app's memory and skills, so what you taught in
chat helps in the terminal and the other way round:
  * before a request, the facts that fit it and the index of saved skills go into the system prompt
  * the remember tool saves a fact you state ("I use pnpm", "my name is Azad") in the app's memory
  * the load_skill tool reads a saved procedure's steps
  * after a request, it is sent to the app, which keeps it in the chat history as a terminal chat and, in the
    background, learns facts and (after a task of several steps) saves or improves a reusable skill

With any other provider the same tools work on a local file (memory.json next to rooks.toml): remembered facts come
back in every later session, in every folder.

Learning never blocks or breaks a request: every call has a short timeout and failures are ignored.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .tools import Plan, ToolError

TIMEOUT = 4  # seconds; the app is local
MAX_LOCAL = 80  # local facts kept (oldest dropped)
WEB_TOOLS = ("fetch", "browse", "web", "search_web", "http")  # MCP tools that read third-party text

SPECS = [
    {"name": "remember", "description": "Save a durable fact about the user or how they like things done, so later "
                                        "sessions know it (their name, tools they prefer, project conventions). Only "
                                        "facts the user stated or confirmed. One short sentence.",
     "parameters": {"type": "object", "properties": {
         "fact": {"type": "string"},
         "kind": {"type": "string", "enum": ["profile", "preference", "fact"]}}, "required": ["fact"]}},
]
SKILL_SPEC = {"name": "load_skill", "description": "Read the steps of one of your saved skills (listed in your "
                                                   "instructions) before doing that kind of task.",
              "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}


def local_path() -> Path:
    return config.config_path().parent / "memory.json"


def _load_local() -> list[str]:
    try:
        d = json.loads(local_path().read_text(encoding="utf-8"))
        return [str(x) for x in d.get("facts", [])] if isinstance(d, dict) else []
    except (OSError, ValueError):
        return []


def _save_local(facts: list[str]) -> None:
    p = local_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"facts": facts[-MAX_LOCAL:]}, ensure_ascii=False, indent=1), encoding="utf-8")


def _norm(s: str) -> str:
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


class Learning:
    def __init__(self, settings, session_id: str = ""):
        self.s = settings
        self.session = session_id
        self.skills: list[dict] = []

    # where ---------------------------------------------------------------------------------------------------------
    @property
    def app(self) -> bool:
        return self.s.provider == "workbench" and bool(self.s.base_url)

    def _url(self, path: str) -> str:
        return self.s.base_url.rstrip("/") + "/rooks" + path  # base_url ends in /v1

    def _req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", **(getattr(self.s, "headers", None) or {})}
        if self.s.api_key:
            headers["Authorization"] = f"Bearer {self.s.api_key}"
        req = urllib.request.Request(self._url(path), data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - the app's own local address
            return json.loads(r.read() or b"{}")

    # before a request ----------------------------------------------------------------------------------------------
    def context(self, prompt: str) -> str:
        """Extra system prompt text for this request: what is known that fits, and the saved skills."""
        if self.app:
            try:
                d = self._req("GET", "/context?" + urllib.parse.urlencode({"q": prompt[:2000]}))
            except (OSError, ValueError, urllib.error.URLError):
                return ""
            self.skills = [s for s in d.get("skills") or [] if isinstance(s, dict) and s.get("name")]
            parts = [d["context"]] if d.get("context") else []
            if self.skills:
                parts.append("Your saved skills (call load_skill before doing that kind of task):\n" +
                             "\n".join(f"- {s['name']}: {s.get('description', '')}" for s in self.skills))
            return "\n\n".join(parts)
        facts = _load_local()
        return ("What you remember about the user (from earlier sessions):\n" + "\n".join(f"- {f}" for f in facts[-40:])
                if facts else "")

    def specs(self) -> list[dict]:
        return SPECS + ([SKILL_SPEC] if self.skills else [])

    def names(self) -> set[str]:
        return {s["name"] for s in self.specs()}

    # tools ---------------------------------------------------------------------------------------------------------
    def prepare(self, name: str, a: dict) -> Plan:
        if name == "remember":
            fact = " ".join(str(a.get("fact") or "").split())[:500]
            if len(fact) < 4:
                raise ToolError("fact must be a short sentence")
            kind = str(a.get("kind")) if a.get("kind") in ("profile", "preference", "fact") else "fact"
            return Plan("read", f"remember: {fact}", fact, lambda: (self.remember(fact, kind), None, ""))
        if name == "load_skill":
            skill = str(a.get("name") or "").strip()
            if not skill:
                raise ToolError("name is required")
            return Plan("read", f"load skill {skill}", skill, lambda: (self.load_skill(skill), None, ""))
        raise ToolError(f"unknown tool {name}")

    def remember(self, fact: str, kind: str = "fact") -> str:
        if self.app:
            try:
                r = self._req("POST", "/remember", {"content": fact, "kind": kind})
            except (OSError, ValueError, urllib.error.URLError) as e:
                return f"Error: the app could not save it ({e})"
            return "Saved to your AI Workspace memory." if r.get("saved") else "Already known; nothing new saved."
        facts = _load_local()
        n = _norm(fact)
        if any(_norm(f) == n for f in facts):
            return "Already known; nothing new saved."
        facts = (facts + [fact])[-MAX_LOCAL:]
        try:
            _save_local(facts)
        except OSError as e:
            return f"Error: could not save it ({e})"
        return f"Saved. Rooks will remember it in later sessions ({local_path()})."

    def facts(self) -> list[str]:
        """What is remembered, for /memory."""
        if self.app:
            try:
                return [f["content"] for f in self._req("GET", "/memory").get("facts") or [] if f.get("content")]
            except (OSError, ValueError, urllib.error.URLError):
                return []
        return _load_local()

    def forget(self, text: str) -> str:
        """Drop memories that contain these words. The same store chat uses, when the app is connected."""
        phrase = " ".join((text or "").split())
        if len(phrase) < 2:
            return "Say which memory to forget, or say everything."
        if self.app:
            try:
                r = self._req("POST", "/forget", {"text": phrase})
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    return "Say which memory to forget, or say everything."
                return f"Error: the app could not forget it ({e.code})"
            except (OSError, ValueError, urllib.error.URLError) as e:
                return f"Error: the app could not forget it ({e})"
            gone = [str(x) for x in (r.get("forgotten") or []) if str(x).strip()]
        else:
            facts = _load_local()
            if phrase.lower() in {"everything", "all", "*"}:
                gone, kept = list(facts), []
            else:
                needle = phrase.lower()
                gone = [f for f in facts if needle in f.lower()]
                kept = [f for f in facts if needle not in f.lower()]
            if gone:
                try:
                    _save_local(kept)
                except OSError as e:
                    return f"Error: could not forget it ({e})"
        if not gone:
            return "Nothing matched that."
        return "\n".join(f"Forgot: {item}" for item in gone[:40])

    def history(self, q: str = "", limit: int = 12) -> list[dict]:
        """Search past Chat and terminal turns via the app (Hermes-style session recall)."""
        if not self.app:
            return []
        try:
            d = self._req("GET", "/history?" + urllib.parse.urlencode({"q": q[:200], "limit": str(limit)}))
            return [r for r in (d.get("results") or []) if isinstance(r, dict)]
        except (OSError, ValueError, urllib.error.URLError):
            return []

    def skill_list(self) -> list[dict]:
        """Saved skills, for /skills (the app's; none without it)."""
        if self.app and not self.skills:
            self.context("")
        return self.skills

    def check_skill(self, url: str) -> dict:
        """Hand a GitHub skill to the app's security check; the user installs it on the app's Skills page."""
        if not self.app:
            return {"ok": False, "error": "Skills from GitHub are checked and installed through the AI Workspace app; it isn't connected."}
        data = json.dumps({"url": url}).encode()
        headers = {"Content-Type": "application/json", **(getattr(self.s, "headers", None) or {})}
        if self.s.api_key:
            headers["Authorization"] = f"Bearer {self.s.api_key}"
        req = urllib.request.Request(self._url("/skills/check"), data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 - the app's own local address
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"the app answered {e.code}"}
        except (OSError, ValueError, urllib.error.URLError) as e:
            return {"ok": False, "error": str(e)}

    def app_page(self, path: str) -> str:
        return self.s.base_url.rstrip("/").removesuffix("/v1") + path

    def load_skill(self, name: str) -> str:
        if not self.app:
            return "Error: saved skills come from the AI Workspace app; it is not connected."
        try:
            d = self._req("GET", "/skills/" + urllib.parse.quote(name, safe=""))
        except urllib.error.HTTPError as e:
            return f"Error: no skill named {name}" if e.code == 404 else f"Error: {e}"
        except (OSError, ValueError, urllib.error.URLError) as e:
            return f"Error: {e}"
        return f"Skill {d.get('name')}: {d.get('description', '')}\n\n{d.get('steps', '')}"

    # after a request -----------------------------------------------------------------------------------------------
    def after(self, prompt: str, answer: str, messages: list[dict], wait: bool = False) -> threading.Thread | None:
        """Send the finished request to the app to learn from (in the background). Nothing to do without the app:
        local learning is the remember tool."""
        if not self.app or not prompt.strip():
            return None
        calls, results = [], {m.get("tool_call_id"): m.get("content") or "" for m in messages if m.get("role") == "tool"}
        for m in messages:
            for c in m.get("tool_calls") or []:
                f = c.get("function") or {}
                calls.append({"name": str(f.get("name", ""))[:200], "arguments": str(f.get("arguments", ""))[:4000],
                              "result": str(results.get(c.get("id"), ""))[:4000]})
        untrusted = any(any(w in c["name"].lower() for w in WEB_TOOLS) for c in calls)
        body = {"session": self.session or "rooks", "prompt": prompt[:20000], "answer": (answer or "")[:40000],
                "calls": calls[:200], "model": self.s.model, "untrusted": untrusted, "folder": str(self.s.root)[:300]}

        def send():
            try:
                self._req("POST", "/turn", body)
            except (OSError, ValueError, urllib.error.URLError):
                pass  # learning is best effort

        t = threading.Thread(target=send, daemon=True)
        t.start()
        if wait:
            t.join(TIMEOUT + 1)
        return t
