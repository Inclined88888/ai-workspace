"""Fake model for the Computer Use end-to-end run (port 8896). It plays the planner: each scenario is a short program
that reads the latest tool result (observations, snapshots, listings) and picks element ids from it, as a model would.
It never sees coordinates."""
import json, os, re, time, uvicorn
from fastapi import FastAPI, Request
import base64, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from rooks_companion import imaging  # the stand-in "vision model" really decodes the screenshot it is sent
app = FastAPI()
FILES = os.environ["CU_FILES"]
LONG = "The quick brown fox jumps over the lazy dog. " * 70   # about 3,150 characters, for the Stop test
REC = "Recovered line " + "abcdefghij" * 30                     # about 315 characters, for the restart test

def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{time.monotonic_ns()}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}
def say(text): return {"role": "assistant", "content": text}
def find(text, label, prefix="e"):
    m = re.search(r"\[(" + prefix + r"\d+)\] [^\n]*?\"" + re.escape(label) + r"\"", text or "")
    return m.group(1) if m else None

def program(goal, results):
    """results: tool result texts of this turn so far. Returns the next message."""
    n, last = len(results), (results[-1] if results else "")
    F = "ROOKS Fixture"
    if "TEST-V1" in goal:  # accessibility cannot see the red button: find it visually, click, verify
        steps = [lambda: call("computer_observe", app=F),
                 lambda: call("computer_locate", app=F, description="the red square button"),
                 lambda: call("computer_click_visual", target=json.loads(last)["target"], app=F),
                 lambda: call("computer_observe", app=F),
                 lambda: say("Clicked the red button. " + (re.search(r"hidden=\d+", last).group(0) if re.search(r"hidden=\d+", last) else "?"))]
    elif "TEST-T1" in goal:  # terminal: write code and a check, run it, see it fail, fix it, run it again
        steps = [lambda: call("workspace_write", path="calc/calc.py", content="def add(a, b):\n    return a - b\n"),
                 lambda: call("workspace_write", path="calc/check.py", content="from calc import add\nassert add(2, 3) == 5, f'add(2, 3) gave {add(2, 3)}'\nprint('check passed')\n"),
                 lambda: call("run_command", command="python3 check.py", cwd="calc"),
                 lambda: call("workspace_edit", path="calc/calc.py", old_text="return a - b", new_text="return a + b"),
                 lambda: call("run_command", command="python3 check.py", cwd="calc"),
                 lambda: say("Fixed and verified: check.py passes." if "check passed" in last else "Still failing: " + last[:200])]
    elif "TEST-T2" in goal:  # terminal: a command that fails
        steps = [lambda: call("run_command", command="ls no-such-folder"), lambda: say("The command failed: " + last.splitlines()[1][:120])]
    elif "TEST-T3" in goal:  # terminal: needs approval in the default ask mode
        steps = [lambda: call("run_command", command="touch from-approval.txt"), lambda: say("That command is waiting for your approval.")]
    elif "TEST-V2" in goal:  # locate only; the test moves the window before clicking
        steps = [lambda: call("computer_locate", app=F, description="the red square button"), lambda: say("located " + last[:200])]
    elif "TEST-V3" in goal:
        steps = [lambda: call("computer_click_visual", target=goal.split()[-1], app=F), lambda: say("tried")]
    elif "TEST-V4" in goal:  # nothing red asked for: the stand-in cannot find it and must say so
        steps = [lambda: call("computer_locate", app=F, description="the purple triangle"), lambda: say("vision said: " + last[:120])]
    elif "TEST-A" in goal:
        steps = [lambda: call("computer_observe", app=F),
                 lambda: call("computer_click", target=find(last, "Add one"), label="Add one", app=F),
                 lambda: call("computer_observe", app=F),
                 lambda: say("Clicked Add one. " + (re.search(r"count=\d+", last).group(0) if re.search(r"count=\d+", last) else "count not visible"))]
    elif "TEST-B" in goal:
        steps = [lambda: call("files_list", path=FILES), lambda: call("files_mkdir", path=f"{FILES}/Invoices"),
                 lambda: call("files_mkdir", path=f"{FILES}/Receipts")]
        moves = [("invoice-1.txt", "Invoices"), ("invoice-2.txt", "Invoices"), ("receipt-1.txt", "Receipts")]
        steps += [lambda s=s, d=d: call("files_move", src=f"{FILES}/{s}", dst=f"{FILES}/{d}") for s, d in moves]
        steps += [lambda: call("files_list", path=f"{FILES}/Invoices"), lambda: say("Organized: " + last[:200])]
    elif "TEST-C2" in goal or "check the page" in goal:
        steps = [lambda: call("browser_snapshot"), lambda: say("Page says: " + (re.search(r"Thanks, [^\n\"]*", last).group(0) if "Thanks" in last else "no thanks"))]
    elif "TEST-C" in goal:
        steps = [lambda: call("browser_open", url="http://127.0.0.1:8897/"), lambda: call("browser_snapshot"),
                 lambda: call("browser_click", ref=find(last, "Contact form", "r"), label="Contact form"), lambda: call("browser_snapshot"),
                 lambda: call("browser_fill", ref=find(last, "Name", "r"), text="Ada Lovelace", label="Name"),
                 lambda: call("browser_fill", ref=find(results[3], "Email", "r"), text="ada@example.com", label="Email"),
                 lambda: call("browser_fill", ref=find(results[3], "Password", "r"), text="secret", label="Password"),
                 lambda: call("browser_click", ref=find(results[3], "Submit", "r"), label="Submit"),
                 lambda: say("Waiting for approval to submit.")]
    elif "TEST-D" in goal:
        steps = [lambda: call("computer_observe", app=F), lambda: call("computer_type", target=find(last, "Document"), label="Document", text="Quarterly total: 1250", app=F),
                 lambda: call("computer_key", keys="ctrl+s", app=F), lambda: call("computer_observe", app=F), lambda: say("Saved.")]
    elif "TEST-E" in goal:
        steps = [lambda: call("computer_observe", app=F), lambda: call("computer_type", target=find(last, "Document"), label="Document", text=LONG, app=F),
                 lambda: call("computer_key", keys="ctrl+s", app=F), lambda: say("done")]
    elif "TEST-F1" in goal:
        steps = [lambda: call("computer_observe", app=F), lambda: say("blocked: " + last[:150])]
    elif "TEST-F2" in goal:  # claims the Delete all button is "Add one"
        steps = [lambda: call("computer_observe", app=F), lambda: call("computer_click", target=find(last, "Delete all"), label="Add one", app=F), lambda: say("tried")]
    elif "TEST-F3" in goal:  # honest label: needs approval, which the test denies
        steps = [lambda: call("computer_observe", app=F), lambda: call("computer_click", target=find(last, "Delete all"), label="Delete all", app=F), lambda: say("waiting")]
    elif "TEST-G1" in goal:
        steps = [lambda: call("computer_observe", app=F), lambda: call("computer_type", target=find(last, "Document"), label="Document", text=REC, app=F), lambda: say("typed")]
    elif "TEST-G2" in goal:  # after the restart: try the same thing again
        steps = [lambda: call("computer_type", target="e3", label="Document", text=REC, app=F),
                 lambda: call("computer_observe", app=F),
                 lambda: say("Already there, not typing again." if REC[:40] in last else "Not there; would type again.")]
    else:
        return say("ok")
    return steps[n]() if n < len(steps) else say("finished")

