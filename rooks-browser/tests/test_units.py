"""Pieces without a browser: policy, reply parsing, model request shapes, receipts and Workbench compatibility."""

import ast
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from rooks_browser import llm, receipts
from rooks_browser.policy import SitePolicy, classify, parse_categories

REPO = Path(__file__).resolve().parents[2]


# --- site policy ------------------------------------------------------------------------------------------------------
def test_site_policy_patterns():
    p = SitePolicy(allow=["example.com", "*.shop.test"], deny=["ads.example.com"])
    assert p.check("https://example.com/a")[0] and p.check("https://www.example.com/")[0]
    assert p.check("https://shop.test/")[0] and p.check("https://a.shop.test/")[0]
    assert not p.check("https://ads.example.com/x")[0]  # deny wins
    assert not p.check("https://other.org/")[0]
    assert not p.check("javascript:alert(1)")[0] and not p.check("ftp://example.com/")[0]
    assert p.check("about:blank")[0]
    assert SitePolicy().check("https://anything.org/")[0]
    assert not SitePolicy(deny=["*"]).check("https://anything.org/")[0]
    assert "notexample.com" and not SitePolicy(allow=["example.com"]).check("https://notexample.com/")[0]


def test_block_private_for_service_mode():
    p = SitePolicy(block_private=True)
    for u in ("http://127.0.0.1:3100/", "http://localhost/", "http://192.168.1.5/", "http://workbench:8000/", "file:///etc/passwd"):
        assert not p.check(u)[0], u
    assert p.check("https://example.org/")[0]


def test_site_lists_persist(tmp_path):
    f = tmp_path / "sites.json"
    SitePolicy(["a.com"], ["b.com"]).save(f)
    p = SitePolicy.load(f).merged(["c.com"])
    assert p.allow == ["a.com", "c.com"] and p.deny == ["b.com"]
    assert SitePolicy.load(tmp_path / "missing.json").allow == []


@pytest.mark.parametrize("action,el,args,want", [
    ("click", {"name": "Buy now", "is_submit": True, "in_form": True}, {}, "purchase"),
    ("click", {"name": "Add to cart", "role": "button"}, {}, "purchase"),
    ("click", {"name": "Place order"}, {}, "purchase"),
    ("click", {"name": "Send message", "is_submit": True, "in_form": True}, {}, "message"),
    ("click", {"name": "Post", "role": "button"}, {}, "message"),
    ("click", {"name": "Sign in", "is_submit": True, "in_form": True, "form_has_password": True}, {}, "login"),
    ("click", {"name": "Search", "is_submit": True, "in_form": True}, {}, "submit"),
    ("click", {"name": "Products", "role": "link"}, {}, None),
    ("click", {"name": "Posts archive", "role": "link"}, {}, None),
    ("type", {"name": "Password", "type": "password"}, {}, "login"),
    ("type", {"name": "Card number"}, {}, "purchase"),
    ("type", {"name": "Search", "in_form": True}, {"submit": True}, "submit"),
    ("type", {"name": "Search", "in_form": False}, {"submit": True}, None),
    ("type", {"name": "Message"}, {}, None),
    ("scroll", None, {}, None),
])
def test_classify(action, el, args, want):
    assert classify(action, el, args) == want


def test_parse_categories():
    assert parse_categories("submit, message") == {"submit", "message"}
    assert "login" not in parse_categories("all")
    with pytest.raises(ValueError):
        parse_categories("everything")


# --- model replies ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,action,args", [
    ('{"thought": "x", "action": "click", "args": {"id": 3}}', "click", {"id": 3}),
    ('```json\n{"action": "open", "args": {"url": "https://a.b"}}\n```', "open", {"url": "https://a.b"}),
    ('<think>hmm {"no": 1</think> Sure! {"action":"done","args":{"answer":"$5 {ok}"}}', "done", {"answer": "$5 {ok}"}),
    ('{"click": {"id": 4}}', "click", {"id": 4}),
    ('{"action": "type", "id": 2, "text": "hi"}', "type", {"id": 2, "text": "hi"}),
    ('{"action": {"name": "scroll", "args": {"direction": "up"}}}', "scroll", {"direction": "up"}),
    ('{"tool": "SEARCH", "arguments": {"query": "q"}}', "search", {"query": "q"}),
])
def test_parse_action(text, action, args):
    a = llm.parse_action(text)
    assert a["action"] == action and a["args"] == args


def test_parse_action_rejects_prose():
    with pytest.raises(ValueError):
        llm.parse_action("I will click the button")


def test_resolve_and_vision(monkeypatch):
    assert llm.resolve(None) == "ollama"
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert llm.resolve("auto") == "openai"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert llm.resolve(None) == "anthropic"
    with pytest.raises(ValueError):
        llm.resolve("nope")
    assert llm.sees("anthropic", "claude-sonnet-4-5") and llm.sees("openai", "gpt-4.1-mini")
    assert not llm.sees("ollama", "qwen2.5:7b") and llm.sees("ollama", "qwen2.5vl:7b") and llm.sees("ollama", "llava")
    assert llm.sees("ollama", "qwen2.5:7b", "on") and not llm.sees("openai", "gpt-4o", "off")


