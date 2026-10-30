"""Local HTTP API (standard library only), the same job shape as Rooks Browser's so Workbench can proxy it:

  GET  /healthz                         liveness
  GET  /api/status                      backend, permissions, kill-switch hotkey, running job, policy
  GET  /api/settings  PUT /api/settings allowed folders, app allow/deny lists, trusted MCP servers
  GET  /api/providers                   models it can use
  POST /api/jobs {"goal", "approve": [...], "dry_run", "max_steps", "folders", "provider", "model"}
  GET  /api/jobs  /api/jobs/{id}        steps (thought, action, result, shell output), approvals, result
  GET  /api/jobs/{id}/screenshot[/N]    live screen, or step N's receipt screenshot (PNG)
  GET  /api/jobs/{id}/approvals         POST /api/jobs/{id}/approvals/{aid} {"approve": true|false}
  POST /api/jobs/{id}/cancel            stop that job now        POST /api/stop   stop whatever is running
  GET  /api/jobs/{id}/receipt           receipt rows             GET  /api/screen  a screenshot right now
  POST /api/recordings  GET /api/recordings/{id}  POST /api/recordings/{id}/stop
                                        "Show me how": which apps and windows the person used, in order (recording.py)

Only one job drives the mouse at a time; others queue. Binding beyond 127.0.0.1 needs ROOKS_DESK_TOKEN (24+ chars).
No answer to an approval in ROOKS_DESK_APPROVAL_TIMEOUT seconds (default 600) is a no.
"""

from __future__ import annotations

import argparse
import hmac
import io
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__, llm, receipts, runner
from .killswitch import HOTKEY_LABEL
from .policy import DeskPolicy, parse_categories

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


