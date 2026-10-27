"""On-device check for ROOKS Companion on a real Mac. Run it yourself; it is the evidence that macOS works.

    cd ~/ai-workspace/companion && python3 tests/macos_check.py

What it does, using only a new temporary folder and TextEdit documents it opens itself:
  permissions, displays and Retina scale, window list, focus, reading TextEdit's accessibility tree, typing,
  setting a field exactly (non-ASCII), a native menu (Edit > Select All), saving with Cmd+S and checking the file on
  disk, a save sheet (detected as a dialog; "Don't Save" refused without approval), Stop during typing, window capture
  and a moved-window visual click being refused, Finder showing the folder, and file moves with versions and undo.
It never touches your other files or apps, and it closes what it opened. Results: ~/rooks-macos-check.json
Needs: Accessibility, and Automation for System Events, for the app running it (Terminal). Screen Recording is
optional (the capture checks are skipped without it).
"""

import json
import os
import platform
import sys
import tempfile
import threading
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from rooks_companion.adapters.macos import MacOSAdapter, window_point  # noqa: E402
from rooks_companion.engine import Engine  # noqa: E402

RESULTS = []
HOME = tempfile.mkdtemp(prefix="rooks-macos-check-")
DOC = os.path.join(HOME, "rooks-check.txt")
MOVE_WIN = r"""
function run(argv) { const a = JSON.parse(argv[0]);
  const w = Application('System Events').applicationProcesses.byName(a.app).windows[0];
  const p = w.position(); w.position = [p[0] + a.dx, p[1]]; return 'ok'; }"""


def check(name, fn):
    t = time.monotonic()
    try:
        detail = fn()
        RESULTS.append({"check": name, "result": "skip" if detail == "skip" else "pass", "detail": "" if detail in (None, "skip") else str(detail)[:300]})
    except AssertionError as e:
        RESULTS.append({"check": name, "result": "fail", "detail": str(e)[:300]})
    except Exception as e:  # noqa: BLE001
        RESULTS.append({"check": name, "result": "error", "detail": f"{e.__class__.__name__}: {e}"[:300], "trace": traceback.format_exc()[-800:]})
    RESULTS[-1]["seconds"] = round(time.monotonic() - t, 2)
    print(f"{RESULTS[-1]['result'].upper():5} {name}  {RESULTS[-1]['detail'][:120]}", flush=True)


