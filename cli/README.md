# rooks: ROOKS in the terminal

The same assistant, conversations, work and approvals as the ROOKS web app. A conversation you start here is in the web
app's sidebar; a job you start here shows in the browser while it runs. The CLI has no model, tools or memory of its
own: it talks to your ROOKS server (API version 1), and the server's permissions and approvals apply.

## Install

Python 3.10 or later. No other packages are needed.

```sh
pip install "git+https://github.com/Inclined88888/ai-workspace@development/rooks-v15#subdirectory=cli"
# or, from a checkout:   pip install ./cli
# keep the token in the system keychain:   pip install "./cli[keyring]"
```

## Connect

In ROOKS, open Memory and skills (in the sidebar, under Workspace), then Assistant settings, Terminal access, and make a token. Then:

```sh
rooks connect http://localhost:3100       # asks for the token; it is not shown and not kept in shell history
rooks doctor                              # checks the address, token, API version and AI model
```

For scripts and CI: `echo "$TOKEN" | rooks connect http://server:3100 --token-stdin`, or set `ROOKS_URL` and
`ROOKS_TOKEN` (never written to disk).

## Use

```sh
rooks                                     # a conversation (Ctrl+C stops an answer; /new, /jobs, /quit)
rooks ask "What should I work on today?"
rooks ask -c "And after that?"            # continue your last terminal conversation
rooks ask --conversation 42 "..."         # continue any conversation, including one from the web app
rooks ask -f sales.csv "Which region sells most?"
rooks run "Research instant asset write-off rules and write a summary"   # waits for the work to finish
rooks status                              # model, review count, work in progress, your computer
rooks jobs [--active] | rooks jobs show research:12 | watch | cancel | retry
rooks approvals | rooks approve 7 | rooks deny 7
rooks conversations | rooks show 42 | rooks projects
rooks disconnect
```

Add `--json` for machine-readable output on stdout (progress goes to stderr) and `--no-input` to never prompt.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done |
| 1 | ROOKS reported an error |
| 2 | wrong usage |
| 3 | not connected, or the token was refused |
| 4 | an action is waiting for your approval (with `--no-input`, or when no terminal can ask) |
| 5 | the work failed or was stopped |
| 6 | the server cannot be reached |
| 7 | the server speaks another API version: update the client or the server |
| 124 | timed out (`--timeout`, default 600 seconds) |
| 130 | interrupted |

## How it behaves

- **Approvals.** When ROOKS needs your approval, the CLI shows exactly what will run and asks. Without a terminal it
  never approves anything by itself: it exits with 4 and you decide with `rooks approve ID` (which needs `--yes` when
  nothing can ask you).
- **Safe retries.** Every request carries an idempotency key. If the connection drops mid-answer, the CLI asks again
  with the same key and the server answers from what it already did; nothing runs twice.
- **Versions.** The CLI sends its API version; a server that does not speak it answers with what to update (exit 7).
- **Where the token lives.** In the system keychain with the `keyring` extra; otherwise in
  `~/.config/rooks/config.json` (Windows: `%APPDATA%\rooks\config.json`), readable only by you (`rooks doctor` checks).

The older single-file client `cli/ws` still works and is unchanged.
