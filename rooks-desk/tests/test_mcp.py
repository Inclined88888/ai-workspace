import io
import json
import threading
import time

from rooks_desk import llm, mcp_server
from rooks_desk.backends.fake import FakeBackend


class Pipe:
    """Feeds lines to the server and lets the test answer elicitation requests."""

    def __init__(self):
        self.lines = []
        self.cv = threading.Condition()
        self.closed = False

    def __iter__(self):
        while True:
            with self.cv:
                while not self.lines and not self.closed:
                    self.cv.wait()
                if not self.lines:
                    return
                line = self.lines.pop(0)
            yield line

    def push(self, msg):
        with self.cv:
            self.lines.append(json.dumps(msg) + "\n")
            self.cv.notify()

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify()


class Out(io.StringIO):
    def __init__(self, on_msg):
        super().__init__()
        self.on_msg = on_msg

    def write(self, s):
        for line in s.splitlines():
            if line.strip():
                self.on_msg(json.loads(line))
        return len(s)


def session(elicit_answer=None):
    inp = Pipe()
    got = {}
    asked = []

    def on_msg(m):
        if m.get("method") == "elicitation/create":
            asked.append(m["params"]["message"])
            inp.push({"jsonrpc": "2.0", "id": m["id"], "result": {"action": "accept", "content": {"approve": bool(elicit_answer)}}})
        elif "id" in m:
            got[m["id"]] = m

    b = FakeBackend()
    srv = mcp_server.Server(inp, Out(on_msg), backend=b,
                            model_factory=lambda s: llm.ScriptedModel([{"action": "open_app", "args": {"name": "Text Editor"}},
                                                                       {"action": "done", "args": {"answer": "opened"}}]))
    threading.Thread(target=srv.serve, daemon=True).start()
    caps = {"elicitation": {}} if elicit_answer is not None else {}
    n = iter(range(1, 1000))

    def call(method, params=None):
        i = next(n)
        inp.push({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}})
        end = time.time() + 20
        while i not in got and time.time() < end:
            time.sleep(0.02)
        return got[i].get("result") or got[i].get("error")

    call("initialize", {"protocolVersion": "2025-06-18", "capabilities": caps})
    return call, b, asked, inp


def tool(call, _tool, **args):
    r = call("tools/call", {"name": _tool, "arguments": args})
    return r, "\n".join(c.get("text", "") for c in r["content"] if c["type"] == "text")


def test_low_level_tools_and_policy():
    call, b, asked, inp = session(elicit_answer=None)
    names = {t["name"] for t in call("tools/list")["tools"]}
    assert {"use_computer", "screenshot", "click", "type_text", "run_shell", "elements"} <= names
    tool(call, "open_app", name="Mail")
    r, text = tool(call, "screenshot")
    assert r["content"][0]["type"] == "image" and "'Send'" in text
    send_id = int(text.split("'Send'")[0].rsplit("[", 1)[1].split("]")[0])
    r, text = tool(call, "click", id=send_id)
    assert r["isError"] and "cannot ask" in text and b.sent == []  # no elicitation: sensitive steps refused
    r, text = tool(call, "run_shell", command="pwd")
    assert not r["isError"] and "exit code 0" in text  # read-only command
    r, text = tool(call, "run_shell", command="echo hi > /tmp/rooks-desk-mcp-test")
    assert r["isError"]
    inp.close()


def test_elicitation_approves_and_use_computer():
    call, b, asked, inp = session(elicit_answer=True)
    r, text = tool(call, "run_shell", command="echo approved-run && touch made.txt")
    assert not r["isError"] and "approved-run" in text and asked and "touch made.txt" in asked[0]
    r, text = tool(call, "use_computer", goal="open the text editor")
    d = json.loads(text)
    assert d["success"] and d["final"] == "opened" and "Text Editor" in b.apps
    inp.close()
