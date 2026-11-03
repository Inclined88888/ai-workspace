# Security Policy

AI Workspace (Workbench, Rooks Code, Rooks Browser, Rooks Desk, the desktop app, the mobile companion and the VS Code
extension) is local first software for one trusted owner. It is not a multi tenant boundary between users who
distrust each other. The full trust model is in [docs/security/THREAT-MODEL.md](docs/security/THREAT-MODEL.md).

## Report a vulnerability

Report privately through a GitHub Security Advisory:
https://github.com/Inclined88888/ai-workspace/security/advisories/new

Do not open a public issue or pull request that describes an unpatched vulnerability, an exploit path, a secret or a
working proof of concept. Public reports are hidden and moved to the private process.

There is no paid bug bounty.

## Supported versions

| Version | Supported |
| --- | --- |
| Latest tagged release (`vX.Y.Z`) | Yes |
| `integration/v0.3` (pre release branch) | Best effort |
| Anything older | No |

Fixes ship in a new patch release. The desktop app picks it up through its signed updater; the Docker install and the
Python tools are updated by the owner.

## What a useful report contains

* The component, version or commit SHA, and the exact file and function.
* Reproduction steps against the latest release or `integration/v0.3`.
* The trust boundary that is crossed (see the threat model) and the real impact.
* A suggested fix if you have one.

Reports without a reproduction and a crossed boundary are deprioritised.

## Usually not a vulnerability on its own

* Prompt injection that does not get past an approval, a sandbox, the egress rules or authentication.
* The owner deliberately using a powerful local feature (shell tools, browser automation, desktop control).
* A malicious skill or MCP server that the owner installed after the security gate showed it as High risk and they
  typed the override.
* Exposing Workbench to the public internet against the defaults (it binds to `127.0.0.1`) without TLS and a strong
  password.
* Changing the environment of the process before it starts.
* Scanner or dependency CVE output without a working path through this code.

## Response

We aim to acknowledge a report within 7 days and to agree a disclosure date with the reporter. This is a small project
and these are targets, not guarantees. Credit is given in the advisory unless you ask otherwise.