def see(msgs):
    """A stand-in vision model: it reads the pixels it was sent. It finds red regions; it is not intelligent."""
    m = next(m for m in msgs if isinstance(m.get("content"), list))
    text = " ".join(p.get("text", "") for p in m["content"] if p.get("type") == "text")
    url = next(p["image_url"]["url"] for p in m["content"] if p.get("type") == "image_url")
    png = base64.b64decode(url.split(",", 1)[1])
    if os.environ.get("CU_CAPTURE_OUT") and "LEFT half" not in text:
        open(os.environ["CU_CAPTURE_OUT"], "wb").write(png)
    w, h, ch, px = imaging.decode(png)
    def rgb(x, y):
        o = (y * w + x) * ch
        return px[o], px[o + 1], px[o + 2]
    if "LEFT half" in text:
        r, g, b_ = rgb(w // 4, h // 2)
        return "Red" if r > 200 and g < 60 else "Blue" if b_ > 200 else "Unknown"
    xs, ys = [], []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b_ = rgb(x, y)
            if r > 200 and g < 60 and b_ < 60:
                xs.append(x); ys.append(y)
    if "red" not in text.lower() or not xs:
        return json.dumps({"found": False})
    return json.dumps({"found": True, "x": (sum(xs) / len(xs)) / w, "y": (sum(ys) / len(ys)) / h, "confidence": 0.95, "label": "red square"})

@app.post("/v1/chat/completions")
async def chat(req: Request):
    b = await req.json(); msgs = b["messages"]
    if any(isinstance(m.get("content"), list) for m in msgs):
        return {"choices": [{"message": say(see(msgs))}]}
    system = msgs[0]["content"] if msgs[0]["role"] == "system" else ""
    if "long term memory" in system: return {"choices": [{"message": say("[]")}]}
    if "reusable skill" in system: return {"choices": [{"message": say('{"save": false}')}]}
    if system.startswith("You plan missions"):
        return {"choices": [{"message": say(json.dumps({"steps": [
            {"title": "List the inbox folder", "done_when": "The files are listed", "needs_tool": True},
            {"title": "Move the contract into Contracts", "done_when": "contract.txt is inside Contracts", "needs_tool": True}]}))}]}
    if system.startswith("You check whether"):
        ok = "OK" in msgs[1]["content"]
        return {"choices": [{"message": say(json.dumps({"verdict": "done" if ok else "not_done", "evidence": "tool result shows it", "missing": ""}))}]}
    if system.startswith("Write the final answer"):
        return {"choices": [{"message": say("The contract is filed in Contracts.")}]}
    last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
    goal = msgs[last_user]["content"]
    results = [m["content"] for m in msgs[last_user:] if m["role"] == "tool"]
    if "Now do step" in goal:
        step = goal.split("Now do step ")[1].split("\n")[0]
        if "customers.csv and report" in step:  # template: spreadsheet cleanup
            seq = [lambda: call("sheet_read", path=f"{FILES}/customers.csv"), lambda: say("Read it. OK")]
        elif "without duplicate rows" in step:
            seq = [lambda: call("sheet_dedupe", path=f"{FILES}/customers.csv", columns=["Email"], output=f"{FILES}/customers-clean.csv"),
                   lambda: say("Saved the cleaned copy. OK")]
        elif "Read the new file" in step:
            seq = [lambda: call("sheet_read", path=f"{FILES}/customers-clean.csv"),
                   lambda: say("No duplicates left. OK" if '"duplicate_rows": []' in results[-1] else "Still has duplicates")]
        elif "List the inbox" in step:
            seq = [lambda: call("files_list", path=f"{FILES}/inbox"), lambda: say("Listed. OK")]
        else:
            seq = [lambda: call("files_mkdir", path=f"{FILES}/Contracts"), lambda: call("files_move", src=f"{FILES}/inbox/contract.txt", dst=f"{FILES}/Contracts"),
                   lambda: say("Moved. OK")]
        n = len(results)
        return {"choices": [{"message": seq[n]() if n < len(seq) else say("OK")}]}
    return {"choices": [{"message": program(goal, results)}]}

@app.get("/v1/models")
def models(): return {"data": [{"id": "fake-model"}]}
uvicorn.run(app, host="127.0.0.1", port=8896, log_level="error")
