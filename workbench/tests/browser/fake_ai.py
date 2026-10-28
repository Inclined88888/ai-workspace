"""A stand-in AI provider for the browser tests. Standard library only.

It answers like an OpenAI compatible server in two places:
  /v1/...                     a plain endpoint (Custom AI Server)
  /openrouter/api/v1/...      shaped like OpenRouter: /models with real-format `pricing`, /key, cost in usage

Its replies are scripted by the user's words, so the tests can follow real journeys (ask, get a task created, see it in
Tasks). It is NOT a model: passing tests against it says the interface and the server work, never that a real
provider does. Every request is appended to the log file given on the command line.

Usage: python3 fake_ai.py PORT LOGFILE
"""

import datetime
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
LOG = sys.argv[2] if len(sys.argv) > 2 else "/dev/null"
GOOD_KEY = "sk-or-test-good"
ZERO = {"prompt": "0", "completion": "0"}
ROUTER_MODELS = [
    {"id": "acme/helper:free", "context_length": 131072, "supported_parameters": ["tools", "temperature"], "pricing": ZERO},
    {"id": "acme/notools:free", "context_length": 262144, "supported_parameters": ["temperature"], "pricing": ZERO},
    {"id": "acme/pro", "context_length": 200000, "supported_parameters": ["tools"], "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
]


def say(text):
    return {"role": "assistant", "content": text}


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"call_{time.monotonic_ns()}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def reply(body):
    msgs = body.get("messages") or []
    tools = body.get("tools") or []
    if tools and tools[0].get("function", {}).get("name") == "noop":
        return say("OK")  # connection check
    system = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" and isinstance(msgs[0]["content"], str) else ""
    if system.startswith("You maintain long term memory"):
        return say("[]")
    if system.startswith("You review a completed task"):
        return say('{"save": false}')
    if system.startswith("You compress the older part"):
        return say("The user and assistant talked about planning the week.")
    if system.startswith("You plan missions"):
        return say(json.dumps({"steps": [
            {"title": "List what the offsite needs", "done_when": "A list of needs is written", "needs_tool": False},
            {"title": "Draft the invitation", "done_when": "An invitation draft is written", "needs_tool": False}]}))
    if system.startswith("You check whether"):
        return say(json.dumps({"verdict": "done", "evidence": "The reply contains the requested text.", "missing": ""}))
    if system.startswith("Write the final answer"):
        return say("The offsite plan and invitation draft are ready.")
    if system.startswith("You write proactive check-in briefings"):
        return say(json.dumps({"notify": True, "urgency": "normal", "briefing": "One task is due today."}))
    users = [i for i, m in enumerate(msgs) if m["role"] == "user"]
    if not users:
        return say("OK")
    goal = msgs[users[-1]]["content"] if isinstance(msgs[users[-1]]["content"], str) else ""
    results = [m.get("content") or "" for m in msgs[users[-1]:] if m["role"] == "tool"]
    if "Now do step" in goal:
        return say("Done: " + goal.split("Now do step ")[1].split("\n")[0][:120] + ". OK")
    m = re.search(r"remind me to (.+?)(?: tomorrow)?[.?!]*$", goal, re.I)
    if m:
        what = m.group(1).strip()
        if not results:
            due = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
            return call("create_task", title=what[:1].upper() + what[1:], due=due)
        return say(f"Done. **{what[:1].upper() + what[1:]}** is on your task list for tomorrow.")
    if re.search(r"what('s| is) on my (task )?list", goal, re.I):
        if not results:
            return call("list_tasks")
        return say("Here is what is open:\n\n" + results[-1][:600])
    if re.search(r"AI spending", goal, re.I):
        if not results:
            return call("get_usage", days=30)
        return say("Here is your AI usage for the last 30 days.")
    if re.search(r"save a note", goal, re.I):
        if not results:
            return call("create_note", title="Ideas for the team offsite", body="- Walk on the beach\n- Cooking class")
        return say("Saved the note **Ideas for the team offsite**.")
    # v13 scenarios: the assistant as the place to get work done
    n = len(results)
    if goal.startswith("Research "):
        if not n:
            return call("start_research", question=goal[len("Research "):].strip(), depth=1)
        return say("I started the research. The report will appear here when it is ready.")
    f = re.search(r"\(file #(\d+)\)", goal)
    if f and "region" in goal.lower():
        if not n:
            return call("analyze_table", file_id=int(f.group(1)), group_by="Region", value="Revenue")
        return say("**North** sells the most: 1,500.50 of the 2,300.50 total revenue. One East row has no revenue.")
    if goal.lower().startswith("write a document"):
        if not n:
            return call("create_note", title="Onboarding plan", kind="document",
                        body="# Onboarding plan\n\n## Week 1\n\n- Meet the team\n- **Set up** the laptop\n\n## Week 2\n\n- First project")
        return say("The document **Onboarding plan** is saved. Preview or download it below.")
    if "as a mission" in goal.lower():
        if not n:
            return call("start_mission", goal=goal.split(" as a mission")[0].strip())
        return say("I set it up as a mission. The plan will appear here for you to approve; nothing runs before that.")
    a = re.search(r"use (codex|claude code) to (.+?) in (/\S+)", goal, re.I)
    if a:
        agent = "codex" if a.group(1).lower() == "codex" else "claude"
        if n == 0:
            return call("coding_agents")
        if n == 1:
            return call("run_coding_agent", task=a.group(2).strip(), folder=a.group(3), agent=agent)
        return say("It is waiting for your approval. It runs on your computer, in that folder only.")
    if goal.lower().startswith("check in with me"):
        if not n:
            return call("create_checkin", name="Deadlines", sources="tasks,calendar", cron="0 8 * * 1-5", notify="call",
                        quiet_start="21:00", quiet_end="07:30", description="Tell me about deadlines in the next two days")
        return say("The check-in waits for your approval. Quiet hours: 21:00 to 07:30.")
    if "take your time" in goal.lower():  # a slow reply, for testing what happens when you switch away mid-answer
        time.sleep(4)
        return say("Here is the slow answer you asked for.")
    if "long answer" in goal.lower():
        para = ("Planning works best when it is light. Pick the three outcomes that matter this week, give each a first "
                "step you can do in under twenty minutes, and put those steps on your calendar. ")
        return say("\n\n".join(f"**Part {i}.** " + para * 3 for i in range(1, 9)))
    return say("Here's a simple way to plan your week:\n\n"
               "1. **Pick three outcomes** that would make the week a success.\n"
               "2. **Block time** for each one on your calendar before Monday starts.\n"
               "3. **Review on Friday**: what moved, what didn't, and why.\n\n"
               "Want me to turn these into tasks?")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _log(self, body=None):
        with open(LOG, "a") as f:
            f.write(json.dumps({"method": self.command, "path": self.path, "model": (body or {}).get("model"),
                                "auth_ok": self.headers.get("authorization") == f"Bearer {GOOD_KEY}"}) + "\n")

    def _send(self, code, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _router(self):
        return self.path.startswith("/openrouter/api/v1/")

    def _authorised(self):
        return not self._router() or self.headers.get("authorization") == f"Bearer {GOOD_KEY}"

    def do_GET(self):
        self._log()
        path = self.path.split("?")[0]
        if path == "/search":  # a stand-in SearxNG that stalls, so research stays visibly running during a test
            time.sleep(float(__import__("os").environ.get("SEARCH_DELAY", "90")))
            return self._send(200, {"results": []})
        if path == "/v1/models":
            return self._send(200, {"data": [{"id": "stand-in-model"}]})
        if path == "/openrouter/api/v1/models":
            return self._send(200, {"data": ROUTER_MODELS})
        if path == "/openrouter/api/v1/key":
            if not self._authorised():
                return self._send(401, {"error": {"message": "No auth credentials found", "code": 401}})
            return self._send(200, {"data": {"is_free_tier": True, "limit_remaining": None}})
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        self._log(body)
        if not self.path.endswith("/chat/completions"):
            return self._send(404, {"error": {"message": "not found"}})
        if not self._authorised():
            return self._send(401, {"error": {"message": "No auth credentials found", "code": 401}})
        if body.get("model") == "acme/pro":
            return self._send(402, {"error": {"message": "Insufficient credits", "code": 402}})
        if body.get("model") == "acme/notools:free" and body.get("tools"):
            return self._send(404, {"error": {"message": "No endpoints found that support tool use", "code": 404}})
        msg = reply(body)
        usage = {"prompt_tokens": 120, "completion_tokens": 40}
        if self._router():
            usage["cost"] = 0
        if not body.get("stream"):
            return self._send(200, {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": usage})
        text = msg.get("content") or ""
        chunks = [text[i:i + 24] for i in range(0, len(text), 24)] or [""]
        out = b"".join(b"data: " + json.dumps({"choices": [{"delta": {"content": c}}]}).encode() + b"\n\n" for c in chunks)
        out += b"data: " + json.dumps({"choices": [], "usage": usage}).encode() + b"\n\ndata: [DONE]\n\n"
        self._send(200, out, "text/event-stream")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
