"""ROOKS Companion command line.

  python3 -m rooks_companion pair http://localhost:3100 CODE   pair with ROOKS (code from ROOKS, under Computer)
  python3 -m rooks_companion run                               connect and work until Ctrl+C
  python3 -m rooks_companion check                             show permissions and what works on this computer
  python3 -m rooks_companion permissions                       open the macOS permission settings
  python3 -m rooks_companion allow-folder ~/Documents/Invoices let ROOKS use a folder (nothing is deleted)
  python3 -m rooks_companion block-app "Notes"                 never let ROOKS control an app
  python3 -m rooks_companion read-only on|off                  look only, change nothing
  python3 -m rooks_companion allow-claude-api on|off           let ROOKS run Claude Code with your ANTHROPIC_API_KEY (billed per use)
  python3 -m rooks_companion stop | resume                     emergency stop on this computer (works without ROOKS)
  python3 -m rooks_companion forget                            remove the pairing token from this computer
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys

from . import VERSION
from .adapters import detect
from .browser import Browser
from .engine import Engine
from .runner import Client, Runner, info

HOME = os.environ.get("ROOKS_COMPANION_HOME", os.path.expanduser("~/.rooks-companion"))
CONFIG = os.path.join(HOME, "config.json")


def load() -> dict:
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    os.chmod(HOME, 0o700)  # holds the pairing token: only you may read it
    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            cfg = json.load(f)
    cfg.setdefault("folders", [])
    cfg.setdefault("blocked_apps", [])
    cfg["stop_file"] = os.path.join(HOME, "STOP")
    cfg["versions_dir"] = os.path.join(HOME, "versions")
    return cfg


def save(cfg: dict) -> None:
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    data = {k: v for k, v in cfg.items() if k not in ("stop_file", "versions_dir")}
    fd = os.open(CONFIG + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(CONFIG + ".tmp", CONFIG)


def build_engine(cfg: dict) -> Engine:
    adapter = None
    try:
        adapter = detect()
    except Exception as e:  # noqa: BLE001
        print(f"Desktop control unavailable: {e}")
    browser = None
    if Browser.available() and not cfg.get("no_browser"):
        browser = Browser(os.path.join(HOME, "browser-profile"), headless=bool(cfg.get("headless_browser")),
                          executable=cfg.get("browser_executable") or None)
    return Engine(adapter, cfg, browser, os.path.join(HOME, "journal.json"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rooks-companion", description=f"ROOKS Companion {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair"); p.add_argument("url"); p.add_argument("code"); p.add_argument("--name", default=socket.gethostname())
    sub.add_parser("run"); sub.add_parser("check"); sub.add_parser("permissions"); sub.add_parser("stop"); sub.add_parser("resume")
    sub.add_parser("forget")
    p = sub.add_parser("allow-folder"); p.add_argument("path")
    p = sub.add_parser("remove-folder"); p.add_argument("path")
    p = sub.add_parser("block-app"); p.add_argument("app")
    p = sub.add_parser("read-only"); p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("allow-claude-api"); p.add_argument("state", choices=["on", "off"])
    a = ap.parse_args(argv)
    cfg = load()

    if a.cmd == "pair":
        engine = build_engine(cfg)
        r = Client(a.url).call("POST", "/api/companion/pair", {"code": a.code, "name": a.name, "info": info(engine, cfg)})
        cfg.update(url=a.url.rstrip("/"), token=r["token"], name=a.name)
        save(cfg)
        print(f"Paired with ROOKS at {a.url} as '{a.name}'. Start it with: python3 -m rooks_companion run")
        return 0
    if a.cmd == "check":
        engine = build_engine(cfg)
        print(json.dumps(info(engine, cfg), indent=2))
        return 0
    if a.cmd == "permissions":
        engine = build_engine(cfg)
        if engine.adapter is not None and hasattr(engine.adapter, "request_permissions"):
            engine.adapter.request_permissions()
            print("System Settings is open. Turn on Accessibility (and Screen Recording for previews) for the app running this, then run 'check'.")
        else:
            print("This computer has no permission prompts to open.")
        return 0
    if a.cmd == "stop":
        open(cfg["stop_file"], "w").close()
        print("Stopped. ROOKS cannot do anything on this computer until you run: python3 -m rooks_companion resume")
        return 0
    if a.cmd == "resume":
        if os.path.exists(cfg["stop_file"]):
            os.remove(cfg["stop_file"])
        print("Resumed.")
        return 0
    if a.cmd == "forget":
        cfg.pop("token", None)
        save(cfg)
        print("Pairing removed from this computer. Remove it in ROOKS too (Computer, your devices).")
        return 0
    if a.cmd == "allow-folder":
        path = os.path.realpath(os.path.expanduser(a.path))
        if not os.path.isdir(path):
            print(f"{path} is not a folder."); return 1
        if path in ("/", os.path.expanduser("~")):
            print("Allow a specific folder, not your whole disk or home folder."); return 1
        cfg["folders"] = sorted(set(cfg["folders"]) | {path}); save(cfg); print(f"ROOKS may use {path} (never deletes)."); return 0
    if a.cmd == "remove-folder":
        path = os.path.realpath(os.path.expanduser(a.path))
        cfg["folders"] = [f for f in cfg["folders"] if f != path]; save(cfg); print("Removed."); return 0
    if a.cmd == "block-app":
        cfg["blocked_apps"] = sorted(set(cfg["blocked_apps"]) | {a.app}); save(cfg); print(f"ROOKS will never control {a.app}."); return 0
    if a.cmd == "read-only":
        cfg["read_only"] = a.state == "on"; save(cfg); print(f"Read only {a.state}."); return 0
    if a.cmd == "allow-claude-api":
        cfg["claude_api_billing"] = a.state == "on"; save(cfg)
        print("ROOKS may run Claude Code with the ANTHROPIC_API_KEY set for the companion. Anthropic bills that key per use; "
              "your Claude subscription is never used." if a.state == "on" else "ROOKS will not run Claude Code."); return 0
    if a.cmd == "run":
        if not cfg.get("token"):
            print("Not paired yet. In ROOKS open Computer, press Pair, and run the command it shows."); return 1
        engine = build_engine(cfg)
        perms = info(engine, cfg)["permissions"]
        if perms.get("notes"):
            print(perms["notes"])
        print(f"ROOKS Companion {VERSION} connected to {cfg['url']}. Press Ctrl+C to stop.")
        runner = Runner(Client(cfg["url"], cfg["token"]), engine, cfg)
        try:
            runner.run()
        except KeyboardInterrupt:
            runner.stop()
            print("Stopped.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
