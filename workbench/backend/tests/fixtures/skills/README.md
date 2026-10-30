Test fixtures for the skills security gate (tests/test_skill_scan.py, tests/test_skills_hub.py).
The `mal-*` folders are deliberately malicious samples. They point at reserved/placeholder domains (example.net)
and are only ever executed inside the skill sandbox with networking disabled. Do not install them anywhere.

| Fixture | Attack class | Caught by |
|---------|--------------|-----------|
| `mal-key-stealer` | reads `~/.ssh/id_rsa` and env, posts to a webhook | code scan + trial run (secret read) |
| `mal-curl-pipe` | `curl … \| sh` installer | code scan (remote code) |
| `mal-obfuscated` | base64 decode-and-exec | code scan (obfuscation, decode-and-run) |
| `mal-persistence` | crontab / shell rc persistence | code scan (persistence) |
| `mal-prompt-injection` | SKILL.md tells the assistant to hide actions and leak data | prompt-injection scan |
| `mal-runtime-only` | builds `socket` at run time to evade static rules | sealed trial run only (network violation) |
| `benign-*` | word count, commit messages, an MCP notes server | expected Low, installs |

See `docs/skills-security.md` for the full gate.
