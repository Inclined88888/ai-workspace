"""Used by check_upgrade_from.sh: `seed` writes data with an older ROOKS; `check` opens it with this one."""
import os
import sys

os.environ.update(APP_SECRET="a" * 40, TOOLS_API_KEY="k" * 30, DATA_DIR=sys.argv[2])
sys.path.insert(0, sys.argv[1])
from app import db  # noqa: E402

db.init()
if sys.argv[3] == "seed":
    cid = db.run("INSERT INTO conversations(title, channel, created_at, updated_at) VALUES ('Old chat', 'web', ?, ?)", db.now(), db.now())
    db.run("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, 'user', 'kept message', ?)", cid, db.now())
    db.run("INSERT INTO research(question, model, status, created_at, updated_at) VALUES ('Old research', 'm', 'running', ?, ?)", db.now(), db.now())
    db.run("INSERT INTO approvals(tool, args, reason, channel, created_at) VALUES ('create_task', '{}', 'old', 'web', ?)", db.now())
    print("seeded with the older version")
    sys.exit(0)
fails = []
if not db.one("SELECT 1 FROM messages WHERE content = 'kept message'"):
    fails.append("a message was lost")
rid = db.one("SELECT id FROM research WHERE question = 'Old research'")["id"]
db.run("UPDATE research SET status = 'done' WHERE id = ?", rid)
if [(r["old_status"], r["new_status"]) for r in db.all("SELECT old_status, new_status FROM work_events WHERE kind = 'research' AND ref_id = ?", rid)] != [("running", "done")]:
    fails.append("a status change on an old row was not recorded")
from app import work  # noqa: E402

if work.get(f"research:{rid}").phase != work.Phase.COMPLETED:
    fails.append("an old row has the wrong phase")
if not db.one("SELECT 1 FROM approvals WHERE reason = 'old' AND scope = ''"):
    fails.append("an old approval did not keep its row")
db.init()  # starting twice changes nothing
print("upgrade check:", "OK" if not fails else "FAILED: " + "; ".join(fails))
sys.exit(1 if fails else 0)
