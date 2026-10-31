"""Four offline fake websites of different kinds, to prove the Sites engine needs no per-site code.

* /jobs     a field-service job tracker: a table, detail pages, a status form, an attachment
* /roster   a rostering app behind a sign-in: shift cards with "Label: value" lines
* /project  a construction-style project portal: task list items with classed spans and <time>, plus an RFI table
* /lab      an unfamiliar CRUD app (lab samples) with unusual column names

STATE is mutable so tests can change the sites between checks.
"""

import copy
import html
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

INITIAL = {
    "jobs": [
        {"no": "J-1001", "customer": "Smith Bakery", "desc": "Replace hot water system", "status": "Scheduled", "scheduled": "2026-11-02", "due": "2026-11-03", "tech": "Alex"},
        {"no": "J-1002", "customer": "Lee Dental", "desc": "Quote for split system", "status": "Quote", "scheduled": "2026-10-20", "due": "2026-10-25", "tech": "Sam"},
        {"no": "J-1003", "customer": "Park Café", "desc": "Fix leaking tap", "status": "Complete", "scheduled": "2026-10-01", "due": "2026-10-02", "tech": "Alex"},
    ],
    "shifts": [
        {"date": "Mon 2 Nov 2026", "start": "07:00", "end": "15:30", "role": "Site supervisor", "location": "Kangaroo Point"},
        {"date": "Tue 3 Nov 2026", "start": "06:30", "end": "14:30", "role": "Labourer", "location": "Woolloongabba"},
        {"date": "Thu 5 Nov 2026", "start": "07:00", "end": "15:30", "role": "Site supervisor", "location": "Kangaroo Point"},
    ],
    "tasks": [
        {"name": "Pour level 3 slab", "status": "In progress", "start": "2026-10-26", "end": "2026-11-06", "owner": "Concrete crew"},
        {"name": "Frame inspection", "status": "Not started", "start": "2026-11-09", "end": "2026-11-09", "owner": "Certifier"},
        {"name": "Order windows", "status": "Late", "start": "2026-10-05", "end": "2026-10-16", "owner": "Procurement"},
    ],
    "rfis": [
        {"id": "RFI-07", "subject": "Beam size at grid C4", "asked": "2026-10-21", "reply_by": "2026-10-28", "state": "Open"},
        {"id": "RFI-08", "subject": "Balustrade finish", "asked": "2026-10-27", "reply_by": "2026-11-10", "state": "Open"},
    ],
    "samples": [
        {"sid": "S-501", "collected": "2026-10-19", "analyst": "Dr Nguyen", "result": "Pending", "review": "2026-10-26"},
        {"sid": "S-502", "collected": "2026-10-22", "analyst": "Dr Patel", "result": "Pass", "review": "2026-10-29"},
        {"sid": "S-503", "collected": "2026-10-28", "analyst": "Dr Nguyen", "result": "Pending", "review": "2026-11-04"},
    ],
}
STATE = copy.deepcopy(INITIAL)
SUBMITS: list[dict] = []
COOKIE = "session=signed-in-in-the-browser-profile"


def reset():
    STATE.clear()
    STATE.update(copy.deepcopy(INITIAL))
    SUBMITS.clear()


def page(title, body, nav=""):
    return f"""<!doctype html><html><head><title>{title}</title></head><body>
<header><nav class="menu">{nav}<a href="/logout">Log out</a></nav></header><main>{body}</main></body></html>"""


E = html.escape


