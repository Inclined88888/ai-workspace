"""Tools and context that make Rooks a full working agent, not only a file editor:

* project instructions: AGENTS.md, CLAUDE.md, ROOKS.md or .rooks/instructions.md (nearest first, up to the git root)
  go into the system prompt, so the agent follows the project's own rules
* todo: a visible checklist the agent keeps for multi-step work (shown live in the terminal)
* web_search and web_fetch: through the AI Workspace app's search, or straight from the web without it
* background commands: start a server, watcher or long test run, check its output later, stop it
* task: a sub-agent with its own fresh context — explore (read-only research), plan (read-only), or build (can edit/run
  under the same approvals as you)
"""

from __future__ import annotations

import html
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from .tools import Plan, ToolError, _clip

INSTRUCTION_FILES = ("AGENTS.md", "ROOKS.md", "CLAUDE.md", ".rooks/instructions.md")
MAX_INSTRUCTIONS = 24_000
TIMEOUT = 25

SPECS = [
    {"name": "todo", "description": "Keep a short checklist for multi-step work. Send the whole list each time with each item's "
                                    "status (pending, doing, done). Use it for any task of 3+ steps; the user sees it live.",
     "parameters": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
         "text": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "doing", "done"]}},
         "required": ["text", "status"]}}}, "required": ["items"]}},
    {"name": "web_search", "description": "Search the web. Returns titles, links and snippets.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "count": {"type": "integer"}},
                    "required": ["query"]}},
    {"name": "web_fetch", "description": "Read a web page (docs, an issue, an API reference) as plain text.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "start_background", "description": "Start a long-running command (dev server, watcher, long test run) without "
                                                "waiting for it. Returns a job id; read its output with check_background.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "check_background", "description": "The status and newest output of a background job (or all jobs without an id).",
     "parameters": {"type": "object", "properties": {"job": {"type": "string"}, "lines": {"type": "integer"}}}},
    {"name": "stop_background", "description": "Stop a background job.",
     "parameters": {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]}},
    {"name": "task", "description": "Give a self-contained job to a sub-agent with a fresh context. mode=explore (default) "
                                    "or plan: read-only research. mode=build: may edit files and run commands, with the same "
                                    "approvals as you. Returns a dense summary. Use explore to scan a large codebase; use "
                                    "build for a focused fix that would clutter your own context.",
     "parameters": {"type": "object", "properties": {
         "goal": {"type": "string"},
         "mode": {"type": "string", "enum": ["explore", "plan", "build"],
                  "description": "explore/plan = read-only; build = edit and run under your approvals"}},
                    "required": ["goal"]}},
]
NAMES = {s["name"] for s in SPECS}
READ_ONLY = {"read_file", "list_files", "search", "web_search", "web_fetch"}
# Build sub-agents get file tools + shell + checklist, not nested task/background (keeps trust and context bounded).
BUILD_TOOLS = READ_ONLY | {"write_file", "edit_file", "run_command", "todo"}
TASK_MODES = frozenset({"explore", "plan", "build"})


# project instructions -------------------------------------------------------------------------------------------------
def project_instructions(root: Path) -> tuple[str, list[str]]:
    """The project's instruction files, nearest folder first, up to the repository root. Returns (text, paths)."""
    root = root.resolve()
    folders = [root]
    for p in root.parents:
        folders.append(p)
        if (p / ".git").exists() or p == Path.home():
            break
    if (root / ".git").exists():
        folders = [root]
    parts, found, used = [], [], 0
    for folder in folders:
        for name in INSTRUCTION_FILES:
            f = folder / name
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if not text:
                continue
            text = text[: MAX_INSTRUCTIONS - used]
            used += len(text)
            found.append(str(f))
            parts.append(f"## {f}\n{text}")
            if used >= MAX_INSTRUCTIONS:
                break
    if not parts:
        return "", []
    return ("Project instructions (from the project's own files; follow them unless the user says otherwise):\n\n"
            + "\n\n".join(parts)), found


# web ------------------------------------------------------------------------------------------------------------------
class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "pre", "section", "article", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self.out.append(data)


