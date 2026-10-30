"""Local HTTP API, standard library only. It speaks the same job API as the old browser-agent container
(/api/providers, /api/jobs, /api/jobs/{id}, /screenshot, /cancel, DELETE), so Workbench's Browser page, its browse_web
tool and browser_proxy.py work unchanged when BROWSER_URL points here, and adds what that container never had:
approvals (GET/POST /api/jobs/{id}/approvals), receipts (GET /api/jobs/{id}/receipt), one-off /api/read, /api/search, and
recordings for Workbench's "Show me how" (POST /api/recordings, GET /api/recordings/{id}, POST /api/recordings/{id}/stop),
and work sites for Workbench's Sites page (POST /api/site/login|close|page|submit|download, see sites.py).

  rooks-browser serve                      127.0.0.1:8765, no token needed from this computer
  rooks-browser serve --host 0.0.0.0       refuses to start without ROOKS_BROWSER_TOKEN (or BROWSER_API_KEY)

Sensitive actions in a job wait for an answer from POST /api/jobs/{id}/approvals/{approval_id} {"approve": true|false};
no answer in ROOKS_BROWSER_APPROVAL_TIMEOUT seconds (default 600) is a no. `approve: ["submit"]` in the job pre-approves.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__, llm, receipts, runner
from .policy import parse_categories

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")



def _auto_or_none() -> str | None:
    try:
        return llm.resolve(None)
    except ValueError:
        return None

class Jobs:
    def __init__(self, *, headless: bool = True, engine: str = "auto", cdp_url: str | None = None, max_concurrent: int = 2,
                 max_steps_limit: int = 50, approval_timeout: float = 600, block_private: bool = False):
        self.headless, self.engine, self.cdp_url = headless, engine, cdp_url
        self.max_steps_limit, self.approval_timeout, self.block_private = max_steps_limit, approval_timeout, block_private
        self.jobs: dict[str, dict] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.agents: dict[str, object] = {}
        self.waiting: dict[str, tuple[asyncio.Future, dict]] = {}
        self.loop = asyncio.new_event_loop()
        self.sem = asyncio.Semaphore(max_concurrent)
        self.lock = threading.Lock()
        self.shots = runner.home() / "shots"
        threading.Thread(target=self.loop.run_forever, daemon=True, name="rooks-browser-jobs").start()

    # called from HTTP threads -----------------------------------------------------------------------------------------
    def create(self, data: dict, source: str = "api") -> dict:
        goal = str(data.get("goal") or "").strip()
        if len(goal) < 3:
            raise ValueError("goal is required")
        provider = data.get("provider") or None
        if provider not in (None, "auto") and provider not in llm.PROVIDERS:
            raise ValueError(f"unknown provider {provider}")
        approve = data.get("approve") or []
        s = runner.Settings(provider=provider, model=data.get("model") or None, max_steps=min(int(data.get("max_steps") or 25), self.max_steps_limit),
                            headless=self.headless if data.get("headed") is None else not data["headed"], engine=self.engine,
                            cdp_url=self.cdp_url, allow=list(data.get("allowed_domains") or []), deny=list(data.get("denied_domains") or []),
                            vision=data.get("vision") or "auto", start_url=data.get("start_url") or None,
                            pre_approved=parse_categories(",".join(approve) if isinstance(approve, list) else str(approve)),
                            workbench_db=os.getenv("ROOKS_BROWSER_WORKBENCH_DB"))
        llm.resolve(provider)  # fail fast on a bad provider
        jid = uuid.uuid4().hex[:12]
        now = time.time()
        job = {"id": jid, "goal": goal, "provider": llm.resolve(provider), "model": s.model or "", "status": "queued", "source": source,
               "created_at": now, "updated_at": now, "max_steps": s.max_steps, "allowed_domains": s.allow, "steps": [],
               "result": None, "error": None, "approvals": [], "receipt": None}
        with self.lock:
            self.jobs[jid] = job
        fut = asyncio.run_coroutine_threadsafe(self._spawn(jid, goal, s), self.loop)
        fut.result(5)
        return self.get(jid)

    def get(self, jid: str) -> dict:
        with self.lock:
            j = self.jobs.get(jid)
            if not j:
                raise KeyError(jid)
            d = json.loads(json.dumps(j))
        d["has_screenshot"] = (self.shots / f"{jid}.png").exists()
        d["pending_approvals"] = [a for a in d["approvals"] if a.get("answer") is None]
        return d

    def list(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = sorted(self.jobs.values(), key=lambda j: -j["created_at"])[:limit]
            return [{k: j[k] for k in ("id", "goal", "provider", "model", "status", "source", "created_at", "updated_at")} for j in rows]

    def answer(self, jid: str, aid: str, approve: bool) -> dict:
        w = self.waiting.get(aid)
        if not w or w[1].get("job") != jid:
            raise KeyError(aid)
        self.loop.call_soon_threadsafe(lambda: w[0].done() or w[0].set_result(bool(approve)))
        return {"ok": True, "approval": aid, "approve": bool(approve)}

    def cancel(self, jid: str) -> dict:
        t = self.tasks.get(jid)
        if t:
            self.loop.call_soon_threadsafe(t.cancel)
        return self.get(jid)

    def delete(self, jid: str) -> dict:
        self.cancel(jid) if jid in self.tasks else None
        with self.lock:
            self.jobs.pop(jid, None)
        for f in [self.shots / f"{jid}.png", *self.shots.glob(f"{jid}-*.png")]:
            f.unlink(missing_ok=True)
        return {"ok": True}

    def wait(self, jid: str, timeout: float) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            if self.get(jid)["status"] not in ("queued", "running"):
                break
            time.sleep(0.25)
        return self.get(jid)

    # on the job loop ------------------------------------------------------------------------------------------------
    async def _spawn(self, jid, goal, s):
        self.tasks[jid] = asyncio.ensure_future(self._run(jid, goal, s))

    def _set(self, jid, **kw):
        with self.lock:
            if jid in self.jobs:
                self.jobs[jid].update(kw, updated_at=time.time())

    async def _run(self, jid: str, goal: str, s: runner.Settings):
        shot = self.shots / f"{jid}.png"
        holder: dict = {}

        async def approver(ap) -> bool:
            fut = self.loop.create_future()
            entry = {**ap.to_dict(), "job": jid, "answer": None, "asked_at": time.time()}
            self.waiting[ap.id] = (fut, entry)
            with self.lock:
                self.jobs[jid]["approvals"].append(entry)
            self._set(jid, waiting_for_approval=ap.id)
            try:
                ok = await asyncio.wait_for(fut, self.approval_timeout)
            except asyncio.TimeoutError:
                ok = False
                entry["timed_out"] = True
            entry["answer"] = "approved" if ok else "denied"
            self.waiting.pop(ap.id, None)
            self._set(jid, waiting_for_approval=None)
            return ok

        async def save(ag, *paths):  # write then rename, so the app never reads half a PNG
            data = await ag.browser.screenshot()
            shot.parent.mkdir(parents=True, exist_ok=True)
            for p in paths:
                tmp = p.with_suffix(".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, p)

        async def on_event(ev):
            d = ev.to_dict()
            d.pop("screenshot", None)
            d["has_screenshot"] = False
            ag = holder.get("agent")
            if ag:  # a picture of every step, so the app can replay the run step by step
                try:
                    await save(ag, shot, self.shots / f"{jid}-{ev.step}.png")
                    d["has_screenshot"] = True
                except Exception:  # noqa: BLE001
                    pass
            with self.lock:
                self.jobs[jid]["steps"].append(d)

        async def live_view():  # keep the live picture fresh while a page loads or the model thinks
            while True:
                await asyncio.sleep(1.5)
                ag = holder.get("agent")
                if ag:
                    try:
                        await save(ag, shot)
                    except Exception:  # noqa: BLE001 - a page mid-navigation; the next tick catches up
                        pass

        async with self.sem:
            self._set(jid, status="running")
            live = asyncio.ensure_future(live_view())
            try:
                res = await runner.run_task(goal, s, approver=approver, on_event=on_event,
                                            on_agent=lambda ag: (holder.update(agent=ag), self._policy(ag)))
                self._set(jid, status="done", result=res.to_dict(), receipt=res.receipt, model=res.model)
            except asyncio.CancelledError:
                self._set(jid, status="cancelled")
            except Exception as e:  # noqa: BLE001 - every failure is shown on the job
                self._set(jid, status="error", error=str(e) or e.__class__.__name__)
            finally:
                live.cancel()
                self.tasks.pop(jid, None)

    def _policy(self, agent):
        if self.block_private:
            agent.browser.policy.block_private = True


def make_handler(jobs: Jobs, token: str):
    class H(BaseHTTPRequestHandler):
        server_version = f"rooks-browser/{__version__}"

        def log_message(self, fmt, *args):
            if os.getenv("ROOKS_BROWSER_ACCESS_LOG"):
                super().log_message(fmt, *args)

        def _send(self, code: int, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
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
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0] if not (self.headers.get("Host") or "").startswith("[") \
                else (self.headers.get("Host") or "").split("]")[0] + "]"
            return host in LOOPBACK_HOSTS  # no token: only this computer, and no DNS-rebinding from web pages

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
                return self._send(200, {"ok": True, "version": __version__})
            if not self._authorised():
                return self._send(401, {"detail": "Invalid or missing bearer token"})
            try:
                if parts[:2] == ["api", "providers"]:
                    ready = llm.ready()
                    return self._send(200, {"auto": _auto_or_none(), "usage": {"used": 0, "limit": 0}, "workbench_required": False,
                                            "providers": [{"name": n, "label": p.label, "ready": n in ready, "default_model": p.default_model,
                                                           "notes": p.notes} for n, p in llm.PROVIDERS.items() if n != "scripted"]})
                if parts[:2] == ["api", "jobs"]:
                    if len(parts) == 2:
                        if method == "GET":
                            return self._send(200, jobs.list(int(parse_qs(u.query).get("limit", ["50"])[0])))
                        if method == "POST":
                            body = self._body()
                            job = jobs.create(body, source=str(body.get("source") or "api"))
                            if body.get("wait"):
                                job = jobs.wait(job["id"], min(float(body["wait"]), 900))
                            return self._send(200, job)
                    jid = parts[2]
                    if len(parts) == 3 and method == "GET":
                        return self._send(200, jobs.get(jid))
                    if len(parts) == 3 and method == "DELETE":
                        return self._send(200, jobs.delete(jid))
                    if parts[3:] == ["cancel"] and method == "POST":
                        return self._send(200, jobs.cancel(jid))
                    if parts[3:4] == ["screenshot"] and len(parts) <= 5:
                        step = parts[4] if len(parts) == 5 else ""
                        if step and not step.isdigit():
                            return self._send(404, {"detail": "No such step"})
                        p = jobs.shots / (f"{jid}-{step}.png" if step else f"{jid}.png")
                        if not p.exists():
                            return self._send(404, {"detail": "No screenshot yet"})
                        return self._send(200, p.read_bytes(), "image/png")
                    if parts[3:] == ["receipt"]:
                        j = jobs.get(jid)
                        return self._send(200, receipts.read(Path(j["receipt"])) if j.get("receipt") else [])
                    if parts[3:4] == ["approvals"]:
                        if len(parts) == 4:
                            return self._send(200, jobs.get(jid)["approvals"])
                        if method == "POST":
                            return self._send(200, jobs.answer(jid, parts[4], bool(self._body().get("approve"))))
                if parts[:2] == ["api", "recordings"]:  # Workbench "Show me how" (recorder.py)
                    from .recorder import Recordings
                    if not hasattr(jobs, "recordings"):
                        jobs.recordings = Recordings(jobs.loop, headless=os.getenv("ROOKS_BROWSER_RECORD_HEADLESS") == "1", engine=jobs.engine)
                    if len(parts) == 2 and method == "POST":
                        return self._send(200, jobs.recordings.start(str(self._body().get("start_url") or "")))
                    if len(parts) == 3 and method == "GET":
                        return self._send(200, jobs.recordings.get(parts[2]))
                    if parts[3:] == ["stop"] and method == "POST":
                        return self._send(200, jobs.recordings.stop(parts[2]))
                if parts[:2] == ["api", "site"] and len(parts) == 3 and method == "POST":  # Workbench Sites (sites.py)
                    from .sites import handle
                    code, out = handle(jobs, parts[2], self._body())
                    return self._send(code, out)
                if parts[:2] == ["api", "read"] and method == "POST":
                    from .cli import one_off_read
                    b = self._body()
                    s = runner.Settings(headless=True, engine=jobs.engine, cdp_url=jobs.cdp_url)
                    fut = asyncio.run_coroutine_threadsafe(one_off_read(str(b.get("url") or ""), s, int(b.get("max_chars") or 20000)), jobs.loop)
                    return self._send(200, fut.result(120))
                if parts[:2] == ["api", "search"] and method == "POST":
                    from .cli import one_off_search
                    s = runner.Settings(headless=True, engine=jobs.engine, cdp_url=jobs.cdp_url)
                    fut = asyncio.run_coroutine_threadsafe(one_off_search(str(self._body().get("query") or ""), s), jobs.loop)
                    return self._send(200, fut.result(120))
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

        def do_DELETE(self):
            self._route("DELETE")

    return H


def serve(host: str = "127.0.0.1", port: int = 8765, token: str = "", **jobs_kw) -> tuple[ThreadingHTTPServer, Jobs]:
    if host not in LOOPBACK_HOSTS and len(token) < 24:
        raise SystemExit("rooks-browser serve: binding beyond this computer needs ROOKS_BROWSER_TOKEN (24+ characters)")
    jobs = Jobs(**jobs_kw)
    srv = ThreadingHTTPServer((host, port), make_handler(jobs, token))
    srv.daemon_threads = True
    return srv, jobs


def main(argv: list[str] | None = None) -> int:
    from .cli import has_display
    p = argparse.ArgumentParser(prog="rooks-browser serve")
    p.add_argument("--host", default=os.getenv("ROOKS_BROWSER_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("ROOKS_BROWSER_PORT", "8765")))
    p.add_argument("--headed", action="store_true", default=os.getenv("ROOKS_BROWSER_HEADED") == "1" and has_display())
    p.add_argument("--headless", dest="headed", action="store_false", help="no window (overrides --headed)")
    p.add_argument("--exit-with-parent", action="store_true", help="exit when stdin closes (used by the desktop app)")
    p.add_argument("--browser", dest="engine", default=os.getenv("ROOKS_BROWSER_ENGINE", "auto"))
    p.add_argument("--cdp", dest="cdp_url", default=os.getenv("ROOKS_BROWSER_CDP_URL"))
    p.add_argument("--block-private", action="store_true", default=os.getenv("ROOKS_BROWSER_BLOCK_PRIVATE") == "1",
                   help="refuse localhost and LAN addresses (use when other services share the network)")
    a = p.parse_args(argv)
    if a.exit_with_parent:
        import sys

        def watch():
            try:
                while sys.stdin.read(1):
                    pass
            finally:
                os._exit(0)

        threading.Thread(target=watch, daemon=True, name="parent-watch").start()
    token = os.getenv("ROOKS_BROWSER_TOKEN") or os.getenv("BROWSER_API_KEY") or ""
    srv, _ = serve(a.host, a.port, token, headless=not a.headed, engine=a.engine, cdp_url=a.cdp_url, block_private=a.block_private,
                   approval_timeout=float(os.getenv("ROOKS_BROWSER_APPROVAL_TIMEOUT", "600")))
    print(f"Rooks Browser API on http://{a.host}:{srv.server_address[1]} ({'token required' if token else 'this computer only'}, "
          f"{'headed' if a.headed else 'headless'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
