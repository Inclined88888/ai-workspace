// Runs against the compiled client (npm test builds first). A local server stands in for AI Workspace.
import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { buildMessage, chat, describe, SseParser } from "../out/client.js";

test("SSE parser handles split chunks, CRLF and junk", () => {
  const p = new SseParser();
  assert.deepEqual(p.push('data: {"type":"st'), []);
  assert.deepEqual(p.push('art","conversation_id":7}\r\n\r\ndata: nope\n\ndata: {"type":"done"}\n\n'),
    [{ type: "start", conversation_id: 7 }, { type: "done" }]);
});

test("selection is fenced, labelled and capped", () => {
  const m = buildMessage("why?", { selection: "x = 1", fileName: "a.py", languageId: "python" });
  assert.equal(m, "why?\n\nCode from a.py:\n```python\nx = 1\n```");
  assert.match(buildMessage("q", { selection: "a```b" }), /````\na```b\n````/);
  assert.match(buildMessage("q", { selection: "y".repeat(50), maxChars: 10 }), /cut to the first 10 characters/);
  assert.equal(buildMessage("  plain  "), "plain");
});

function server(handler) {
  return new Promise((resolve) => {
    const s = http.createServer(handler).listen(0, "127.0.0.1", () => resolve(s));
  });
}

test("a turn streams events, keeps the conversation and sends auth + CSRF headers", async () => {
  let seen;
  const s = await server((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen = { auth: req.headers.authorization, xw: req.headers["x-workbench"], path: req.url, body: JSON.parse(body) };
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write('data: {"type":"start","conversation_id":42}\n\n');
      res.write('data: {"type":"tool_call","name":"read_file","args":"{}"}\n\n');
      setTimeout(() => {
        res.write('data: {"type":"approval_required","id":9,"tool":"send_email"}\n\n');
        res.end('data: {"type":"message","content":"Done."}\n\ndata: {"type":"done","conversation_id":42,"cost_cents":0,"tool_calls":1}\n\n');
      }, 20);
    });
  });
  const url = `http://127.0.0.1:${s.address().port}`;
  const lines = [];
  const r = await chat({ url: url + "/", token: "tok", message: "hi", conversationId: 5, onEvent: (e) => lines.push(describe(e, url)) });
  s.close();
  assert.equal(seen.auth, "Bearer tok");
  assert.equal(seen.xw, "1");
  assert.equal(seen.path, "/api/assistant/chat");
  assert.deepEqual(seen.body, { message: "hi", channel: "cli", conversation_id: 5 });
  assert.equal(r.answer, "Done.");
  assert.equal(r.conversationId, 42);
  assert.ok(lines.includes("  -> read file"));
  assert.ok(lines.some((l) => l.includes("Waiting for your OK") && l.endsWith("/assistant")));
});

test("a rejected token says how to fix it", async () => {
  const s = await server((req, res) => { res.writeHead(401, { "content-type": "application/json" }); res.end('{"detail":"Not signed in"}'); });
  await assert.rejects(chat({ url: `http://127.0.0.1:${s.address().port}`, token: "bad", message: "hi" }),
    (e) => e.status === 401 && /Terminal access/.test(e.message));
  s.close();
});

test("a stream that only errors rejects with the (server-redacted) error", async () => {
  const s = await server((req, res) => { res.writeHead(200); res.end('data: {"error":"model unavailable"}\n\n'); });
  await assert.rejects(chat({ url: `http://127.0.0.1:${s.address().port}`, token: "t", message: "hi" }), /model unavailable/);
  s.close();
});
