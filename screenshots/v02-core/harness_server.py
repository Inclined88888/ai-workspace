"""Screenshot harness for feat/v02-core: the real app + a scripted fake model (no network, no API key)."""
import json, os, re, sys, threading, shutil
W = "/workspace/ai-workspace-v02a/workbench"
sys.path[:0] = [W + "/backend"]
shutil.rmtree("/tmp/v02shot/data", ignore_errors=True)
os.environ.update(APP_SECRET="a" * 40, TOOLS_API_KEY="k" * 30, DATA_DIR="/tmp/v02shot/data", STATIC_DIR=W + "/frontend/dist")
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import uvicorn

fake = FastAPI()

def tc(name, args, i="c1"):
    return {"id": i, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

ANSWER = ("Two things are still open on the Smith St job: order the bathroom tiles (due 6 November) and book the plumber for the rough-in. "
          "The tile quote from Coastal Tiles was $4,280. Jane Lee has already signed off the kitchen plans.")

def reply(body):
    msgs = body["messages"]
    sys_p = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    if "split an assistant's answer" in sys_p:
        payload = msgs[-1]["content"]
        ids = re.findall(r"^\[(S\d+)\]", payload, re.M)
        sents = [s.strip() for s in re.split(r"(?<=\.)\s+", ANSWER)]
        return {"content": json.dumps({"claims": [{"text": sents[0], "sources": ids[:2]}, {"text": sents[1], "sources": ids[2:3] or ids[:1]},
                                                  {"text": sents[2], "sources": []}]})}
    if "re-check claims" in sys_p:
        return {"content": json.dumps({"checks": [
            {"i": 0, "verdict": "supported", "source": "S1", "quote": "Order bathroom tiles", "note": "Both tasks are open in the task list."},
            {"i": 1, "verdict": "supported", "source": "S3", "quote": "Tile quote from Coastal Tiles", "note": "The quote note mentions it."},
            {"i": 2, "verdict": "unsupported", "note": "Nothing looked up says Jane signed off the kitchen plans."}]})}
    if "long term memory" in sys_p or "reusable skill" in sys_p:
        return {"content": "[]"}
    last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
    q = msgs[last_user]["content"] if isinstance(msgs[last_user]["content"], str) else ""
    tools_done = [m for m in msgs[last_user:] if m["role"] == "tool"]
    if "outstanding" in q:
        if not tools_done:
            return {"content": "", "tool_calls": [tc("list_tasks", {"project": ""}, "a1"), tc("search_notes", {"query": "Smith St tiles"}, "a2")]}
        return {"content": ANSWER}
    if "plumber" in q:
        if not tools_done:
            return {"content": "", "tool_calls": [tc("create_task", {"title": "Call Dave the plumber about the rough-in", "due": "2026-11-03"}, "b1"),
                                                  tc("create_event", {"title": "Site meeting at 12 Smith St", "start": "2026-11-03T09:00:00+10:00",
                                                                      "end": "2026-11-03T10:00:00+10:00", "location": "12 Smith St"}, "b2"),
                                                  tc("remember", {"content": "Jane Lee prefers a text over a phone call", "kind": "preference"}, "b3")]}
        return {"content": "Done. I added a task to call Dave on Tuesday, put the site meeting in your calendar for 9am, and noted that Jane prefers texts."}
    return {"content": "Sure."}

@fake.post("/v1/chat/completions")
async def completions(req: Request):
    body = await req.json()
    msg = {"role": "assistant", **reply(body)}
    if body.get("stream"):
        def gen():
            d = {"content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                d["tool_calls"] = [{"index": i, **t} for i, t in enumerate(msg["tool_calls"])]
            yield "data: " + json.dumps({"choices": [{"delta": d}]}) + "\n\n"
            yield "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 60}}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 900, "completion_tokens": 60}}

@fake.get("/v1/models")
def models():
    return {"data": [{"id": "demo-model"}]}

threading.Thread(target=lambda: uvicorn.run(fake, host="127.0.0.1", port=8942, log_level="warning"), daemon=True).start()

from app import main, history, features_v02

# Harness-only switches: pretend to be on the free plan, and add one sent email (an approved send) to History.
FREE = "/tmp/v02shot/free"
_orig = features_v02._account_allowed
features_v02._account_allowed = lambda f: (not os.path.exists(FREE)) and _orig(f)

@main.app.post("/shot/seed-email")
def seed_email():
    d = history.describe("send_email", {"to": "jane.lee@example.com", "subject": "Tiles for Smith St"}, {"sent": True}, {})
    return {"id": history.record(d["category"], d["title"], tool="send_email", undo_kind="cannot", fix=d["fix"], fix_prompt=d["fix_prompt"])}

uvicorn.run(main.app, host="127.0.0.1", port=8940, log_level="warning")
