# Terminal and workspace files

The assistant can run commands and create, read and edit files in its own workspace folder, then use the results
to keep going: run the tests, read the failure, fix the file, run them again.

## Where it runs

Commands and file changes happen in the **runner**, a separate container (`workbench/runner`). It has the
`workspace` volume and nothing else. It holds no ROOKS database, no `APP_SECRET`, and no API keys, and it
publishes no port. Only ROOKS reaches it, on a network the two share, and only with `RUNNER_TOKEN`.

So a command, even a bad one or one an injected instruction slipped in, cannot read your credentials or your
ROOKS data. The runner checks this about itself: if it can see ROOKS secrets or the database, Settings,
**Workspace and terminal** shows a warning.

The runner can reach the internet, so commands like `pip install` work. `./setup.sh --runner-offline` cuts it off
(it adds `docker-compose.runner-offline.yml`; `--runner-online` undoes it). The runner checks its own internet access
and Settings shows the result.

There is no sudo in the runner, by design. To add system packages, list them in `.env` as
`RUNNER_PACKAGES=build-essential nodejs npm` and run `./setup.sh`. They are installed when the runner image is built,
and only plain package names are accepted.

Setup: `./setup.sh` creates `RUNNER_TOKEN` (existing installs get it on their next setup run). Docker Compose then
builds and starts the runner with the rest. Without the token, the assistant simply has no terminal.

## What the assistant can do

| Tool | What it does |
|---|---|
| `run_command` | `bash -c` in the workspace (or a folder inside it). Returns exit code, stdout, stderr and duration. |
| `workspace_list` | Lists a folder. |
| `workspace_read` | Shows a text file as numbered lines, with `offset` and `limit` for long files. |
| `workspace_write` | Creates a file. It won't replace an existing one unless `overwrite` is true, and then the old one is kept. |
| `workspace_edit` | Replaces exact text. It must match exactly once (or set `replace_all`), and the old file is kept. |

Rules the runner enforces:

* **Paths stay inside the workspace.** `../`, absolute paths and symbolic links that lead outside are refused.
* **`.rooks/` belongs to ROOKS.** It holds logs and previous versions. The assistant can read it, but its file
  tools can't write there.
* **Commands get no input.** stdin is closed, so they have to use non-interactive flags.
* **Commands get a clean environment.** Only PATH, HOME (the workspace), locale and settings such as no pager and
  no colour are passed in.
* **No sudo.** The runner is an unprivileged user.

## Permissions

Settings, **Permissions** has two groups.

| Group | Default | Meaning |
|---|---|---|
| Terminal | **Ask me first** | Every command waits for your approval, except commands on your allow list. **On** runs commands without asking, and **Off** removes the tool. |
| Workspace files | On | List and read freely. Writes and edits keep the previous version. |

Some things ask whatever you set:

* **Risky commands.** Anything that runs as another user, deletes recursively, writes to disks, pipes a download
  into a shell, changes or publishes git history, connects to another machine, publishes a package, can stop the
  system, opens permissions broadly, or changes services.
* **Anything after outside content.** Once web, email or file content has entered the conversation, every command
  and file change asks. So does anything after a download: `curl`, git clone, pull and fetch, package installs for
  pip, uv, npm, npx, go, cargo, gem, apt and others, and `docker pull`. Network code in a command, or in a file the
  assistant writes, counts too (Python `requests`, `urllib` or sockets, JavaScript `fetch`, and the like). With an
  offline runner nothing can come in, so this rule is not needed there.

  A script that was already in the workspace and downloads something can't be detected from its command line. Use
  the offline runner if that matters to you.
* **Runs without you watching.** Schedules, Telegram and company agents ask before any command or file change.
  Missions follow the group's setting, and a mission waits while an approval is pending.

The allow list is in Settings, **Workspace and terminal**. Defaults include `ls`, `cat`, `grep`, `git status` and
`git diff`. A command matches only if it starts with an entry and contains no `;`, `|`, `&`, redirection or
`$(...)`. Risky commands can't be added to it.

## Timeouts, failures, Stop, large output, restarts

| Situation | What happens |
|---|---|
| Timeout | Default 120 seconds (changeable in Settings), at most 900. A request has 300 seconds in total unless `WORKBENCH_TURN_SECONDS` is raised. The command and every process it started are stopped together. |
| A failed command | Shows as a failed step, with its exit code, stdout and stderr, so the assistant can act on it. |
| Stop in chat | Stops a running command within a second, children included. |
| Large output | The assistant gets the start and the end (errors are usually at the end) and the path of a log file with everything, which it can read with `workspace_read`. |
| Secrets in output | What the model sees from commands and files is redacted. That covers known key formats, `.env`-style `NAME=value` lines whose name says secret, token, password or key, the same in JSON and YAML, passwords inside URLs, whole private key blocks, and long random-looking tokens. Git hashes and package checksums are kept. The full log file in the workspace is not redacted. Redaction is a safety net, not a guarantee. |
| ROOKS restarts mid-command | The runner notices the request is gone and stops the command, so nothing runs on unattended. A mission resumed after a restart is told to check the workspace before running the command again. Commands are never "replayed" from a record: rerunning the tests really reruns them. |
| Step limit | Ordinary chat keeps 12 model steps per request (`WORKBENCH_MAX_STEPS`). Once a request uses the terminal or workspace files it may go on to 25 (`WORKBENCH_MAX_STEPS_WORKSPACE`), to test and fix. |

## In the interface

Each command appears as a step in the chat. It shows the command, an exit badge (green `exit 0`, red for other
codes, amber for timed out or stopped) and the output in a terminal panel. Failed steps open automatically.
Approvals show the exact command. Settings, **Workspace and terminal** shows:

* the runner's status and isolation;
* the allow list and the default timeout;
* a read-only browser of the workspace files.

## Tests

* `workbench/backend/tests/test_workspace.py` (15 tests) runs a real runner process with real commands. It covers:
  * a multi-step write, fail, fix and rerun task;
  * failed commands (exit 3, command not found);
  * ask mode, the allow list and chaining;
  * risky commands, outside content, Off, and unattended runs;
  * workspace confinement, including a symlink escape;
  * overwrite and version keeping, and exact-match edits;
  * a timeout that stops child processes, large output with the log file, and Stop;
  * a command ROOKS stopped waiting for being stopped;
  * no secrets in a command's environment, output redaction, and runner authentication;
  * download and network-code detection, and offline mode;
  * `.env` secrets redacted in reads and command output;
  * the step limits.
* The browser suite `ui_terminal.py` (17 checks; a scratch harness, not committed) drives the real interface:
  * switching Terminal on in Permissions;
  * running the multi-step task and a failing command from chat;
  * approving a command in chat;
  * the Settings card and the file browser;
  * phone width, and no secrets on the page.

**Not verified here:** building and running the runner container with Docker (the test environment had no Docker
daemon). The same `runner.py` was tested as a plain process.