def html_to_text(page: str) -> tuple[str, str]:
    p = _Text()
    try:
        p.feed(page)
    except Exception:  # noqa: BLE001 - malformed pages still give what was parsed
        pass
    text = re.sub(r"[ \t]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return p.title.strip(), text


def _get(url: str, headers: dict | None = None) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Rooks", **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - the user's agent reading the web
        ctype = r.headers.get("Content-Type", "")
        return r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", errors="replace"), ctype


class Web:
    def __init__(self, learning=None):
        self.learning = learning  # through the app: its search service

    def _app(self, path: str):
        lr = self.learning
        return lr._req("GET", path) if lr is not None and lr.app else None

    def search(self, query: str, count: int = 6) -> str:
        try:
            d = self._app("/search?" + urllib.parse.urlencode({"q": query, "count": count}))
        except urllib.error.HTTPError:
            d = None  # the app's search is switched off or failing: fall back below
        except (OSError, ValueError):
            d = None
        if d is None:  # no app, or its search is not set up: straight to the web
            d = {"results": self._duck(query, count)}
        rows = d.get("results") or []
        if not rows:
            return "No results."
        return "\n\n".join(f"{i}. {r.get('title', '')}\n   {r.get('url', '')}\n   {r.get('snippet', '')}"
                           for i, r in enumerate(rows[:count], 1))

    @staticmethod
    def _brave(query: str, count: int) -> list[dict]:
        """Brave Search's public results page, read like a browser would (fallback; its layout can change)."""
        page, _ = _get("https://search.brave.com/search?" + urllib.parse.urlencode({"q": query}),
                       {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/126 Safari/537.36", "Accept-Language": "en"})
        strip = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()  # noqa: E731
        out = []
        for block in page.split('data-type="web"')[1:]:
            block = block[:8000]
            href = re.search(r'<a href="(https?://[^"]+)"', block)
            title = re.search(r'class="title[^"]*"[^>]*>(.*?)</div>', block, re.S)
            desc = re.search(r'class="(?:snippet-description|content|generic-snippet)[^"]*"[^>]*>(.*?)</div>', block, re.S)
            if href and title:
                out.append({"title": strip(title.group(1)), "url": href.group(1), "snippet": strip(desc.group(1) if desc else "")[:300]})
            if len(out) >= count:
                break
        return out

    @classmethod
    def _duck(cls, query: str, count: int) -> list[dict]:
        try:
            found = cls._brave(query, count)
        except (OSError, urllib.error.URLError):
            found = []
        if found:
            return found
        """Without the app's search: Brave's results page, then DuckDuckGo's plain HTML page (either may refuse)."""
        try:
            page, _ = _get("https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query}))
        except (OSError, urllib.error.URLError) as e:
            raise ToolError(f"web search failed ({e}); open the AI Workspace app to search through it") from None
        out = []
        for m in re.finditer(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', page, re.S):
            link = html.unescape(m.group(1))
            if "uddg=" in link:
                link = urllib.parse.unquote(link.split("uddg=", 1)[1].split("&", 1)[0])
            strip = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()  # noqa: E731
            out.append({"title": strip(m.group(2)), "url": link, "snippet": strip(m.group(3))})
            if len(out) >= count:
                break
        if not out:
            raise ToolError("web search is not set up: add the free search key in the AI Workspace app (Settings > Web search). web_fetch still reads any page you have a link for")
        return out

    def fetch(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise ToolError("url must start with http:// or https://")
        try:
            page, ctype = _get(url)
        except urllib.error.HTTPError as e:
            return f"Error: {url} answered {e.code}"
        except (OSError, urllib.error.URLError) as e:
            return f"Error: could not read {url} ({e})"
        if "html" in ctype or page.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
            title, text = html_to_text(page)
            return _clip(f"{title}\n{url}\n\n{text}" if title else f"{url}\n\n{text}", 30_000)
        return _clip(page, 30_000)


# background jobs ------------------------------------------------------------------------------------------------------
class Jobs:
    def __init__(self, root: Path):
        self.root = root
        self.jobs: dict[str, dict] = {}
        self._n = 0

    def start(self, command: str) -> str:
        self._n += 1
        jid = f"job{self._n}"
        log = self.root / ".rooks" / "jobs" / f"{jid}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        f = open(log, "w", encoding="utf-8")  # noqa: SIM115 - kept open for the job's life
        kw = {"start_new_session": True} if sys.platform != "win32" else {}
        p = subprocess.Popen(command, shell=True, cwd=self.root, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
        self.jobs[jid] = {"command": command, "proc": p, "log": log, "file": f, "started": time.time()}
        time.sleep(1.5)  # early failures (bad command, port in use) show up straight away
        return f"started {jid} (pid {p.pid}): {command}\n" + self.check(jid, 20)

    def check(self, jid: str = "", lines: int = 40) -> str:
        if not jid:
            if not self.jobs:
                return "No background jobs."
            return "\n".join(self._line(j, d) for j, d in self.jobs.items())
        d = self.jobs.get(jid)
        if not d:
            raise ToolError(f"no job {jid}; jobs: {', '.join(self.jobs) or 'none'}")
        try:
            tail = d["log"].read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(lines, 400)):]
        except OSError:
            tail = []
        return self._line(jid, d) + "\n" + _clip("\n".join(tail), 12_000)

    @staticmethod
    def _line(jid: str, d: dict) -> str:
        code = d["proc"].poll()
        state = "running" if code is None else f"finished, exit code {code}"
        return f"{jid}: {state} for {int(time.time() - d['started'])}s · {d['command']}"

    def stop(self, jid: str) -> str:
        d = self.jobs.get(jid)
        if not d:
            raise ToolError(f"no job {jid}")
        p = d["proc"]
        if p.poll() is None:
            try:
                if sys.platform != "win32":
                    os.killpg(p.pid, signal.SIGTERM)
                else:
                    p.terminate()
                p.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                p.kill()
        d["file"].close()
        return f"stopped {jid}"

    def stop_all(self) -> None:
        for jid in list(self.jobs):
            try:
                self.stop(jid)
            except ToolError:
                pass


# the toolbox ----------------------------------------------------------------------------------------------------------
class Extras:
    def __init__(self, agent):
        self.agent = agent
        self.web = Web(getattr(agent, "learning", None))
        self.jobs = Jobs(agent.root)
        self.todos: list[dict] = []

    def prepare(self, name: str, a: dict) -> Plan:
        ui = self.agent.ui
        if name == "todo":
            items = [{"text": str(i.get("text", ""))[:200], "status": i.get("status") if i.get("status") in ("pending", "doing", "done") else "pending"}
                     for i in (a.get("items") or []) if isinstance(i, dict) and i.get("text")]
            if not items:
                raise ToolError("items is required")

            def apply():
                self.todos = items
                show = getattr(ui, "todos", None)
                if show:
                    show(items)
                done = sum(i["status"] == "done" for i in items)
                return f"checklist saved ({done}/{len(items)} done)", None, ""
            return Plan("read", f"checklist: {sum(i['status'] == 'done' for i in items)}/{len(items)} done", "", apply)
        if name == "web_search":
            q = str(a.get("query") or "").strip()
            if not q:
                raise ToolError("query is required")
            n = max(1, min(int(a.get("count") or 6), 10))
            return Plan("read", f"search the web: {q}", q, lambda: (self.web.search(q, n), None, ""))
        if name == "web_fetch":
            url = str(a.get("url") or "").strip()
            return Plan("read", f"read {url}", url, lambda: (self.web.fetch(url), None, ""))
        if name == "start_background":
            cmd = str(a.get("command") or "").strip()
            if not cmd:
                raise ToolError("command is required")
            return Plan("command", f"start in background: {cmd}", f"$ {cmd} &    (cwd {self.agent.root})",
                        lambda: (self.jobs.start(cmd), None, "exit code 0"))
        if name == "check_background":
            jid = str(a.get("job") or "")
            return Plan("read", f"check {jid or 'background jobs'}", "", lambda: (self.jobs.check(jid, int(a.get("lines") or 40)), None, ""))
        if name == "stop_background":
            jid = str(a.get("job") or "")
            return Plan("command", f"stop {jid}", f"stop {jid}", lambda: (self.jobs.stop(jid), None, "exit code 0"))
        if name == "task":
            goal = str(a.get("goal") or "").strip()
            if not goal:
                raise ToolError("goal is required")
            mode = str(a.get("mode") or "explore").strip().lower()
            if mode not in TASK_MODES:
                mode = "explore"
            label = f"sub-agent ({mode}): {goal[:100]}"
            # build may write/run — effect must trigger the same approvals as a direct edit
            effect = "write" if mode == "build" else "read"
            return Plan(effect, label, goal, lambda g=goal, m=mode: (self.subagent(g, m), None, ""))
        raise ToolError(f"unknown tool {name}")

    def subagent(self, goal: str, mode: str = "explore") -> str:
        """A fresh agent; explore/plan are read-only, build may edit/run under the parent's approvals."""
        from .agent import Agent

        mode = mode if mode in TASK_MODES else "explore"
        parent = self.agent
        allow_writes = mode == "build"
        sub_ui = _SubUI(parent.ui, allow_writes=allow_writes)
        sub = Agent(parent.s, sub_ui, model=parent.model, log=parent.log, sub=True, sub_mode=mode)
        if allow_writes:
            brief = ("You are a sub-agent doing a focused build job for another agent. You may edit files and run commands; "
                     "the user approves risky steps the same way as the parent. When done, reply with a dense summary: "
                     "what you changed, exact paths, test results, and what is left.\n\nGoal: ")
        else:
            brief = ("You are a sub-agent doing research for another agent. Use only read-only tools. When done, reply "
                     "with a dense summary of what you found: exact file paths, line numbers, facts, and open "
                     "questions. Do not change anything.\n\nGoal: ")
        answer = sub.run(brief + goal)
        return answer or "(the sub-agent returned nothing)"

    def close(self) -> None:
        self.jobs.stop_all()


class _SubUI:
    """Sub-agent activity indented under the parent. Explore/plan never approve writes; build asks the parent UI."""

    def __init__(self, ui, allow_writes: bool = False):
        self.ui = ui
        self.allow_writes = allow_writes

    def tool_note(self, text, read=False):
        self.ui.tool_note("  ↳ " + text, read=read) if hasattr(self.ui, "tool_note") else None

    def ask(self, plan):
        if self.allow_writes and hasattr(self.ui, "ask"):
            return self.ui.ask(plan)
        return "n"

    def __getattr__(self, name):
        if name in ("assistant", "footer", "stream", "stream_end", "show_plan", "tool_result", "thinking", "todos"):
            return lambda *a, **k: None
        return getattr(self.ui, name)


_lock = threading.Lock()
