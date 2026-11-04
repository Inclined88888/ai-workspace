"""Re-run every acceptance test behind a claim and write docs/evidence/EVIDENCE.md: for each item the commit that
delivered it, the test file, the exact command, and the raw pytest output from this checkout's HEAD.

    python scripts/audit/evidence.py            (from the repo root; provider keys are stripped from the environment)
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
BACKEND, ROOKS = "workbench/backend", "rooks-code"

# (id, claim, delivering commits, [(cwd, test path or nodeid)])
ITEMS = [
    ("SLICE", "Terminal turn runs on the app's shared loop; local file/shell tools delegated back with diff review and "
              "approval in the terminal; one approval shared by terminal and app; per-request ids",
     "a227272 (feat/session-approval-slice), 135e095 + 0ef30a2 (web approvals live + labels)",
     [(BACKEND, "tests/test_rooks_shared_loop.py"), (ROOKS, "tests/test_shared_loop_e2e.py")]),
    ("B1", "Team lead over AI sub-agents: split, parallel read-only sub-agents on the Jobs board, synthesis back to chat",
     "27aef95 + 19651f8 (feat/diff-B1), 66f646f (merge)", [(BACKEND, "tests/test_team_lead_subagents.py")]),
    ("B2", "Knowledge-graph memory: multi-hop traversal for the user (API) and the model (explore_graph tool)",
     "3cbc117 (feat/diff-B2-kg)", [(BACKEND, "tests/test_knowledge_graph_traverse.py")]),
    ("B3", "Packs from the terminal: rooks pack list/info/add/remove against the app's registry",
     "2fbeed0 (feat/diff-B3-packs)", [(ROOKS, "tests/test_pack_cli_e2e.py")]),
    ("B4", "Hand-off: app conversation continues in rooks (one-time code) and the answer shows back in the app",
     "73ac5da (feat/diff-B4-handoff)",
     [(ROOKS, "tests/test_handoff_e2e.py"),
      (BACKEND, "tests/test_rooks_shared_loop.py::test_the_terminal_reaches_an_app_chat_only_after_a_handoff")]),
    ("B5", "Egress audit from the terminal: rooks egress reports what left the machine, same numbers as the app",
     "31ef807 (feat/diff-B5-egress)",
     [(ROOKS, "tests/test_egress_cli_e2e.py"),
      (BACKEND, "tests/test_rooks_shared_loop.py::test_terminal_egress_audit_counts_external_hosts")]),
    ("B6", "Redaction from the terminal: rooks redact filter and --check gate; AWS and PEM private keys now redacted",
     "bccec7e (feat/diff-B6-redact)", [(ROOKS, "tests/test_redact_cli.py")]),
    ("FIX-base", "Failures present on base 64980bf, fixed: rooks login + streaming fake (merge loss), document taint "
                 "LIKE bug (safety gate), date-rot in portal watch, path-sensitive cli-agents test",
     "61ea132, ec8165c (fix/rooks-merge-regressions), e23a134/0905df8",
     [(ROOKS, "tests/test_sessions_stream_login.py"), (ROOKS, "tests/test_tui_pty.py"),
      (BACKEND, "tests/test_attachments.py"), (BACKEND, "tests/test_sites.py::test_plain_english_watch"),
      (BACKEND, "tests/test_cli_agents.py")]),
    ("CANCEL", "Stop from the app while rooks runs a delegated tool: the turn ends as stopped in the app and the terminal, the "
               "step is closed, the late result is dropped, the model is not asked again; Stop also lands during web tool steps",
     "726f968", [(ROOKS, "tests/test_shared_loop_e2e.py::test_stopping_in_the_app_while_the_terminal_runs_a_tool_ends_the_turn_everywhere"),
                 (BACKEND, "tests/test_chat_controls.py")]),
    ("L4-catalog", "models.dev price list download is off by default; Settings switch; env override", "1c89656",
     [(BACKEND, "tests/test_catalog_default_off.py")]),
    ("APP-TERM", "#7 terminal inside the app: real shell over a PTY WebSocket, signed in + same origin + loopback only, "
                 "no app secrets in its env, the tab's close ends the shell and its background jobs",
     "4e17ff4", [(BACKEND, "tests/test_app_terminal.py")]),
]


def env() -> dict:
    e = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN"))}
    e["MODEL_CATALOG_REFRESH"] = "0"
    return e


def run(cwd: str, target: str) -> tuple[str, int, str]:
    cmd = [PY, "-m", "pytest", "-v", "-p", "no:cacheprovider", "-W", "ignore", target]
    shown = f"cd {cwd} && {Path(PY).name} -m pytest -v -p no:cacheprovider -W ignore {target}"
    p = subprocess.run(cmd, cwd=ROOT / cwd, env=env(), capture_output=True, text=True, timeout=900)
    lines = [ln for ln in (p.stdout + p.stderr).splitlines() if " PASSED" in ln or " FAILED" in ln or " ERROR" in ln
             or ln.startswith(("FAILED", "ERROR")) or " passed" in ln or " failed" in ln]
    return shown, p.returncode, "\n".join(lines)


def main() -> int:
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = [f"# Evidence: acceptance tests re-run on {branch} @ {head}", "",
           f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by scripts/audit/evidence.py. "
           "Provider keys stripped from the environment; temp data dirs; scripted fake model servers.", ""]
    status = {}
    for iid, claim, commits, targets in ITEMS:
        out += [f"## {iid}: {claim}", "", f"- delivering commits: {commits}", f"- run at: {head}", ""]
        ok = True
        for cwd, target in targets:
            shown, rc, text = run(cwd, target)
            ok = ok and rc == 0
            out += [f"test: `{cwd}/{target.split('::')[0]}`", "", "```", f"$ {shown}", text, f"(exit {rc})", "```", ""]
        status[iid] = "PASS" if ok else "FAIL"
        out += [f"result: {status[iid]}", ""]
        print(f"{iid}: {status[iid]}", flush=True)
    # Browser checks (B1 and B2 screens, #7 in the page, axe-core, ARIA snapshots) come from scripts/audit/ui_screens.py,
    # run per engine: Chromium (browser) and WebKit (the engine of the macOS app window)
    for engine in ("chromium", "webkit"):
        txt = ROOT / "docs" / "evidence" / "ui" / f"{engine}.txt"
        res = ROOT / "docs" / "evidence" / "ui" / engine / "results.json"
        if txt.exists() and res.exists():
            import json

            at = json.loads(res.read_text()).get("commit", "?")
            body = txt.read_text().strip()
            ok = "FAIL" not in body and "PASS" in body
            status[f"UI-{engine}"] = "PASS" if ok else "FAIL"
            out += [f"## UI-{engine}: B1 run tree, B2 graph walk, #7 app terminal, axe-core WCAG A/AA and ARIA snapshots", "",
                    f"- run at: {at}; screenshots, axe JSON and ARIA trees in docs/evidence/ui/{engine}/", "",
                    "```", f"$ AXE_JS=axe-core-4.10.3/axe.min.js UI_BROWSER={engine} python scripts/audit/ui_screens.py",
                    body, "```", "", f"result: {status[f'UI-{engine}']}", ""]
            print(f"UI-{engine}: {status[f'UI-{engine}']}", flush=True)
    path = ROOT / "docs" / "evidence" / "EVIDENCE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")
    print(path)
    return 0 if all(v == "PASS" for v in status.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