def jobs_nav():
    return '<a href="/jobs">Jobs</a> <a href="/jobs/customers">Customers</a>'


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="text/html; charset=utf-8", code=200, headers=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlsplit(self.path)
        p = u.path.rstrip("/") or "/"
        if p == "/logout":
            STATE["logged_out"] = True
            return self.send(page("Bye", "<p>Logged out</p>"))
        # job tracker
        if p == "/jobs":
            rows = "".join(f"<tr><td><a href='/jobs/{j['no']}'>{j['no']}</a></td><td>{E(j['customer'])}</td><td>{E(j['desc'])}</td>"
                           f"<td>{j['status']}</td><td>{j['scheduled']}</td><td>{j['due']}</td><td>{j['tech']}</td>"
                           f"<td><a href='/jobs/{j['no']}/delete'>Delete</a></td></tr>" for j in STATE["jobs"])
            return self.send(page("Jobs · TradeTrack", f"<h1>Jobs</h1><table><thead><tr><th>Job #</th><th>Customer</th><th>Description</th>"
                                  f"<th>Status</th><th>Scheduled</th><th>Due date</th><th>Technician</th><th></th></tr></thead><tbody>{rows}</tbody></table>"
                                  "<p><a href='/files/materials.csv'>Materials list (CSV)</a></p>", jobs_nav()))
        if p.startswith("/jobs/J-") and p.endswith("/delete"):
            STATE["deleted"] = True
            return self.send(page("Deleted", "deleted"))
        if p.startswith("/jobs/J-"):
            j = next((x for x in STATE["jobs"] if x["no"] == p.split("/")[2]), None)
            if not j:
                return self.send("nope", code=404)
            opts = "".join(f"<option{' selected' if s == j['status'] else ''}>{s}</option>" for s in ("Quote", "Scheduled", "In progress", "Complete"))
            return self.send(page(f"{j['no']} · TradeTrack", f"<h1>{j['no']}</h1><dl><dt>Customer</dt><dd>{E(j['customer'])}</dd><dt>Address</dt>"
                                  f"<dd>12 {E(j['customer'].split()[0])} St, Brisbane</dd><dt>Status</dt><dd>{j['status']}</dd></dl>"
                                  f"<h2>Update status</h2><form method='post' action='/jobs/{j['no']}/status'><label for='st'>New status</label>"
                                  f"<select id='st' name='status'>{opts}</select><label for='n'>Note</label><textarea id='n' name='note'></textarea>"
                                  f"<button type='submit'>Save status</button></form>", jobs_nav()))
        if p == "/jobs/customers":
            return self.send(page("Customers", "<h1>Customers</h1><p>Smith Bakery, Lee Dental, Park Café</p>", jobs_nav()))
        if p == "/files/materials.csv":
            return self.send("item,qty,unit_price\nCopper pipe 15mm,12,8.50\nTempering valve,1,145.00\nFittings,30,2.20\n", "text/csv",
                             headers={"Content-Disposition": 'attachment; filename="materials.csv"'})
        # roster behind sign-in
        if p.startswith("/roster"):
            if COOKIE.split("=")[1] not in (self.headers.get("Cookie") or ""):
                return self.send(page("Sign in · ShiftHub", "<h1>Sign in</h1><form method='post' action='/roster/login'><input name='email'>"
                                      "<input type='password' name='password'><button>Sign in</button></form>"))
            cards = "".join(f"<div class='shift-card'><h3>{s['role']}</h3><p>Date: {s['date']}</p><p>Start: {s['start']}</p>"
                            f"<p>Finish: {s['end']}</p><p>Location: {s['location']}</p></div>" for s in STATE["shifts"])
            return self.send(page("My shifts · ShiftHub", f"<h1>My shifts</h1><section class='shifts'>{cards}</section>",
                                  '<a href="/roster">Shifts</a> <a href="/roster/timesheet">Timesheet</a>'))
        # project portal
        if p == "/project":
            items = "".join(f"<li class='task'><a class='task-name' href='/project/tasks/{i}'>{E(t['name'])}</a> <span class='badge'>{t['status']}</span> "
                            f"<time class='start' datetime='{t['start']}'>{t['start']}</time> <time class='finish' datetime='{t['end']}'>{t['end']}</time> "
                            f"<span class='owner'>{E(t['owner'])}</span></li>" for i, t in enumerate(STATE["tasks"]))
            return self.send(page("Tower B · BuildSpace", f"<h1>Schedule</h1><ul class='tasks'>{items}</ul>",
                                  '<a href="/project">Schedule</a> <a href="/project/rfis">RFIs</a> <a href="/project/drawings">Drawings</a>'))
        if p.startswith("/project/tasks/"):
            t = STATE["tasks"][int(p.rsplit("/", 1)[1])]
            return self.send(page(t["name"], f"<h1>{E(t['name'])}</h1><dl><dt>Location</dt><dd>Level 3</dd><dt>Owner</dt><dd>{E(t['owner'])}</dd></dl>"))
        if p == "/project/rfis":
            rows = "".join(f"<tr><td>{r['id']}</td><td>{E(r['subject'])}</td><td>{r['asked']}</td><td>{r['reply_by']}</td><td>{r['state']}</td></tr>" for r in STATE["rfis"])
            return self.send(page("RFIs · BuildSpace", f"<h2>Requests for information</h2><table><tr><th>RFI</th><th>Subject</th><th>Raised</th>"
                                  f"<th>Response required by</th><th>State</th></tr>{rows}</table>", '<a href="/project">Schedule</a>'))
        if p == "/project/drawings":
            return self.send(page("Drawings", "<h1>Drawings</h1><a href='/project/files/A-101.dxf'>A-101 floor plan</a>"))
        # unfamiliar CRUD app
        if p == "/lab":
            rows = "".join(f"<tr><td>{s['sid']}</td><td>{s['collected']}</td><td>{s['analyst']}</td><td>{s['result']}</td><td>{s['review']}</td></tr>" for s in STATE["samples"])
            return self.send(page("Samples — LIMS", f"<h1>Samples</h1><form method='get' action='/lab'><input type='search' name='q'></form>"
                                  f"<table><tr><th>Sample ID</th><th>Collected</th><th>Analyst</th><th>Result</th><th>Review by</th></tr>{rows}</table>"
                                  "<h2>Log a result</h2><form method='post' action='/lab/result'><input name='sample' aria-label='Sample'>"
                                  "<input name='result' aria-label='Result'><input type='submit' value='Record'></form>"))
        return self.send("not found", code=404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        SUBMITS.append({"path": self.path, "data": data})
        if self.path.startswith("/jobs/J-") and self.path.endswith("/status"):
            j = next(x for x in STATE["jobs"] if x["no"] == self.path.split("/")[2])
            j["status"] = data.get("status", j["status"])
            return self.send(page("Saved", f"<p>Status saved: {j['status']}</p>"))
        return self.send(page("Saved", "<p>Saved</p>"))


def start() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
