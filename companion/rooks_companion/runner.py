"""Connects to ROOKS, asks for one action at a time, runs it on a single worker thread, and keeps checking for Stop
while it runs. Talks only to the ROOKS address you paired with; opens no port of its own."""

from __future__ import annotations

import json
import os
import platform
import queue
import socket
import threading
import time
import urllib.error
import urllib.request

from . import VERSION
from .engine import Engine


class Client:
    def __init__(self, url: str, token: str = ""):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "X-Workbench": "1",
                                              **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail", "")
            except ValueError:
                detail = ""
            raise RuntimeError(f"ROOKS answered {e.code}: {detail or e.reason}") from e


def info(engine: Engine, cfg: dict) -> dict:
    perms = {"accessibility": None, "screen_recording": None, "notes": ""}
    note = ""
    if engine.adapter is not None:
        try:
            perms = engine.adapter.permissions()
        except Exception as e:  # noqa: BLE001
            perms["notes"] = str(e)
        if engine.adapter.name == "x11":
            note = "Linux X11: windows, typing, keys and clicks work; app elements only for apps that publish them."
        elif engine.adapter.name == "windows":
            note = "Windows: desktop control is not available yet; ROOKS's browser and allowed folders work."
    return {"os": f"{platform.system()} {platform.release()}", "hostname": socket.gethostname(), "version": VERSION,
            "adapter": engine.adapter.name if engine.adapter else "", "permissions": perms, "capabilities": engine.capabilities(),
            "folders": cfg.get("folders", []), "blocked_apps": cfg.get("blocked_apps", []), "read_only": bool(cfg.get("read_only")),
            "platform_note": note}


class Runner:
    def __init__(self, client: Client, engine: Engine, cfg: dict, log=print):
        self.client, self.engine, self.cfg, self.log = client, engine, cfg, log
        self.jobs: queue.Queue = queue.Queue()
        self.done: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.quit = threading.Event()
        self.worker = threading.Thread(target=self._work, daemon=True)

    def _work(self) -> None:
        while not self.quit.is_set():
            try:
                action = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            self.done.put(self.engine.execute(action, self.cancel.is_set))
        if self.engine.browser is not None:
            self.engine.browser.close()

    def hello(self) -> dict:
        body = info(self.engine, self.cfg)
        body["unacknowledged"] = self.engine.unacknowledged()
        st = self.client.call("POST", "/api/companion/hello", body)
        for r in body["unacknowledged"]:
            self.engine.acknowledge(r["id"])
        return st

    def run(self, once: bool = False) -> None:
        self.worker.start()
        last_hello = 0.0
        backoff = 1.0
        while not self.quit.is_set():
            try:
                if time.monotonic() - last_hello > 60:
                    st = self.hello()  # also tells ROOKS when a macOS permission was turned off
                    self.engine.config["screenshots"] = st.get("screenshots", True) and self.cfg.get("screenshots", True)
                    last_hello = time.monotonic()
                r = self.client.call("POST", "/api/companion/next", {}, timeout=40)
                backoff = 1.0
                action = r.get("action")
                if not action:
                    if once:
                        return
                    continue
                self.log(f"action #{action['id']}: {action['kind']}")
                self.cancel.clear()
                self.jobs.put(action)
                result = self._wait(action["id"])
                self.client.call("POST", "/api/companion/result", result)
                self.engine.acknowledge(action["id"])
                if once:
                    return
            except (OSError, RuntimeError) as e:
                self.log(f"connection problem: {e}; retrying in {backoff:.0f}s")
                if once:
                    raise
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                last_hello = 0.0  # say hello again: results finished while away are re-sent from the journal

    def _wait(self, aid: int) -> dict:
        """Wait for the worker, checking ROOKS for Stop about three times a second."""
        while True:
            try:
                return self.done.get(timeout=0.3)
            except queue.Empty:
                pass
            if os.path.exists(self.cfg.get("stop_file", "")):
                self.cancel.set()
            try:
                st = self.client.call("GET", "/api/companion/state", timeout=5)
                if st.get("stop") or st.get("paused") or aid in st.get("cancel", []):
                    self.cancel.set()
            except (OSError, RuntimeError):
                pass

    def stop(self) -> None:
        self.quit.set()
        self.cancel.set()
