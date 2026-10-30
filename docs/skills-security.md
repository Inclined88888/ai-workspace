# Skills from GitHub: how the security gate works

Workbench can install skills (a `SKILL.md` folder with optional helper scripts) and MCP servers straight from GitHub.
Nothing from GitHub runs, and nothing is added to the assistant, until it has passed the gate below **and** you have
approved it. The code is in `workbench/backend/app/skills_hub.py` (pipeline and API), `skill_scan.py` (scanners and
risk) and `skill_sandbox.py` (isolation). The UI is on the **Skills** page (`/skills`).

## The eight stages

| # | Stage | What it does | Where |
|---|-------|--------------|-------|
| 1 | **Fetch, pinned** | Resolves the link to one commit SHA and downloads that tarball over HTTPS with httpx. Limits: 60 MB tarball, 20 MB / 1,500 files per skill. Symlinks, hard links, devices and paths that escape the folder are dropped. From then on, every step uses the pinned copy, and install copies those reviewed files. It never fetches again. | `skills_hub.resolve`, `download` |
| 2 | **Code scan** | Built-in rules always run: secret files, environment dumps, writes outside the folder, persistence (cron, launchd, systemd, shell rc), privilege escalation, destructive deletes, decode-and-run, `curl \| sh`, obfuscation (long base64, hex or char-code strings), crypto miners, shell exec and network calls. Bundled binaries are flagged. If `bandit`, `semgrep`, `gitleaks` or `osv-scanner` is on PATH, their findings are added too. | `skill_scan.static_scan` |
| 3 | **Prompt-injection scan** | Reads `SKILL.md` and the other text for instructions aimed at the assistant: "ignore previous instructions", hiding actions from the user, exfiltration requests, hidden HTML comments and zero-width or bidi characters. | `skill_scan.injection_scan` |
| 4 | **What it can touch** | Builds a permission manifest (hosts, paths outside the folder, environment variables, tools, shell) from the declared `allowed-tools` and from the code. XML namespace URIs are not counted as network access. Undeclared capabilities are listed separately. | `skill_scan.permissions` |
| 5 | **Sealed trial run** | Runs every helper script once in the sandbox with **no** grants, and records every blocked attempt (network, secret file, write outside, subprocess). This catches behaviour that static rules miss, such as the `mal-runtime-only` fixture, which builds `socket` with `importlib` at run time. | `skill_sandbox.run` |
| 6 | **Risk verdict** | Low / Medium / High, each with the reasons shown to you. **High** comes from any of these: a critical finding; a high-severity injection, credential or exfiltration finding; two or more high findings; or a trial run that tried the network, a secret file, a write outside its folder or persistence. Medium comes from internet access, obfuscation, undeclared access or a single high finding. | `skill_scan.risk` |
| 7 | **Your approval** | You choose which permissions to grant, one checkbox each. Low and Medium come pre-ticked with what the report found. **High is blocked**: every box starts unticked, and "Install anyway" stays disabled until you type `install <name> anyway`. On an update, any access the update newly asks for is never pre-ticked. | Skills page, `POST /api/hub/skills/{id}/install` |
| 8 | **Run-time enforcement + audit** | Installed scripts always run in the sandbox with only the granted permissions. Anything else is blocked and turned into a request on **Skills → Activity** ("Allow from now on" / "Keep blocked"). Every check, install, update, rejection, run and decision is written to the audit log. | `skills_hub.run_script`, `skill_audit` table |

**Updates** ("Check for update") run all eight stages again on the new commit. They show a file diff and any new
permissions, and the installed version keeps running until you approve.

**Disable** removes the skill from the assistant and MCP but keeps the files. **Uninstall** deletes the files and
records an audit entry.

## Sandbox layers

1. **Linux:** bubblewrap (`bwrap`). Gives a private filesystem view: system folders are read-only, the skill gets its own scratch copy, and there is no real home folder. Network namespaces are unshared unless network was granted. `unshare -rn` is the fallback when bwrap is missing.
2. **Every OS:** the script gets
   - a clean environment: no API keys, and `HOME` points at scratch
   - a 20 s timeout, plus CPU and 1 GB memory rlimits
   - command shims that refuse `curl`, `wget`, `nc`, `ssh`, `crontab`, `launchctl`, `systemctl`, `sudo` and similar unless granted
3. **Python scripts** also run under a `sys.addaudithook` guard. It checks every connect, DNS lookup, file open and subprocess against the grants, blocks the ones outside them, and reports each attempt. The report hides the scratch path: `~/.ssh/id_rsa` is shown rather than `/tmp/skill-sandbox-…`.

## How skills reach the assistant and Rooks Code

- **Assistant:** installed, enabled skills appear in its skill list.
  - `load_skill` returns the steps.
  - `run_skill_script` runs a helper in the sandbox with the grants. If a request is blocked, the reply tells the assistant to send you to the Skills page.
- **Rooks Code and other MCP clients:** the Workbench MCP server exposes the same `load_skill` and `run_skill_script`.
- **MCP-server skills:** these get a launch config from `GET /api/hub/mcp-config`. Copy it into your client yourself; Workbench does not launch them.

## Known gaps (honest list)

- **Strong isolation is Linux-only.** On macOS (`sandbox-exec` profiles) and Windows (AppContainer / job objects) the design is done but not wired up yet. There, only layers 2 and 3 apply.
- **The audit hook covers Python only.** Shell and Node scripts rely on bwrap, the shims and the clean environment.
- **Network grants are host-based.** When network is granted, only the DNS host is checked against the grant. There is no egress proxy, so the payload is not inspected.
- **MCP servers are not trial-run.** They are scanned only, never started by the gate. `npx` / `uvx` launch configs can fetch code that was never reviewed, and the report says so.
- **External scanners are optional.** None ship with Workbench, so a stock install uses the built-in rules only.
- **Branch names containing `/` are not supported in links.** Use the commit SHA or the default branch instead.
- **Static rules are heuristics.** A determined attacker can evade them. That is why the trial run, the grants and the run-time enforcement exist, and why High-risk installs need a typed override.

## Test fixtures

The fixtures are in `workbench/backend/tests/fixtures/skills/`.

- The `mal-*` folders are deliberately malicious samples, one per attack class:
  - key stealer
  - curl pipe to shell
  - obfuscated exec
  - persistence
  - prompt injection
  - runtime-only exfiltration
- They point at reserved `example.net` hosts and only ever run inside the sandbox.
- `tests/test_skill_scan.py` and `tests/test_skills_hub.py` assert that:
  - every malicious fixture comes out **High** and is refused without the typed phrase
  - the benign fixtures come out **Low** and install