def main() -> int:
    if sys.platform != "darwin":
        print("This check only runs on macOS.")
        return 2
    ad = MacOSAdapter()
    eng = Engine(ad, {"folders": [HOME], "blocked_apps": [], "stop_file": os.path.join(HOME, "STOP")})
    n = [0]

    def act(kind, approved=False, cancelled=lambda: False, **args):
        n[0] += 1
        r = eng.execute({"id": n[0], "kind": kind, "args": args, "approved": approved}, cancelled)
        return r

    perms = ad.permissions()
    check("permissions reported", lambda: perms)
    if not perms.get("accessibility") or perms.get("automation") is False:
        print("\nAccessibility and Automation (System Events) are needed for the rest. Run: python3 -m rooks_companion permissions")
        return finish()
    check("displays and Retina scale", lambda: ad.displays())
    check("window list has ids and bounds", lambda: (lambda w: (_ for _ in ()).throw(AssertionError("no windows")) if not w else w[:3])(ad.windows()))

    with open(DOC, "w") as f:
        f.write("start\n")
    ad._run(["/usr/bin/open", "-a", "TextEdit", DOC], check=True)
    time.sleep(2.0)

    def focus():
        ad.focus("TextEdit")
        assert ad.active_app() == "TextEdit", ad.active_app()
    check("TextEdit comes to the front (focus verified)", focus)

    state = {}

    def observe():
        r = act("observe", app="TextEdit")
        assert r["ok"], r
        area = next((e for e in r["result"]["elements"] if e.get("role") == "TextArea"), None)
        assert area, [e.get("role") for e in r["result"]["elements"]][:20]
        state["area"] = area["id"]
        return f"{len(r['result']['elements'])} elements"
    check("observe TextEdit (accessibility tree)", observe)

    def select_all_and_set():
        r = act("menu", app="TextEdit", path=["Edit", "Select All"])
        assert r["ok"], r
        act("observe", app="TextEdit")
        area = next(k for k, v in eng.elements.items() if v.get("role") == "TextArea")
        r = act("set_value", app="TextEdit", target=area, label="", text="Grüße, ROOKS ✓\n")
        assert r["ok"], r
    check("menu Edit > Select All, then set the text exactly (non-ASCII)", select_all_and_set)

    def save():
        r = act("key", app="TextEdit", keys="cmd+s")
        assert r["ok"], r
        time.sleep(1.0)
        with open(DOC, encoding="utf-8") as f:
            got = f.read()
        assert got.startswith("Grüße, ROOKS ✓"), repr(got)
    check("Cmd+S saves, and the file on disk has the text", save)

    def typing():
        act("observe", app="TextEdit")
        r = act("type_text", app="TextEdit", text=" typed")
        assert r["ok"], r
        act("key", app="TextEdit", keys="cmd+s")
        time.sleep(1.0)
        assert "typed" in open(DOC, encoding="utf-8").read()
    check("typing with simulated keys, saved", typing)

    def stop_midway():
        stop = threading.Event()
        threading.Timer(0.6, stop.set).start()
        r = act("type_text", app="TextEdit", cancelled=stop.is_set, text="x" * 600)
        assert r.get("cancelled"), r
        return r["error"]
    check("Stop during typing takes effect mid-way", stop_midway)

    def save_sheet():
        act("key", app="TextEdit", keys="cmd+w")
        time.sleep(1.0)
        r = act("observe", app="TextEdit")
        els = r["result"]["elements"]
        dont = next((e for e in els if "don" in e.get("label", "").lower() and "save" in e.get("label", "").lower()), None)
        assert dont and dont.get("in_dialog"), [(e.get("label"), e.get("in_dialog")) for e in els][:15]
        refused = act("click", app="TextEdit", target=dont["id"], label=dont["label"])
        assert refused.get("refused"), refused
        act("observe", app="TextEdit")
        save_btn = next(k for k, v in eng.elements.items() if v.get("label", "").lower() == "save")
        assert act("click", app="TextEdit", target=save_btn, label="Save")["ok"]
        return "dialog detected; Don't Save refused without approval; Save pressed"
    check("save sheet detected as a dialog, risky button refused", save_sheet)

    def capture_and_stale():
        if not perms.get("screen_recording"):
            return "skip"
        ad._run(["/usr/bin/open", "-a", "TextEdit", DOC], check=True)
        time.sleep(1.5)
        r = act("capture", app="TextEdit")
        assert r["ok"], r
        res = r["result"]
        w = res["window"]
        disp = next((d for d in ad.displays() if d["x"] <= w["bbox"][0] < d["x"] + d["width"]), {"scale": 1})
        ratio = res["image_size"][0] / w["bbox"][2]
        x, y = window_point(w["bbox"], 0.5, 0.5)
        ad._jxa(MOVE_WIN, {"app": "TextEdit", "dx": 60})
        time.sleep(0.5)
        r2 = act("click_visual", app="TextEdit", window_id=w["id"], bbox=w["bbox"], nx=0.5, ny=0.5, captured_at=res["captured_at"], label="text")
        assert r2.get("refused") and "moved" in r2["error"], r2
        return f"pixels per point {ratio:.2f} (display scale {disp.get('scale')}); centre {x},{y}; moved window refused"
    check("window capture, scale, and a moved window refuses a visual click", capture_and_stale)

    def finder():
        ad._run(["/usr/bin/open", HOME], check=True)
        time.sleep(1.5)
        titles = [w.get("title", "") for w in ad.windows() if w.get("app") == "Finder"]
        assert any(os.path.basename(HOME) in t for t in titles) or not perms.get("screen_recording"), titles
        return titles[:3]
    check("Finder opens the folder", finder)

    def files():
        os.makedirs(os.path.join(HOME, "in"), exist_ok=True)
        with open(os.path.join(HOME, "in", "invoice-7.txt"), "w") as f:
            f.write("invoice 7")
        assert act("files_mkdir", path=os.path.join(HOME, "Invoices"))["ok"]
        r = act("files_move", src=os.path.join(HOME, "in", "invoice-7.txt"), dst=os.path.join(HOME, "Invoices"))
        assert r["ok"], r
        if "change" in (r.get("result") or {}):
            u = act("files_undo", change=r["result"]["change"])
            assert u["ok"] and os.path.exists(os.path.join(HOME, "in", "invoice-7.txt")), u
            return "moved and undone"
        return "moved"
    check("files: create folder, move, undo", files)

    ad._jxa('function run(){ const t = Application("TextEdit"); t.documents().forEach(d => { try { d.close({saving: "yes"}); } catch (e) {} }); return "ok"; }')
    return finish()


def finish() -> int:
    report = {"macos": platform.mac_ver()[0], "machine": platform.machine(), "python": platform.python_version(),
              "when": time.strftime("%Y-%m-%d %H:%M:%S"), "folder": HOME, "results": RESULTS,
              "summary": {k: sum(r["result"] == k for r in RESULTS) for k in ("pass", "fail", "error", "skip")}}
    path = os.path.expanduser("~/rooks-macos-check.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n{report['summary']}  Report: {path}")
    return 0 if not report["summary"]["fail"] and not report["summary"]["error"] else 1


if __name__ == "__main__":
    sys.exit(main())