class Jobs:
    def __init__(self, *, backend: str | None = None, max_steps_limit: int = 80, approval_timeout: float = 600, overlay: bool = True,
                 backend_obj=None, model_factory=None, mailer=None):
        self.backend_name, self.backend_obj = backend, backend_obj
        self.max_steps_limit, self.approval_timeout, self.overlay = max_steps_limit, approval_timeout, overlay
        self.model_factory, self.mailer = model_factory, mailer
        self.jobs: dict[str, dict] = {}
        self.agents: dict[str, object] = {}
        self.waiting: dict[str, tuple[threading.Event, dict]] = {}
        self.lock = threading.Lock()
        self.mouse = threading.Lock()  # one job at a time drives the computer
        self.shots = runner.home() / "shots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self._backend = None

    def backend(self):
        if self.backend_obj is not None:
            return self.backend_obj
        if self._backend is None:
            from .backends import get_backend
            self._backend = get_backend(self.backend_name)
        return self._backend

    # --- HTTP side
    def create(self, data: dict, source: str = "api") -> dict:
        goal = str(data.get("goal") or "").strip()
        if len(goal) < 3:
            raise ValueError("goal is required")
        provider = data.get("provider") or None
        if provider not in (None, "auto") and provider not in llm.PROVIDERS:
            raise ValueError(f"unknown provider {provider}")
        approve = data.get("approve") or []
        s = runner.Settings(provider=None if provider == "auto" else provider, model=data.get("model") or None,
                            max_steps=min(int(data.get("max_steps") or 40), self.max_steps_limit), backend=self.backend_name,
                            vision=data.get("vision") or "auto", dry_run=bool(data.get("dry_run")),
                            pre_approved=parse_categories(",".join(approve) if isinstance(approve, list) else str(approve)),
                            folders=[str(f) for f in data.get("folders") or []], overlay=self.overlay,
                            workbench_db=os.getenv("ROOKS_DESK_WORKBENCH_DB"))
        if not self.model_factory:
            llm.resolve(s.provider)  # fail fast when no model is set up
        jid = uuid.uuid4().hex[:12]
        now = time.time()
        job = {"id": jid, "goal": goal, "provider": s.provider or "auto", "model": s.model or "", "status": "queued", "source": source,
               "created_at": now, "updated_at": now, "max_steps": s.max_steps, "dry_run": s.dry_run, "steps": [], "result": None,
               "error": None, "approvals": [], "receipt": None, "live": None}
        with self.lock:
            self.jobs[jid] = job
        threading.Thread(target=self._run, args=(jid, goal, s), daemon=True, name=f"desk-job-{jid}").start()
        return self.get(jid)

    def get(self, jid: str) -> dict:
        with self.lock:
            j = self.jobs.get(jid)
            if not j:
                raise KeyError(jid)
            d = json.loads(json.dumps(j, default=str))
        d["has_screenshot"] = (self.shots / f"{jid}.png").exists()
        d["pending_approvals"] = [a for a in d["approvals"] if a.get("answer") is None]
        return d

    def list(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = sorted(self.jobs.values(), key=lambda j: -j["created_at"])[:limit]
            return [{k: j[k] for k in ("id", "goal", "provider", "model", "status", "source", "created_at", "updated_at", "dry_run")}
                    for j in rows]

    def running(self) -> str | None:
        with self.lock:
            return next((j["id"] for j in self.jobs.values() if j["status"] in ("running", "waiting")), None)

    def answer(self, jid: str, aid: str, approve: bool) -> dict:
        w = self.waiting.get(aid)
        if not w or w[1].get("job") != jid:
            raise KeyError(aid)
        w[1]["answer"] = bool(approve)
        w[0].set()
        return {"ok": True, "approval": aid, "approve": bool(approve)}

    def cancel(self, jid: str, reason: str = "Stop pressed in the app") -> dict:
        ag = self.agents.get(jid)
        if ag:
            ag.stop(reason)
        for aid, (ev, entry) in list(self.waiting.items()):
            if entry.get("job") == jid and entry.get("answer") is None:
                entry["answer"] = False
                ev.set()
        with self.lock:
            if jid in self.jobs and self.jobs[jid]["status"] == "queued":
                self.jobs[jid]["status"] = "cancelled"
        return self.get(jid)

    def stop_all(self) -> dict:
        ids = [j for j in list(self.jobs) if self.jobs[j]["status"] in ("queued", "running", "waiting")]
        for j in ids:
            self.cancel(j, "Stop pressed")
        return {"ok": True, "stopped": ids}

    def delete(self, jid: str) -> dict:
        self.cancel(jid)
        with self.lock:
            self.jobs.pop(jid, None)
        (self.shots / f"{jid}.png").unlink(missing_ok=True)
        return {"ok": True}

    def wait(self, jid: str, timeout: float) -> dict:
        end = time.time() + timeout
        while time.time() < end and self.get(jid)["status"] in ("queued", "running", "waiting"):
            time.sleep(0.25)
        return self.get(jid)

    def step_shot(self, jid: str, step: int) -> Path | None:
        j = self.get(jid)
        if not j.get("shots_dir"):
            return None
        p = Path(j["shots_dir"]) / f"step-{step:02d}.png"
        return p if p.exists() else None

    # --- job thread
    def _set(self, jid, **kw):
        with self.lock:
            if jid in self.jobs:
                self.jobs[jid].update(kw, updated_at=time.time())

    def _run(self, jid: str, goal: str, s: runner.Settings):
        shot = self.shots / f"{jid}.png"
        holder: dict = {}

        def approver(ap) -> bool:
            ev = threading.Event()
            entry = {**ap.to_dict(), "job": jid, "answer": None, "asked_at": time.time(), "step": holder.get("step")}
            self.waiting[ap.id] = (ev, entry)
            with self.lock:
                if jid not in self.jobs:  # deleted while running: nobody is left to answer
                    self.waiting.pop(ap.id, None)
                    return False
                self.jobs[jid]["approvals"].append(entry)
            self._set(jid, status="waiting", waiting_for_approval=ap.id)
            ok = ev.wait(self.approval_timeout) and bool(entry.get("answer"))
            if entry.get("answer") is None:
                entry["answer"] = False
                entry["timed_out"] = True
            entry["answered_at"] = time.time()
            self.waiting.pop(ap.id, None)
            self._set(jid, status="running", waiting_for_approval=None)
            return ok

        def on_event(ev):
            d = ev.to_dict()
            holder["step"] = ev.step
            with self.lock:
                if jid not in self.jobs:
                    return
                steps = self.jobs[jid]["steps"]
                if steps and steps[-1]["step"] == ev.step:
                    steps[-1] = d
                else:
                    steps.append(d)
                self.jobs[jid]["live"] = {"step": ev.step, "output": ev.output}
            ag = holder.get("agent")
            if ag and ag.obs is not None:
                _save(shot, ag.obs.png)

        on_event.partial = True  # stream shell output into the job as it arrives

        def live_view():
            while not holder.get("done"):
                time.sleep(1.5)
                ag = holder.get("agent")
                if ag is None:
                    continue
                try:
                    _save(shot, ag.desk.observe(with_elements=False).png)
                except Exception:  # noqa: BLE001
                    pass

        with self.mouse:
            with self.lock:
                if self.jobs.get(jid, {}).get("status", "cancelled") == "cancelled":
                    return  # cancelled or deleted while it waited for the mouse
            self._set(jid, status="running")
            threading.Thread(target=live_view, daemon=True).start()
            try:
                model = self.model_factory(s) if self.model_factory else None

                def on_agent(ag):
                    holder["agent"] = ag
                    self.agents[jid] = ag
                    self._set(jid, receipt=str(ag.log.path), shots_dir=str(ag.log.shots), model=ag.model.model,
                              provider=ag.model.provider)

                res = runner.run_task(goal, s, approver=approver, on_event=on_event, model=model, on_agent=on_agent,
                                      backend=self.backend(), mailer=self.mailer)
                status = "stopped" if res.stopped else "done"
                self._set(jid, status=status, result=res.to_dict(), receipt=res.receipt)
            except Exception as e:  # noqa: BLE001 - every failure is shown on the job
                self._set(jid, status="error", error=str(e) or e.__class__.__name__)
            finally:
                holder["done"] = True
                self.agents.pop(jid, None)


def _save(p: Path, png: bytes):
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(png)
    tmp.replace(p)


def status(jobs: Jobs) -> dict:
    out = {"version": __version__, "platform": sys.platform, "hotkey": HOTKEY_LABEL, "running": jobs.running(),
           "policy": runner.load_policy().to_dict(), "home": str(runner.home()), "mcp_servers": sorted(runner.load_mcp_servers())}
    try:
        b = jobs.backend()
        out.update(backend=b.name, ok=True, permissions=b.permissions(), accessibility=list(b.ax_available()),
                   monitors=[m.to_dict() for m in b.monitors()])
    except Exception as e:  # noqa: BLE001
        out.update(backend=None, ok=False, error=str(e))
    return out


def make_handler(jobs: Jobs, token: str):
    class H(BaseHTTPRequestHandler):
        server_version = f"rooks-desk/{__version__}"

        def log_message(self, fmt, *args):
            if os.getenv("ROOKS_DESK_ACCESS_LOG"):
                super().log_message(fmt, *args)

        def _send(self, code: int, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self) -> bool:
            if token:
                h = self.headers.get("Authorization", "")
                return h.lower().startswith("bearer ") and hmac.compare_digest(h[7:], token)
            raw = self.headers.get("Host") or ""
            host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.rsplit(":", 1)[0]
            return host in LOOPBACK_HOSTS  # no token: only this computer, and no DNS rebinding from web pages

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise ValueError("body too large")
            if n and "json" not in (self.headers.get("Content-Type") or ""):
                raise ValueError("send Content-Type: application/json")
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _route(self, method: str):
            u = urlsplit(self.path)
            parts = [p for p in u.path.split("/") if p]
            if u.path == "/healthz":
                return self._send(200, {"ok": True, "version": __version__, "service": "rooks-desk"})
            if not self._authorised():
                return self._send(401, {"detail": "Invalid or missing bearer token"})
            try:
                if parts == ["api", "status"]:
                    return self._send(200, status(jobs))
                if parts == ["api", "stop"] and method == "POST":
                    return self._send(200, jobs.stop_all())
                if parts == ["api", "settings"]:
                    if method == "GET":
                        return self._send(200, runner.load_policy().to_dict())
                    if method in ("PUT", "POST"):
                        b = self._body()
                        p = DeskPolicy.load(runner.policy_file())
                        for k in ("folders", "allow_apps", "deny_apps", "trusted_mcp", "safe_commands"):
                            if isinstance(b.get(k), list):
                                setattr(p, k, [str(x) for x in b[k] if str(x).strip()])
                        p.save(runner.policy_file())
                        return self._send(200, p.to_dict())
                if parts == ["api", "providers"]:
                    ready = llm.ready()
                    return self._send(200, {"providers": [{"name": n, "label": p.label, "ready": n in ready, "default_model": p.default_model,
                                                           "notes": p.notes} for n, p in llm.PROVIDERS.items() if n != "scripted"]})
                if parts == ["api", "screen"] and method == "GET":
                    from .backends.base import to_png
                    img = jobs.backend().screenshot(1)
                    if img.width > 1280:
                        img = img.resize((1280, round(img.height * 1280 / img.width)))
                    return self._send(200, to_png(img), "image/png")
                if parts[:2] == ["api", "recordings"]:  # Workbench "Show me how" (recording.py): apps and windows only
                    from .recording import Recordings
                    if not hasattr(jobs, "recordings"):
                        jobs.recordings = Recordings(jobs.backend, float(os.getenv("ROOKS_DESK_RECORD_INTERVAL", "0.7")))
                    if len(parts) == 2 and method == "POST":
                        return self._send(200, jobs.recordings.start(str(self._body().get("start_url") or "")))
                    if len(parts) == 3 and method == "GET":
                        return self._send(200, jobs.recordings.get(parts[2]))
                    if parts[3:] == ["stop"] and method == "POST":
                        return self._send(200, jobs.recordings.stop(parts[2]))
                if parts[:2] == ["api", "jobs"]:
                    if len(parts) == 2:
                        if method == "GET":
                            return self._send(200, jobs.list(int(parse_qs(u.query).get("limit", ["50"])[0])))
                        if method == "POST":
                            body = self._body()
                            job = jobs.create(body, source=str(body.get("source") or "api"))
                            if body.get("wait"):
                                job = jobs.wait(job["id"], min(float(body["wait"]), 1800))
                            return self._send(200, job)
                    jid = parts[2]
                    if len(parts) == 3 and method == "GET":
                        return self._send(200, jobs.get(jid))
                    if len(parts) == 3 and method == "DELETE":
                        return self._send(200, jobs.delete(jid))
                    if parts[3:] == ["cancel"] and method == "POST":
                        return self._send(200, jobs.cancel(jid))
                    if parts[3:4] == ["screenshot"] and len(parts) <= 5:
                        if len(parts) == 5:
                            if not parts[4].isdigit():
                                return self._send(404, {"detail": "No such step"})
                            p = jobs.step_shot(jid, int(parts[4]))
                        else:
                            jobs.get(jid)
                            p = jobs.shots / f"{jid}.png"
                        if not p or not p.exists():
                            return self._send(404, {"detail": "No screenshot yet"})
                        return self._send(200, p.read_bytes(), "image/png")
                    if parts[3:] == ["receipt"]:
                        j = jobs.get(jid)
                        return self._send(200, receipts.read(Path(j["receipt"])) if j.get("receipt") and Path(j["receipt"]).exists() else [])
                    if parts[3:4] == ["approvals"]:
                        if len(parts) == 4:
                            return self._send(200, jobs.get(jid)["approvals"])
                        if method == "POST":
                            return self._send(200, jobs.answer(jid, parts[4], bool(self._body().get("approve"))))
            except KeyError:
                return self._send(404, {"detail": "Not found"})
            except (ValueError, json.JSONDecodeError) as e:
                return self._send(400, {"detail": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"detail": str(e).splitlines()[0][:300] if str(e) else e.__class__.__name__})
            return self._send(404, {"detail": "Not found"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")

    return H


def serve(host: str = "127.0.0.1", port: int = 8766, token: str = "", **jobs_kw) -> tuple[ThreadingHTTPServer, Jobs]:
    if host not in LOOPBACK_HOSTS and len(token) < 24:
        raise SystemExit("rooks-desk serve: binding beyond this computer needs ROOKS_DESK_TOKEN (24+ characters)")
    jobs = Jobs(**jobs_kw)
    srv = ThreadingHTTPServer((host, port), make_handler(jobs, token))
    srv.daemon_threads = True
    return srv, jobs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rooks-desk serve")
    p.add_argument("--host", default=os.getenv("ROOKS_DESK_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("ROOKS_DESK_PORT", "8766")))
    p.add_argument("--backend", default=os.getenv("ROOKS_DESK_BACKEND"))
    p.add_argument("--no-overlay", action="store_true")
    p.add_argument("--exit-with-parent", action="store_true", help="exit when stdin closes (used by the desktop app)")
    a = p.parse_args(argv)
    if a.exit_with_parent:
        def watch():
            try:
                while sys.stdin.read(1):
                    pass
            finally:
                os._exit(0)
        threading.Thread(target=watch, daemon=True, name="parent-watch").start()
    token = os.getenv("ROOKS_DESK_TOKEN") or os.getenv("DESK_API_KEY") or ""
    srv, _ = serve(a.host, a.port, token, backend=a.backend, overlay=not a.no_overlay,
                   approval_timeout=float(os.getenv("ROOKS_DESK_APPROVAL_TIMEOUT", "600")))
    print(f"Rooks Desk API on http://{a.host}:{srv.server_address[1]} ({'token required' if token else 'this computer only'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
