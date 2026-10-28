"""Runs one action at a time: validates it, applies this computer's own rules, does it, and reports what happened.

* Elements get ids ("e1", "e2"...) from each observation and expire after any change, so a click never lands where
  a button used to be. A click also checks the element still has the label the request named.
* Long actions (typing, scrolling, waiting) check for Stop between small steps, so Stop takes effect mid-action.
* Each result is written to a journal before it is reported. If ROOKS restarts, the same action id is never run
  twice: the journal's result is reported again instead.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time

from .adapters.base import Adapter, NotSupported
from .policy import WRITE_KINDS, Refused, app_allowed, check_element, label_matches

KEY_RE = re.compile(r"^((cmd|ctrl|alt|option|shift|fn|super|meta)\+){0,3}([a-z0-9]|f[0-9]{1,2}|enter|return|tab|esc|escape|space|"
                    r"backspace|delete|up|down|left|right|home|end|pageup|pagedown|[\[\]\-=;',./`\\])$")
MAX_TEXT = 5000
MAX_FILE = 1_000_000


class Cancelled(Exception):
    pass


class Engine:
    def __init__(self, adapter: Adapter | None, config: dict, browser=None, journal_path: str | None = None):
        self.adapter, self.config, self.browser = adapter, config, browser
        self.elements: dict[str, dict] = {}
        self.journal_path = journal_path
        self.journal = self._load_journal()
        self.lock = threading.Lock()
        from .agents import CodingAgents

        home = os.path.dirname(journal_path) if journal_path else os.path.expanduser("~/.rooks-companion")
        self.agents = CodingAgents(config, home)

    # Journal
    def _load_journal(self) -> dict:
        if self.journal_path and os.path.exists(self.journal_path):
            try:
                with open(self.journal_path) as f:
                    return json.load(f)
            except (OSError, ValueError):
                return {}
        return {}

    def _save_journal(self) -> None:
        if not self.journal_path:
            return
        items = sorted(self.journal.items(), key=lambda kv: int(kv[0]))[-200:]
        self.journal = dict(items)
        tmp = self.journal_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.journal, f)
        os.replace(tmp, self.journal_path)

    def unacknowledged(self) -> list[dict]:
        return [v["result"] for v in self.journal.values() if not v.get("acknowledged")]

    def acknowledge(self, aid: int) -> None:
        if str(aid) in self.journal:
            self.journal[str(aid)]["acknowledged"] = True
            self._save_journal()

    # Capabilities
    def capabilities(self) -> list[str]:
        caps = []
        if self.adapter is not None and self.adapter.name != "windows":
            caps.append("desktop")
        if self.browser is not None:
            caps.append("browser")
        if self.config.get("folders"):
            caps.append("files")
        import shutil

        if self.config.get("folders") and any((self.config.get("coding_agents") or {}).get(n) or shutil.which(n) for n in ("codex", "claude")):
            caps.append("coding")
        return caps

    # Running actions
    def execute(self, action: dict, cancelled=lambda: False) -> dict:
        """Run one action and return {id, ok, result, error, refused, cancelled, screenshot?}. Never raises."""
        aid = action["id"]
        done = self.journal.get(str(aid))
        if done:
            return {**done["result"], "replayed": True}  # already ran: report, do not repeat
        started = time.monotonic()
        out = {"id": aid, "ok": False}
        try:
            if os.path.exists(self.config.get("stop_file", "")):
                raise Cancelled("Stopped on this computer (the STOP file exists).")
            if cancelled():
                raise Cancelled("Stopped by you.")
            with self.lock:
                out["result"] = self._run(action["kind"], action.get("args") or {}, bool(action.get("approved")), cancelled,
                                          float(action.get("timeout") or 30))
            out["ok"] = True
        except Cancelled as e:
            out.update(cancelled=True, error=str(e) or "Stopped.")
        except Refused as e:
            out.update(refused=True, error=str(e))
        except NotSupported as e:
            out.update(error=str(e) or "Not supported on this computer.")
        except Exception as e:  # noqa: BLE001 - report every failure instead of dying
            out.update(error=f"{e.__class__.__name__}: {str(e)[:300]}")
        out["ms"] = int((time.monotonic() - started) * 1000)
        if action["kind"] in WRITE_KINDS:
            self.elements = {}  # anything may have moved: ids expire
        if out.get("ok") and self.config.get("screenshots", True) and action["kind"] in ("observe", "screenshot") and self.adapter:
            try:
                shot = self.adapter.screenshot()
                if shot:
                    out["screenshot"] = "data:image/png;base64," + base64.b64encode(shot).decode()
            except Exception:  # noqa: BLE001 - a missing preview is not a failure
                pass
        self.journal[str(aid)] = {"result": {k: v for k, v in out.items() if k != "screenshot"}, "acknowledged": False}
        self._save_journal()
        return out

    def _files(self):
        from .files import FileOps

        vdir = self.config.get("versions_dir") or os.path.join(os.path.dirname(self.journal_path or os.path.expanduser("~/.rooks-companion/x")), "versions")
        return FileOps(self.config.get("folders", []), vdir)

    def _front_window(self, app: str) -> dict:
        wins = [w for w in self._need_desktop().windows() if w.get("app", "").lower() == app.lower()]
        if not wins:
            raise Refused(f"No window of {app} is open.")
        return next((w for w in wins if w.get("focused")), wins[0])

    def _capture(self, app: str) -> dict:
        """One window as an image for visual targeting, with password fields blacked out before it leaves this computer.
        Its id, bounds and time travel with it so a later click can tell if the window moved."""
        from . import imaging

        if not app:
            raise Refused("Say which app to capture.")
        if not self.config.get("vision", True):
            raise Refused("Visual targeting is turned off on this computer.")
        ad = self._need_desktop()
        if not hasattr(ad, "capture_window"):
            raise NotSupported("Window capture is not available here.")
        win = self._front_window(app)
        png = ad.capture_window(win["id"])
        if not png:
            raise NotSupported("Screen capture is not permitted on this computer (Screen Recording is off).")
        iw, ih = imaging.size(png)
        wx, wy, ww, wh = win["bbox"]
        sx, sy = iw / max(ww, 1), ih / max(wh, 1)
        rects = []
        try:
            for e in ad.elements(app):
                if e.get("secure"):
                    x, y, w, h = e["bbox"]
                    rects.append(((x - wx) * sx - 4, (y - wy) * sy - 4, w * sx + 8, h * sy + 8))
        except Exception:  # noqa: BLE001 - no accessibility: nothing known to redact, said below
            rects = None
        if rects:
            png = imaging.black_out(png, rects)
        import base64

        return {"image": "data:image/png;base64," + base64.b64encode(png).decode(), "window": {"id": win["id"], "app": win["app"],
                "title": win.get("title", ""), "bbox": win["bbox"]}, "image_size": [iw, ih], "captured_at": time.time(),
                "redacted": len(rects or []), "redaction": "password fields blacked out" if rects is not None else "no accessibility data: nothing could be redacted"}

    def _click_visual(self, a: dict, app: str, approved: bool) -> dict:
        """Click a point found in a screenshot, only if the window is still exactly where it was when captured."""
        ad = self._need_desktop()
        try:
            wid, bbox = int(a["window_id"]), [float(v) for v in a["bbox"]]
            nx, ny, at = float(a["nx"]), float(a["ny"]), float(a["captured_at"])
        except (KeyError, TypeError, ValueError):
            raise Refused("A visual click needs the window id, bounds, position and capture time from computer_locate.")
        if not (0 <= nx <= 1 and 0 <= ny <= 1):
            raise Refused("The position is outside the window.")
        if time.time() - at > 120:
            raise Refused("The screenshot is more than 2 minutes old. Locate the target again.")
        check_element({"label": str(a.get("label", ""))}, approved)
        now = next((w for w in ad.windows() if w.get("id") == wid), None)
        if not now:
            raise Refused("That window is gone. Locate the target again.")
        if now.get("app", "").lower() != app.lower():
            raise Refused(f"That window belongs to {now.get('app')}, not {app}.")
        if any(abs(float(p) - q) > 3 for p, q in zip(now["bbox"], bbox)):
            raise Refused("The window moved or changed size since the screenshot. Locate the target again.")
        x, y = int(round(bbox[0] + nx * bbox[2])), int(round(bbox[1] + ny * bbox[3]))
        ad.click_at(x, y)
        time.sleep(0.2)
        return {"after": {"clicked_at": [x, y], "method": "visual", "active_app": ad.active_app()}}

    def _need_desktop(self) -> Adapter:
        if self.adapter is None:
            raise NotSupported("Desktop control is not available on this computer.")
        return self.adapter

    def _write_allowed(self) -> None:
        if self.config.get("read_only"):
            raise Refused("This computer is set to read only for ROOKS.")

    def _element(self, target: str, label: str, app: str) -> dict:
        el = self.elements.get(target)
        if not el:
            raise Refused(f"{target} is not in the latest observation (elements expire after any change). Observe again.")
        if label and not label_matches(label, el.get("label", "")):
            raise Refused(f"{target} is '{el.get('label', '')}', not '{label}'. Observe again.")
        if app and el.get("app") and el["app"].lower() != app.lower():
            raise Refused(f"{target} belongs to {el['app']}, not {app}.")
        return el

    def _bring_forward(self, app: str) -> None:
        """Input goes to the frontmost window, so the target app comes forward before a click or keystroke, and input is
        only sent once it really is in front."""
        if app and self.adapter is not None and self.adapter.active_app().lower() != app.lower():
            self.adapter.focus(app)
            time.sleep(0.2)
            if self.adapter.active_app().lower() != app.lower():
                raise Refused(f"{app} is not in front (now: {self.adapter.active_app() or 'nothing'}), so nothing was sent to it.")

    def _run(self, kind: str, a: dict, approved: bool, cancelled, timeout: float):
        app = str(a.get("app") or "")
        app_allowed(app, self.config.get("blocked_apps", []))
        if kind in WRITE_KINDS:
            self._write_allowed()
        if kind in ("click", "type_text", "key", "scroll", "set_value", "menu", "click_visual", "capture"):
            self._bring_forward(app)

        if kind == "observe":
            ad = self._need_desktop()
            active = ad.active_app()
            app_allowed(app or active, self.config.get("blocked_apps", []))
            els = ad.elements(app)
            self.elements = {}
            for i, e in enumerate(els, 1):
                e.setdefault("app", app or active)
                self.elements[f"e{i}"] = e
            win = getattr(ad, "last_window", None)
            out = {"active_app": active, "windows": [w for w in ad.windows() if w.get("app", "").lower() not in {b.lower() for b in self.config.get("blocked_apps", [])}][:20],
                   "elements": [{"id": k, **{x: v[x] for x in ("role", "label", "value", "secure", "in_dialog", "enabled") if x in v}} for k, v in self.elements.items()]}
            if win:
                out["window"] = {k: win.get(k) for k in ("title", "dialog")}
            if any(e.get("in_dialog") for e in self.elements.values()) or (win or {}).get("dialog"):
                out["note"] = "A dialog or sheet is open: deal with it (or ask the user) before anything else."
            return out
        if kind == "set_value":
            ad = self._need_desktop()
            el = self._element(str(a.get("target", "")), str(a.get("label", "")), app)
            check_element(el, approved, typing=True)
            text = str(a.get("text", ""))
            if len(text) > MAX_TEXT:
                raise Refused(f"Text must be at most {MAX_TEXT} characters.")
            if not hasattr(ad, "set_value"):
                raise NotSupported("Setting a field directly is not available here; use typing.")
            ad.set_value(el, text)
            return {"after": {"set": True, "length": len(text)}}
        if kind == "menu":
            ad = self._need_desktop()
            path = a.get("path")
            if not isinstance(path, list) or not 1 <= len(path) <= 4 or not all(isinstance(p, str) and 0 < len(p) <= 80 for p in path):
                raise Refused("A menu path is 1 to 4 item names, e.g. ['File', 'Save As…'].")
            check_element({"label": path[-1]}, approved)
            if not hasattr(ad, "menu"):
                raise NotSupported("Menus are not available through this adapter.")
            ad.menu(app, path)
            return {"after": {"menu": " > ".join(path), "active_app": ad.active_app()}}
        if kind == "capture":
            return self._capture(app)
        if kind == "click_visual":
            return self._click_visual(a, app, approved)
        if kind == "screenshot":
            shot = self._need_desktop().screenshot()
            return {"note": "screenshot taken" if shot else "screen capture is not permitted on this computer"}
        if kind == "open_app":
            if not re.fullmatch(r"[\w .&'()-]{1,80}", app):
                raise Refused("That is not an application name.")
            self._need_desktop().open_app(app)
            return {"after": {"active_app": self._need_desktop().active_app()}}
        if kind == "focus":
            self._need_desktop().focus(app)
            return {"after": {"active_app": self._need_desktop().active_app()}}
        if kind == "click":
            ad = self._need_desktop()
            if a.get("target"):
                el = self._element(str(a["target"]), str(a.get("label", "")), app)
                check_element(el, approved)
                ad.press(el)
            elif self.config.get("coordinates") and "x" in a and "y" in a:
                ad.click_at(int(a["x"]), int(a["y"]))
            else:
                raise Refused("Click needs an element id from the latest observation.")
            time.sleep(0.2)
            return {"after": {"active_app": ad.active_app(), "windows": [w["title"] for w in ad.windows() if w.get("focused")]}}
        if kind == "type_text":
            ad = self._need_desktop()
            text = str(a.get("text", ""))
            if not text or len(text) > MAX_TEXT:
                raise Refused(f"Text must be 1 to {MAX_TEXT} characters.")
            if a.get("target"):
                el = self._element(str(a["target"]), str(a.get("label", "")), app)
                check_element(el, approved, typing=True)
                ad.press(el)
            if hasattr(ad, "focused_secure") and ad.focused_secure():
                raise Refused("The focused field is a password field. ROOKS never types into password fields.")
            for i in range(0, len(text), 20):  # small chunks so Stop takes effect mid-way
                if cancelled():
                    raise Cancelled(f"Stopped after typing {i} of {len(text)} characters.")
                ad.type_text(text[i:i + 20])
            return {"after": {"typed": len(text)}}
        if kind == "key":
            combo = str(a.get("keys", "")).lower().replace(" ", "")
            if not KEY_RE.match(combo):
                raise Refused(f"'{combo}' is not a supported key or shortcut.")
            self._need_desktop().key(combo)
            return {"after": {"active_app": self._need_desktop().active_app()}}
        if kind == "scroll":
            ad = self._need_desktop()
            direction = a.get("direction")
            if direction not in ("up", "down"):
                raise Refused("direction must be up or down")
            amount = max(1, min(int(a.get("amount") or 3), 20))
            for _ in range(amount):
                if cancelled():
                    raise Cancelled("Stopped while scrolling.")
                ad.scroll(direction, 1)
            return {"after": {"scrolled": amount}}
        if kind == "wait_for":
            ad = self._need_desktop()
            want = str(a.get("text", "")).lower()
            end = time.monotonic() + min(float(a.get("timeout") or 10), timeout, 60)
            while time.monotonic() < end:
                if cancelled():
                    raise Cancelled("Stopped while waiting.")
                titles = " ".join(w.get("title", "") for w in ad.windows() if not app or w.get("app", "").lower() == app.lower())
                labels = " ".join(f"{e.get('label', '')} {e.get('value', '')}" for e in ad.elements(app))
                if want in (titles + " " + labels).lower():
                    return {"note": f"'{a.get('text')}' appeared"}
                time.sleep(0.3)
            raise TimeoutError(f"'{a.get('text')}' did not appear in time.")

        # Browser
        if kind.startswith("browser_"):
            if self.browser is None:
                raise NotSupported("ROOKS's browser is not installed on this computer (pip install playwright).")
            b = self.browser
            if kind == "browser_open":
                return b.open(str(a.get("url", "")))
            if kind == "browser_snapshot":
                return b.snapshot()
            if kind == "browser_click":
                return b.click(str(a.get("ref", "")), str(a.get("label", "")), approved)
            if kind == "browser_fill":
                return b.fill(str(a.get("ref", "")), str(a.get("text", ""))[:MAX_TEXT], str(a.get("label", "")))
            if kind == "browser_select":
                return b.select(str(a.get("ref", "")), str(a.get("value", "")), str(a.get("label", "")))
            if kind == "browser_back":
                return b.back()

        # Coding agents (agents.py): official CLIs, one allowed folder, only after approval
        if kind == "agents_detect":
            return self.agents.detect()
        if kind == "agent_start":
            return self.agents.start(str(a.get("agent", "")), str(a.get("task", "")), str(a.get("folder", "")),
                                     self.config.get("folders", []), approved)
        if kind == "agent_poll":
            return self.agents.poll(str(a.get("run", "")), int(a.get("since") or 0))
        if kind == "agent_stop":
            return self.agents.stop(str(a.get("run", "")))

        # Files, only inside folders allowed on this computer; a version is kept before every change, nothing is deleted
        if kind.startswith(("files_", "sheet_", "doc_")):
            f = self._files()
            if kind == "files_list":
                p = f.path(str(a.get("path", "")))
                if not os.path.isdir(p):
                    raise Refused("That is not a folder.")
                return {"path": p, "entries": [{"name": n, "folder": os.path.isdir(os.path.join(p, n)),
                        "bytes": os.path.getsize(os.path.join(p, n)) if os.path.isfile(os.path.join(p, n)) else None}
                        for n in sorted(os.listdir(p))[:500]]}
            if kind == "files_read":
                p = f.path(str(a.get("path", "")))
                if os.path.getsize(p) > MAX_FILE:
                    raise Refused("That file is larger than 1 MB.")
                with open(p, encoding="utf-8", errors="replace") as fh:
                    return {"path": p, "content": fh.read()}
            if kind == "files_mkdir":
                return f.mkdir(str(a.get("path", "")))
            if kind in ("files_move", "files_copy"):
                return f.move_or_copy(kind[6:], str(a.get("src", "")), str(a.get("dst", "")), bool(a.get("overwrite")), approved)
            if kind == "files_write":
                return f.write(str(a.get("path", "")), str(a.get("text", "")), bool(a.get("overwrite")), approved)
            if kind == "files_history":
                return f.history(str(a.get("path", "")))
            if kind == "files_undo":
                return f.undo(str(a.get("change", "")))
            if kind == "sheet_read":
                return f.sheet_read(str(a.get("path", "")), str(a.get("sheet", "")), int(a.get("limit") or 200))
            if kind == "sheet_update":
                return f.sheet_update(str(a.get("path", "")), a.get("edits"), str(a.get("output", "")), approved, str(a.get("sheet", "")))
            if kind == "sheet_dedupe":
                return f.sheet_dedupe(str(a.get("path", "")), a.get("columns"), str(a.get("output", "")), approved, str(a.get("sheet", "")))
            if kind == "doc_read":
                return f.doc_read(str(a.get("path", "")))
            if kind == "doc_write":
                return f.doc_write(str(a.get("path", "")), a.get("text"), a.get("replace"), str(a.get("output", "")), approved)
        raise Refused(f"Unknown action {kind}.")