class Recorder(BaseHTTPRequestHandler):
    seen: list = []
    reply: dict = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Recorder.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        data = json.dumps(Recorder.reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_api():
    Recorder.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_openai_shape_with_image(fake_api):
    Recorder.reply = {"choices": [{"message": {"content": '{"action":"done"}'}}], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}
    eg = []
    m = llm.make("openai", "gpt-4.1-mini", base_url=fake_api + "/v1", api_key="sk-test", on_egress=lambda *a: eg.append(a))
    r = m.complete("sys", "page", "AAAA")
    path, headers, body = Recorder.seen[0]
    assert path == "/v1/chat/completions" and headers["authorization"] == "Bearer sk-test"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"
    assert body["response_format"] == {"type": "json_object"}
    assert (r.tokens_in, r.tokens_out) == (11, 3) and eg[0][0] == "POST" and eg[0][2] == "ok"


def test_anthropic_shape(fake_api):
    Recorder.reply = {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 5, "output_tokens": 2}}
    m = llm.make("anthropic", "claude-sonnet-4-5", base_url=fake_api, api_key="k")
    m.complete("sys", "page", "BBBB")
    path, headers, body = Recorder.seen[0]
    assert path == "/v1/messages" and headers["x-api-key"] == "k" and body["system"] == "sys"
    assert body["messages"][0]["content"][0]["source"]["data"] == "BBBB"


def test_ollama_and_openrouter_shapes(fake_api):
    Recorder.reply = {"message": {"content": "{}"}, "prompt_eval_count": 9, "eval_count": 1}
    r = llm.make("ollama", "qwen2.5:7b", base_url=fake_api).complete("s", "u", None)
    path, _, body = Recorder.seen[0]
    assert path == "/api/chat" and body["format"] == "json" and body["stream"] is False and "images" not in body["messages"][1]
    assert r.tokens_in == 9
    Recorder.reply = {"choices": [{"message": {"content": "{}"}}]}
    llm.make("openrouter", "deepseek/deepseek-chat", base_url=fake_api + "/api/v1", api_key="or").complete("s", "u")
    path, headers, body = Recorder.seen[1]
    assert path == "/api/v1/chat/completions" and "response_format" not in body and headers["x-title"] == "Rooks Browser"


def test_missing_keys_are_clear_errors():
    for p in ("openai", "anthropic", "openrouter", "workbench", "openai-compatible"):
        with pytest.raises(ValueError):
            llm.make(p, "m")


# --- receipts ---------------------------------------------------------------------------------------------------------
def test_redaction_is_a_verbatim_copy_of_workbench():
    ours = (REPO / "rooks-browser/rooks_browser/redact.py").read_text().split("\n", 1)[1]
    assert ours == (REPO / "workbench/backend/app/redact.py").read_text()


def test_egress_rows_match_workbench_audit(tmp_path):
    log = receipts.RunLog(tmp_path)
    log.egress("GET", "https://shop.example/cart?session=abc123&token=zzz", "ok", 42)
    log.egress("GET", "http://127.0.0.1:3100/x", "ok", 1)
    rows = receipts.read(log.path)
    assert rows[0]["args"] == "GET https://shop.example/cart" and rows[0]["effect"] == "external" and rows[0]["provider"] == "shop.example"
    assert rows[1]["effect"] == "local" and rows[1]["kind"] == "egress"
    assert log.visited() == ["https://shop.example/cart", "http://127.0.0.1:3100/x"]


def test_receipt_text_is_redacted(tmp_path):
    log = receipts.RunLog(tmp_path)
    log.add("tool_call", "ok", args='{"text": "my key is sk-ant-abcdefghijklmnopqrstuvwxyz", "email": "sam@example.com"}')
    text = log.path.read_text()
    assert "sk-ant-abc" not in text and "sam@example.com" not in text


def _workbench_schema() -> str:
    tree = ast.parse((REPO / "workbench/backend/app/db.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SCHEMA" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("SCHEMA not found in workbench db.py")


def test_import_into_workbench_and_live_write(tmp_path):
    db = tmp_path / "workbench.db"
    con = sqlite3.connect(db)
    con.executescript(_workbench_schema())
    con.close()
    log = receipts.RunLog(tmp_path / "home")
    turn = log.add("turn", "running", args="find a price")
    log.egress("GET", "https://example.com/a", "ok", 3, parent=turn)
    tc = log.add("tool_call", "running", turn, tool="click", effect="external")
    log.add("decision", "denied", tc)
    log.finish(tc, "denied")
    log.finish(turn, "no_claim")
    assert receipts.import_into(db, [log.path]) == 4
    assert receipts.import_into(db, [log.path]) == 0  # idempotent
    con = sqlite3.connect(db)
    rows = con.execute("SELECT kind, outcome, channel, parent_action_id FROM actions ORDER BY id").fetchall()
    assert [r[:2] for r in rows] == [("turn", "no_claim"), ("egress", "ok"), ("tool_call", "denied"), ("decision", "denied")]
    assert all(r[2] == "rooks-browser" for r in rows) and rows[3][3] is not None
    # live mode writes the same rows as they happen
    live = receipts.RunLog(tmp_path / "home2", workbench_db=db)
    t = live.add("turn", "running", args="live")
    live.egress("GET", "https://example.com/b", "ok", 1, parent=t)
    live.finish(t, "backed")
    assert con.execute("SELECT outcome FROM actions WHERE args = 'live'").fetchone() == ("backed",)
    # Workbench's egress summary query sees the browser's visits
    assert con.execute("SELECT COUNT(*) FROM actions WHERE kind = 'egress' AND effect = 'external'").fetchone()[0] == 2
