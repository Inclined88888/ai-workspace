"""rooks: ROOKS in the terminal.

The same assistant, conversations, work and approvals as the web app: everything goes through the server's API
(version 1). A conversation started here is in the web app's sidebar, and the other way round.
"""

import argparse
import getpass
import json
import mimetypes
import os
import signal
import sys
import time
import uuid
from pathlib import Path

from . import API_VERSION, __version__, config
from .client import (AUTH, ERROR, INCOMPATIBLE, INTERRUPTED, NEEDS_APPROVAL, OK, TIMEOUT, UNREACHABLE, USAGE, WORK_FAILED, Client,
                     Failure)

EXIT_CODES = """exit codes:
  0  done                          4  an action is waiting for approval (with --no-input)
  1  error reported by ROOKS       5  the work failed or was stopped
  2  wrong usage                   6  the server cannot be reached
  3  not connected or bad token    7  the server speaks another API version
  124 timed out                    130 interrupted (Ctrl+C)"""


class Out:
    """Human output on a terminal, JSON for scripts. Status lines go to stderr so stdout stays clean for pipes."""

    def __init__(self, as_json: bool):
        self.json = as_json
        self.tty = sys.stdout.isatty() and not as_json and not os.environ.get("NO_COLOR")

    def style(self, s: str, code: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.tty else s

    def note(self, text: str) -> None:
        if not self.json:
            print(self.style(text, "2"), file=sys.stderr, flush=True)

    def text(self, text: str = "") -> None:
        if not self.json:
            print(text, flush=True)

    def data(self, obj) -> None:
        if self.json:
            print(json.dumps(obj, ensure_ascii=False, indent=None), flush=True)


def client(args) -> Client:
    cfg = config.load()
    return Client(args.server or cfg.get("url", ""), cfg.get("token", ""), timeout=args.timeout)


# A turn of the conversation, streamed
def turn(args, out: Out, c: Client, message: str, conversation_id: int | None, project_id: int | None = None,
         file_ids: list[int] | None = None) -> dict:
    """Sends one message and follows the answer. A dropped connection is retried once with the same Idempotency-Key, so
    the server answers from what it already did instead of doing it again."""
    body = {"message": message, "channel": "cli", "file_ids": file_ids or []}
    if conversation_id:
        body["conversation_id"] = conversation_id
    elif project_id:
        body["project_id"] = project_id
    key = str(uuid.uuid4())
    deadline = time.monotonic() + args.timeout
    before = {w["id"] for w in c.get(f"/api/v1/work?conversation_id={conversation_id}")} if conversation_id else set()
    result = {"conversation_id": conversation_id, "reply": "", "tools": [], "approvals": [], "errors": [], "work": []}
    for attempt in (1, 2):
        try:
            for e in c.stream("POST", "/api/v1/chat", body, {"Idempotency-Key": key}, deadline):
                t = e.get("type")
                if t == "start":
                    result["conversation_id"] = e["conversation_id"]
                    if not args.no_remember:
                        config.update(conversation_id=e["conversation_id"])
                elif t == "tool_call":
                    result["tools"].append(e["name"])
                    out.note(f"  {STEP.get(e['name'], 'Using ' + e['name'].replace('_', ' '))}…")
                elif t == "approval":
                    result["approvals"].append({"id": e["id"], "tool": e["name"], "args": e.get("args", "")})
                    out.note(f"  Needs your approval (#{e['id']}): {e['name'].replace('_', ' ')}")
                elif t == "notice":
                    out.note(f"  {e.get('text', '')}")
                elif t == "message":
                    result["reply"] = e["content"]
                    out.text("\n" + e["content"].strip() + "\n")
                elif t == "in_progress":
                    out.note(e.get("text", "Still being answered."))
                elif t == "error" or (t is None and "error" in e):
                    result["errors"].append(e["error"])
                    out.text(out.style(e["error"], "31"))
            break
        except Failure as f:
            if attempt == 1 and f.code in (UNREACHABLE,) and result["conversation_id"]:
                out.note("  The connection dropped; asking the server what happened (nothing is run twice)…")
                body["conversation_id"] = result["conversation_id"]
                continue
            raise
    if result["conversation_id"]:  # the work this turn started, finished or not (research can fail in a second)
        try:
            result["work"] = [w for w in c.get(f"/api/v1/work?conversation_id={result['conversation_id']}") if w["id"] not in before]
        except Failure:
            pass
    return result


STEP = {"search_notes": "Searching your notes", "read_note": "Reading a note", "create_note": "Saving a note",
        "list_tasks": "Checking your tasks", "create_task": "Adding a task", "get_agenda": "Checking your calendar",
        "web_search": "Searching the web", "read_page": "Reading a web page", "search_files": "Searching your files",
        "read_file": "Reading a file", "analyze_table": "Analysing the spreadsheet", "search_email": "Searching your email",
        "read_email": "Reading an email", "save_email_draft": "Preparing an email draft", "start_research": "Starting research",
        "start_mission": "Planning the work", "remember": "Saving a memory", "search_memory": "Checking what it remembers"}


def _approvals(args, out: Out, c: Client, pending: list[dict]) -> int:
    """Interactive: ask about each pending approval. With --no-input: report them and exit 4."""
    if not pending:
        return OK
    if args.no_input or not sys.stdin.isatty():
        out.note(f"{len(pending)} action(s) wait for your approval. Decide with: rooks approve ID  or  rooks deny ID")
        return NEEDS_APPROVAL
    detail = {a["id"]: a for a in c.get("/api/v1/approvals")}
    for a in pending:
        d = detail.get(a["id"], {})
        out.text(out.style(f"\nROOKS wants to {a['tool'].replace('_', ' ')}", "1"))
        if d.get("reason"):
            out.text(f"  Why it asks: {d['reason']}")
        out.text(f"  Exactly this: {(d.get('args') or a['args'])[:600]}")
        answer = input("  Allow it once? [y/N] ").strip().lower()
        r = c.post(f"/api/v1/approvals/{a['id']}", {"approve": answer in ("y", "yes")})
        out.note(f"  {'Allowed' if answer in ('y', 'yes') else 'Denied'}: {r.get('status')}" + (f". {r.get('result', '')[:200]}" if r.get("result") else ""))
    return OK


# Commands
def cmd_connect(args, out: Out) -> int:
    url = (args.url or "").strip() or (input("ROOKS address [http://localhost:3100]: ").strip() if not args.no_input else "") or "http://localhost:3100"
    if args.token_stdin:
        token = sys.stdin.readline().strip()
    elif os.environ.get("ROOKS_TOKEN"):
        token = os.environ["ROOKS_TOKEN"]
    elif args.no_input:
        raise Failure(USAGE, "With --no-input, pass the token on standard input (--token-stdin) or in ROOKS_TOKEN.")
    else:
        token = getpass.getpass("Terminal token (ROOKS: Memory and skills, Assistant settings, Terminal access; it is not shown): ").strip()
    c = Client(url, token, timeout=args.timeout)
    meta = c.get("/api/v1/meta")
    status = c.get("/api/v1/status")
    where = config.save({"url": url.rstrip("/"), "token": token})
    out.data({"connected": True, "url": url, "server_version": meta["server_version"], "api_version": meta["api_version"], "token_store": where})
    out.text(f"Connected to ROOKS {meta['server_version']} at {url}. Token kept in {where}.")
    if status.get("model_problem"):
        out.text(out.style(f"Note: {status['model_problem']}", "33"))
    return OK


def cmd_disconnect(args, out: Out) -> int:
    config.forget()
    out.data({"connected": False})
    out.text("Disconnected. The token was removed from this computer; it still works elsewhere until you make a new one in ROOKS.")
    return OK


def _read_message(args) -> str:
    text = " ".join(args.message or []).strip()
    if text == "-" or (not text and not sys.stdin.isatty()):
        text = sys.stdin.read().strip()
    if not text:
        raise Failure(USAGE, "Say what you need, for example: rooks ask \"What is on my calendar today?\"")
    return text


def _conversation(args) -> int | None:
    if getattr(args, "new", False):
        return None
    if args.conversation:
        return args.conversation
    if getattr(args, "continue_", False):
        return config.load().get("conversation_id")
    return None


def _attach(c: Client, out: Out, paths: list[str]) -> list[int]:
    ids = []
    for p in paths or []:
        path = Path(p)
        if not path.is_file():
            raise Failure(USAGE, f"No such file: {p}")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        f = c.upload(path.name, path.read_bytes(), mime)
        out.note(f"  Attached {path.name} (file #{f['id']})")
        ids.append(f["id"])
    return ids


def cmd_ask(args, out: Out) -> int:
    c = client(args)
    text = _read_message(args)
    r = turn(args, out, c, text, _conversation(args), args.project, _attach(c, out, args.file))
    out.data(r)
    code = _approvals(args, out, c, r["approvals"])
    if r["errors"] and not r["reply"]:
        return ERROR
    return code


def cmd_run(args, out: Out) -> int:
    """Ask, then follow any work it started until it finishes (or needs you)."""
    c = client(args)
    text = _read_message(args)
    r = turn(args, out, c, text, _conversation(args), args.project, _attach(c, out, args.file))
    code = _approvals(args, out, c, r["approvals"])
    if code:
        out.data(r)
        return code
    ids = [w["id"] for w in r["work"]]
    final = []
    deadline = time.monotonic() + args.timeout
    last: dict[str, str] = {}
    while ids:
        if time.monotonic() > deadline:
            out.data(r | {"work": final})
            raise Failure(TIMEOUT, "Timed out while the work was still running. Follow it with: rooks jobs watch " + ids[0])
        for wid in list(ids):
            w = c.get(f"/api/v1/work/{wid}")
            if last.get(wid) != w["status_text"]:
                out.note(f"  {w['title'][:60]}: {w['status_text']}")
                last[wid] = w["status_text"]
            if w["terminal"] or w["phase"] == "awaiting_approval":
                ids.remove(wid)
                final.append(w)
        time.sleep(args.poll)
    r["work"] = final or r["work"]
    for w in final:
        if w["error"] and w["phase"] in ("failed", "cancelled"):
            out.note(f"  Why: {w['error'][:400]}")
    out.data(r)
    if any(w["phase"] in ("failed", "cancelled") for w in final):
        return WORK_FAILED
    if any(w["phase"] == "awaiting_approval" for w in final):
        out.note("Work is waiting for you. See: rooks approvals, or open it in ROOKS.")
        return NEEDS_APPROVAL
    return OK


def cmd_chat(args, out: Out) -> int:
    if args.json:
        raise Failure(USAGE, "chat is interactive; for scripts use: rooks ask --json")
    c = client(args)
    try:
        import readline  # noqa: F401 - arrow keys and history in input()
    except ImportError:
        pass
    cid = _conversation(args) if (args.conversation or args.continue_) else None
    project = args.project
    st = c.get("/api/v1/status")
    out.text(out.style("ROOKS", "1") + out.style(f"  {st.get('model') or 'no model'}  ·  /new  /jobs  /help  /quit", "2"))
    if st.get("model_problem"):
        out.text(out.style(st["model_problem"], "33"))
    while True:
        try:
            text = input(out.style("› ", "36")).strip()
        except EOFError:
            out.text()
            return OK
        except KeyboardInterrupt:
            out.text()
            return OK
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return OK
        if text == "/new":
            cid = None
            out.note("New conversation.")
            continue
        if text == "/help":
            out.text("Type what you need. /new starts a new conversation, /jobs shows work in progress, /quit leaves. "
                     "Ctrl+C stops an answer that is still coming.")
            continue
        if text == "/jobs":
            _print_work(out, c.get("/api/v1/work?active=true"))
            continue
        try:
            r = turn(args, out, c, text, cid, project if not cid else None)
            cid = r["conversation_id"]
            _approvals(args, out, c, r["approvals"])
            for w in r["work"]:
                out.note(f"  Started: {w['title'][:70]} ({w['status_text']}). Follow it with /jobs or in the web app.")
        except KeyboardInterrupt:
            if cid:
                try:
                    c.post(f"/api/assistant/stop/{cid}")
                    out.note("Stopped after the current step. Steps already done are not undone.")
                except Failure:
                    pass
        except Failure as f:
            out.text(out.style(f.text, "31"))
            if f.code in (AUTH, INCOMPATIBLE):
                return f.code


def _print_work(out: Out, items: list[dict]) -> None:
    if not items:
        out.text("Nothing in progress.")
        return
    for w in items:
        out.text(f"{w['id']:<16} {w['status_text']:<30} {w['title'][:70]}")


def cmd_status(args, out: Out) -> int:
    c = client(args)
    s = c.get("/api/v1/status")
    out.data(s)
    out.text(f"ROOKS {s['server_version']} at {c.url}")
    out.text(f"  AI: {s['model'] or 'no model'}" + (f" ({'picked automatically' if s['model_source'] == 'auto' else 'your choice'})" if s["model"] else ""))
    if s.get("model_problem"):
        out.text(out.style(f"  {s['model_problem']}", "33"))
    out.text(f"  Needs your review: {s['needs_review']}    Work in progress: {s['active_work']}")
    out.text(f"  Your computer: {'connected (' + s['companion']['name'] + ')' if s['companion']['online'] else 'not connected (pair the ROOKS Companion to let ROOKS act on it)'}")
    return OK


def cmd_projects(args, out: Out) -> int:
    ps = client(args).get("/api/v1/projects")
    out.data(ps)
    for p in ps:
        out.text(f"{p['id']:<5} {p['name'][:50]:<50} {p.get('status', '')}")
    if not ps:
        out.text("No projects yet. Make one in ROOKS, under Projects.")
    return OK


def cmd_conversations(args, out: Out) -> int:
    cs = client(args).get(f"/api/v1/conversations?limit={args.limit}")
    out.data(cs)
    for c in cs:
        out.text(f"{c['id']:<6} {c['channel']:<9} {c['updated_at'][:16].replace('T', ' ')}  {c['title'][:70]}")
    return OK


def cmd_show(args, out: Out) -> int:
    conv = client(args).get(f"/api/v1/conversations/{args.id}")
    out.data(conv)
    out.text(out.style(conv["title"], "1"))
    for m in conv["messages"]:
        who = "You" if m["role"] == "user" else "ROOKS"
        out.text(f"\n{out.style(who, '36')}  {m['content'].strip()}")
    return OK


def cmd_jobs(args, out: Out) -> int:
    c = client(args)
    if args.action in (None, "list"):
        items = c.get("/api/v1/work" + ("?active=true" if args.active else ""))
        out.data(items)
        _print_work(out, items)
        return OK
    if not args.id:
        raise Failure(USAGE, f"Say which work: rooks jobs {args.action} research:12")
    if args.action == "show":
        w = c.get(f"/api/v1/work/{args.id}")
        out.data(w)
        out.text(f"{w['title']}\n  {w['status_text']}" + (f"\n  {w['progress']}" if w["progress"] else "") + (f"\n  {w['error']}" if w["error"] else ""))
        for e in w["events"]:
            out.note(f"  {e['at'][:19].replace('T', ' ')}  {e['text']}")
        return OK
    if args.action in ("cancel", "retry"):
        w = c.post(f"/api/v1/work/{args.id}/{args.action}")
        out.data(w)
        out.text(f"{w['title'][:70]}: {w['status_text']}")
        return OK
    if args.action == "watch":
        deadline, last = time.monotonic() + args.timeout, ""
        while True:
            w = c.get(f"/api/v1/work/{args.id}")
            if w["status_text"] != last:
                out.note(f"{w['status_text']}" + (f": {w['progress']}" if w["progress"] else ""))
                last = w["status_text"]
            if w["terminal"]:
                out.data(w)
                return WORK_FAILED if w["phase"] in ("failed", "cancelled") else OK
            if time.monotonic() > deadline:
                raise Failure(TIMEOUT, "Still running; watch again later.")
            time.sleep(args.poll)
    raise Failure(USAGE, f"Unknown action {args.action}")


def cmd_approvals(args, out: Out) -> int:
    items = client(args).get("/api/v1/approvals")
    out.data(items)
    for a in items:
        out.text(f"#{a['id']:<5} {a['tool'].replace('_', ' '):<24} {a.get('reason', '')[:80]}")
    if not items:
        out.text("Nothing waits for your approval.")
    return OK


def cmd_decide(args, out: Out, approve: bool) -> int:
    c = client(args)
    if approve and not args.yes and (args.no_input or not sys.stdin.isatty()):
        raise Failure(USAGE, "Approving runs the action. Confirm with --yes when there is no one to ask.")
    if approve and not args.yes:
        a = next((x for x in c.get("/api/v1/approvals") if x["id"] == args.id), None)
        if a:
            out.text(f"{a['tool'].replace('_', ' ')}: {a.get('args', '')[:600]}")
        if input("Allow it once? [y/N] ").strip().lower() not in ("y", "yes"):
            out.text("Not approved.")
            return OK
    r = c.post(f"/api/v1/approvals/{args.id}", {"approve": approve})
    out.data(r)
    out.text(("Already decided: " if r.get("already_decided") else "") + f"{r['status']}" + (f". {r.get('result', '')[:300]}" if r.get("result") else ""))
    return OK


def cmd_doctor(args, out: Out) -> int:
    """Each check says what it found and what to do. Exit 0 only if ROOKS can answer from this terminal."""
    checks: list[tuple[str, bool, str]] = []
    cfg = config.load()
    url = args.server or cfg.get("url", "")
    checks.append(("Server address", bool(url), url or "Not set. Run: rooks connect http://your-server:3100"))
    store = cfg.get("token_store") or ("environment" if os.environ.get("ROOKS_TOKEN") else "")
    checks.append(("Terminal token", bool(cfg.get("token")), f"kept in {store}" if cfg.get("token") else "Missing. Run: rooks connect"))
    if sys.platform != "win32" and config.config_path().exists():
        mode = config.config_path().stat().st_mode & 0o777
        checks.append(("Settings file is private", mode & 0o077 == 0, f"{config.config_path()} mode {oct(mode)}"))
    ok_so_far = all(c[1] for c in checks)
    if ok_so_far:
        try:
            c = Client(url, cfg["token"], timeout=args.timeout)
            t0 = time.monotonic()
            meta = c.get("/api/v1/meta", timeout=15)
            checks.append(("Server reachable", True, f"ROOKS {meta['server_version']}, {int((time.monotonic() - t0) * 1000)} ms"))
            checks.append(("API version", API_VERSION in meta["supported"], f"server speaks {meta['supported']}, this client {API_VERSION}"))
            s = c.get("/api/v1/status")
            checks.append(("Token accepted", True, "yes"))
            checks.append(("AI model", bool(s["model"]), s["model"] or s.get("model_problem", "No model connected.")))
            checks.append(("Your computer (optional)", True, "connected" if s["companion"]["online"] else "not paired; ROOKS cannot act on this computer"))
        except Failure as f:
            checks.append(("Server", False, f.text))
    out.data({"ok": all(c[1] for c in checks), "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks], "client_version": __version__})
    for n, o, d in checks:
        out.text(f"{out.style('ok  ', '32') if o else out.style('FAIL', '31')}  {n}: {d}")
    return OK if all(c[1] for c in checks) else ERROR


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rooks", description="ROOKS in the terminal. With no command, starts a conversation.",
                                epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"rooks {__version__} (API {API_VERSION})")
    p.add_argument("--json", action="store_true", help="machine-readable output on stdout; progress stays off stdout")
    p.add_argument("--no-input", action="store_true", help="never prompt; actions that need approval exit with code 4")
    p.add_argument("--server", help="ROOKS address for this command (default: the one you connected to, or ROOKS_URL)")
    p.add_argument("--timeout", type=float, default=float(os.environ.get("ROOKS_TIMEOUT", 600)), help="seconds before giving up (default 600)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("connect", help="connect this terminal to a ROOKS server (asks for the terminal token)")
    s.add_argument("url", nargs="?")
    s.add_argument("--token-stdin", action="store_true", help="read the token from standard input instead of a prompt")
    sub.add_parser("disconnect", help="forget the server and token on this computer")

    def talk(name, help_):
        s = sub.add_parser(name, help=help_)
        s.add_argument("message", nargs="*", help='what you need; "-" or a pipe reads it from standard input')
        s.add_argument("-c", "--continue", dest="continue_", action="store_true", help="continue your last terminal conversation")
        s.add_argument("--conversation", type=int, help="continue this conversation (any client's)")
        s.add_argument("--project", type=int, help="start the conversation in this project")
        s.add_argument("-f", "--file", action="append", help="attach a file (repeatable)")
        s.add_argument("--no-remember", action="store_true", help="do not remember this conversation for --continue")
        s.add_argument("--poll", type=float, default=2.0, help=argparse.SUPPRESS)
        s.add_argument("--new", action="store_true", help=argparse.SUPPRESS)
        return s

    talk("ask", "ask once and print the answer")
    talk("run", "ask, then follow any work it starts until it finishes")
    s = sub.add_parser("chat", help="a conversation in the terminal (the default)")
    s.add_argument("-c", "--continue", dest="continue_", action="store_true")
    s.add_argument("--conversation", type=int)
    s.add_argument("--project", type=int)
    s.add_argument("--no-remember", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("status", help="model, review count, work in progress, your computer")
    sub.add_parser("projects", help="your projects")
    s = sub.add_parser("conversations", help="recent conversations from every client")
    s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("show", help="print a conversation")
    s.add_argument("id", type=int)
    s = sub.add_parser("jobs", help="work in progress: list, show, watch, cancel, retry")
    s.add_argument("action", nargs="?", choices=["list", "show", "watch", "cancel", "retry"])
    s.add_argument("id", nargs="?", help="like research:12 or mission:3")
    s.add_argument("--active", action="store_true", help="only work that is not finished")
    s.add_argument("--poll", type=float, default=2.0, help=argparse.SUPPRESS)
    sub.add_parser("approvals", help="actions waiting for your approval")
    for name in ("approve", "deny"):
        s = sub.add_parser(name, help=f"{name} an action (once)")
        s.add_argument("id", type=int)
        s.add_argument("--yes", action="store_true", help="do not ask to confirm")
    sub.add_parser("doctor", help="check the connection, token, API version and AI model")
    return p


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL) if hasattr(signal, "SIGPIPE") else None  # `rooks ... | head` exits quietly
    args = parser().parse_args(argv)
    if args.cmd is None:
        args.cmd, args.conversation, args.continue_, args.project, args.no_remember = "chat", None, False, None, False
    out = Out(args.json)
    handlers = {"connect": cmd_connect, "disconnect": cmd_disconnect, "ask": cmd_ask, "run": cmd_run, "chat": cmd_chat,
                "status": cmd_status, "projects": cmd_projects, "conversations": cmd_conversations, "show": cmd_show,
                "jobs": cmd_jobs, "approvals": cmd_approvals, "doctor": cmd_doctor,
                "approve": lambda a, o: cmd_decide(a, o, True), "deny": lambda a, o: cmd_decide(a, o, False)}
    try:
        code = handlers[args.cmd](args, out)
    except Failure as f:
        if args.json:
            print(json.dumps({"error": f.text, "exit_code": f.code, "status": f.status}), flush=True)
        else:
            print(out.style(f.text, "31"), file=sys.stderr)
        code = f.code
    except KeyboardInterrupt:
        print(file=sys.stderr)
        code = INTERRUPTED
    sys.exit(code)


if __name__ == "__main__":
    main()
