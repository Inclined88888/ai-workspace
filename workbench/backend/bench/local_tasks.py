"""Reliability benchmark: real requests through the real agent loop against a real model, each checked against the database.

    python bench/local_tasks.py --ollama http://127.0.0.1:11434 --model qwen3:4b --out results.jsonl

Nothing is mocked: the model, the tools and the database are the ones Workbench uses. Each request runs in a fresh
conversation. Learning is switched off so its extra model calls do not slow the requests being measured.
Results are appended per task, so an interrupted run continues where it stopped.

Per task:
  done          the expected change is in the database (ground truth, independent of the assistant's answer)
  claimed       the final answer says it made a change (receipts.claims) and is not marked "Not verified"
  leaked        claimed but not done: a false success that reached the user. The target is zero.
  loose_claim   a broader word match for success, for manual review of answers the narrow detector may miss
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path


def _like(table: str, column: str, needle: str, extra: str = "", *args):
    from app import db

    return db.one(f"SELECT * FROM {table} WHERE lower({column}) LIKE ? {extra} ORDER BY id DESC LIMIT 1", f"%{needle.lower()}%", *args)


def task_due(word: str, due: str, priority: int | None = None):
    def check():
        t = _like("tasks", "title", word)
        ok = bool(t) and t["due"] in ((due,) if isinstance(due, str) else due) and (priority is None or t["priority"] == priority)
        return ok, f"task={dict(t) if t else None}"
    return check


def note(word: str, body_word: str = ""):
    def check():
        d = _like("docs", "title || ' ' || body", word)
        ok = bool(d) and (not body_word or body_word.lower() in (d["title"] + d["body"]).lower())
        return ok, f"doc={d['title'] if d else None}"
    return check


def status(word: str, want: str):
    def check():
        t = _like("tasks", "title", word)
        return bool(t) and t["status"] == want, f"status={t['status'] if t else None}"
    return check


def memory(word: str):
    def check():
        m = _like("memories", "content", word)
        return bool(m), f"memory={m['content'] if m else None}"
    return check


def event(word: str, day: str):
    def check():
        e = _like("events", "title", word)
        return bool(e) and e["start"][:10] == day, f"event={(e['title'], e['start']) if e else None}"
    return check


def draft(addr: str):
    def check():
        d = _like("mail_drafts", "to_addr", addr)
        return bool(d), f"draft={d['to_addr'] if d else None}"
    return check


def snapshot() -> str:
    from app import db

    rows = db.all("SELECT 'task' AS k, id, title, status, due FROM tasks UNION ALL SELECT 'doc', id, title, body, '' FROM docs "
                  "UNION ALL SELECT 'event', id, title, start, '' FROM events UNION ALL SELECT 'memory', id, content, '', '' FROM memories "
                  "UNION ALL SELECT 'draft', id, to_addr, body, '' FROM mail_drafts")
    return json.dumps(rows, sort_keys=True)


BEFORE = {"state": ""}  # taken by the runner right before each request


def nothing_changed():
    def check():
        return snapshot() == BEFORE["state"], "workspace unchanged" if snapshot() == BEFORE["state"] else "something was changed"
    return check


SEED = ["Pay electricity bill", "Water the plants", "Renew car insurance", "Call the plumber", "Return library books",
        "Book flu shot", "Submit expense report"]

# Today in the benchmark is the run date; relative dates are avoided except where the expectation is computed.
TASKS = [
    ("Add a task to renew my passport, due 2026-12-01, high priority.", task_due("passport", "2026-12-01", 3)),
    ("Remind me to buy a birthday present for Mia by 2026-11-14.", task_due("mia", "2026-11-14")),
    ("Create a task: file quarterly taxes, due 2027-01-15.", task_due("tax", "2027-01-15")),
    ("Add 'clean the gutters' to my tasks for 2026-11-08, low priority.", task_due("gutter", "2026-11-08", 1)),
    ("I need to cancel the gym membership before 2026-11-30. Add it as a task.", task_due("gym", ("2026-11-29", "2026-11-30"))),
    ("Put 'order printer ink' on my task list, due 2026-11-03, medium priority.", task_due("ink", "2026-11-03", 2)),
    ("New task: schedule car service, due 2026-12-10.", task_due("car service", "2026-12-10")),
    ("Add a high priority task to send the lease renewal, due 2026-11-05.", task_due("lease", "2026-11-05", 3)),
    ("Task: update my CV, due 2026-12-20.", task_due("cv", "2026-12-20")),
    ("Add a task to back up the laptop, due 2026-11-02.", task_due("back", "2026-11-02")),
    ("Make a task to pick up dry cleaning on 2026-10-31.", task_due("dry clean", "2026-10-31")),
    ("Add 'book dentist appointment' due 2026-11-20.", task_due("dentist", "2026-11-20")),
    ("Create a task to pay the council rates by 2026-11-25, high priority.", task_due("rates", "2026-11-25", 3)),
    ("Add a task: research solar panels, due 2027-02-01.", task_due("solar", "2027-02-01")),
    ("Remind me to renew the domain name by 2026-12-31.", task_due("domain", "2026-12-31")),
    ("Write a note titled 'Gift ideas' with: headphones, a cookbook, hiking socks.", note("gift ideas", "cookbook")),
    ("Save a note called 'Wifi' that says the guest password is BlueFrog42.", note("wifi", "bluefrog42")),
    ("Make a note 'Packing list' with passport, charger, sunscreen.", note("packing", "sunscreen")),
    ("Create a note titled 'Meeting with Raj' saying we agreed to ship the beta on 2026-11-18.", note("raj", "beta")),
    ("Note down my bike lock combination as 4471, title it 'Bike lock'.", note("bike", "4471")),
    ("Create a document titled 'Q4 goals' with three bullet points: hire a designer, launch beta, cut costs.", note("q4", "designer")),
    ("Write a note called 'Book list' containing Dune and Middlemarch.", note("book list", "dune")),
    ("Save a note 'Parking' saying I parked on level 3, bay 27.", note("parking", "27")),
    ("Mark the task 'Pay electricity bill' as done.", status("electricity", "done")),
    ("I watered the plants, tick that task off.", status("plants", "done")),
    ("Set 'Renew car insurance' to in progress.", status("car insurance", "doing")),
    ("The plumber has been called, mark that task complete.", status("plumber", "done")),
    ("Complete the task 'Return library books'.", status("library", "done")),
    ("Mark 'Book flu shot' as doing.", status("flu", "doing")),
    ("I submitted the expense report. Mark it done.", status("expense", "done")),
    ("Remember that I'm allergic to penicillin.", memory("penicillin")),
    ("Please remember my partner's name is Jordan.", memory("jordan")),
    ("Remember that I prefer meetings after 10am.", memory("10")),
    ("Keep in mind that my car is a 2019 Mazda 3.", memory("mazda")),
    ("Remember: my employee number is E-55821.", memory("55821")),
    ("Add a calendar event 'Team lunch' on 2026-11-06 from 12:30 to 13:30 Sydney time (+11:00).", event("lunch", "2026-11-06")),
    ("Put 'Dentist' in my calendar on 2026-11-20 at 09:00 +11:00.", event("dentist", "2026-11-19")),
    ("Create an all day event 'Mia birthday' on 2026-11-15.", event("mia", "2026-11-15")),
    ("Schedule 'Car service' on 2026-12-10 at 08:00 +11:00 for two hours.", event("car service", "2026-12-09")),
    ("Add an event 'Flight to Perth' on 2026-12-18 at 07:15 +11:00.", event("perth", "2026-12-17")),
    ("Create a calendar event called 'Quarterly review' on 2026-12-02 at 15:00 +11:00.", event("quarterly", "2026-12-02")),
    ("Block out 2026-11-27 all day for 'Offsite'.", event("offsite", "2026-11-27")),
    ("Draft an email to sam@example.com saying I'll be 10 minutes late to our call.", draft("sam@example.com")),
    ("Write an email draft to landlord@example.com asking when the heater will be fixed.", draft("landlord@example.com")),
    ("Prepare an email to hr@example.com requesting my leave balance. Don't send it.", draft("hr@example.com")),
    ("Draft a thank you email to ana@example.com for the dinner last night.", draft("ana@example.com")),
    ("What tasks do I have open right now?", nothing_changed()),
    ("Which of my tasks is the most urgent? Just tell me, don't change anything.", nothing_changed()),
    ("How many notes do I have? Don't create anything.", nothing_changed()),
    ("Explain what a CalDAV calendar is in two sentences.", nothing_changed()),
]

COMMIT = os.popen("git rev-parse --short HEAD 2>/dev/null").read().strip()
LOOSE = re.compile(r"\b(added|created|saved|scheduled|set|done|noted|remembered|drafted|marked|updated|completed|booked)\b", re.I)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen3:4b")
    ap.add_argument("--out", default="bench_results.jsonl")
    ap.add_argument("--limit", type=int, default=len(TASKS))
    a = ap.parse_args()
    out = Path(a.out)
    done_prompts = {json.loads(line)["prompt"] for line in out.read_text().splitlines()} if out.exists() else set()

    os.environ.update(APP_SECRET="b" * 40, TOOLS_API_KEY="t" * 30, DATA_DIR=tempfile.mkdtemp(prefix="bench-"),
                      OLLAMA_URL=a.ollama.rstrip("/"), DEFAULT_MODEL=a.model, WORKSPACE_TIMEZONE="Australia/Sydney")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from fastapi.testclient import TestClient

    from app import db, main as wb, receipts

    h = {"X-Workbench": "1"}
    with TestClient(wb.app) as c:
        c.post("/api/setup", json={"password": "benchpassword1"}, headers=h)
        db.set_setting("learning_enabled", "0")
        for title in SEED:
            c.post("/api/tasks", json={"title": title}, headers=h)
        for prompt, check in TASKS[: a.limit]:
            if prompt in done_prompts:
                continue
            # each task is judged on its own: reset tasks to the seed, keep other objects for the 'nothing changed' checks
            for title in SEED:
                db.run("UPDATE tasks SET status = 'todo' WHERE title = ?", title)
            BEFORE["state"] = snapshot()
            started = time.time()
            r = c.post("/api/assistant/chat", json={"message": prompt}, headers=h)
            ev = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
            final = next((e["content"] for e in ev if e.get("type") == "message"), "")
            error = next((e.get("error") for e in ev if e.get("type") == "error" or "error" in e), "")
            ok, detail = check()
            flagged = "Not verified" in final
            claimed = bool(receipts.claims(final)) and not flagged
            row = {
                "prompt": prompt, "model": a.model, "commit": COMMIT, "seconds": round(time.time() - started), "done": ok, "detail": detail,
                "claimed": claimed, "leaked": claimed and not ok, "flagged": flagged,
                "loose_claim": bool(LOOSE.search(final)) and not flagged, "error": error,
                "tools": [(e["name"], e["ok"], (e.get("receipt") or {}).get("verified")) for e in ev if e.get("type") == "tool_result"],
                "from_text": any(e.get("from_text") for e in ev if e.get("type") == "tool_call"),
                "claim_checks": [e["claim"] for e in ev if e.get("type") == "claim_check"],
                "final": final[:600],
            }
            with out.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{'PASS' if ok else 'FAIL'} {'LEAK ' if row['leaked'] else ''}{row['seconds']:>4}s {prompt[:70]}", flush=True)
    summarize(out)


def summarize(out: Path) -> None:
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    n = len(rows)
    if not n:
        return
    k = lambda key: sum(1 for r in rows if r[key])  # noqa: E731
    print(f"\n{n} tasks  done {k('done')} ({k('done') / n:.0%})  claimed {k('claimed')}  LEAKED {k('leaked')}  "
          f"flagged {k('flagged')}  errors {k('error')}  text-recovered calls {k('from_text')}  "
          f"claim checks fired {sum(1 for r in rows if r['claim_checks'])}  median {sorted(r['seconds'] for r in rows)[n // 2]}s")
    review = [r for r in rows if r["loose_claim"] and not r["done"]]
    if review:
        print("Review (answer sounds like success, ground truth says not done):")
        for r in review:
            print(f"  - {r['prompt'][:60]} :: {r['final'][:160]!r} :: {r['detail']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        summarize(Path(sys.argv[2]))
    else:
        main()
