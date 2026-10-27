# One-command installer verification

27 October 2026. Work is confined to security/rooks-v9-1.

## What changed
- Installer accepts branch, tag or a full 40-character Git commit pin.
- Requires Git, Bash 3.2+ and an already running Docker engine with Compose v2.
- Targets macOS/Linux on x86_64/arm64 and Windows through WSL2; refuses native Windows shells.
- Does not install system packages, modify Docker groups or run sudo.
- Existing installs must match the expected origin, have a clean checkout and accept a fast-forward update.
- Refuses archive-directory overwrites, symlink destinations and option-like references.
- Uses a staging directory; failed downloads do not create a partial installation.
- Prints the installed revision before running setup.
- Setup uses a restrictive umask, ensures .env mode 600 and refuses symlink/non-regular .env files.
- Adds installer CI on Ubuntu and macOS and an npm high-severity audit gate.

## Verification
- 16 local unittest cases passed using real temporary Git repositories and fake Docker/curl commands.
- Shell syntax passed with the local macOS Bash.
- Cases cover exact pins, updates preserving secrets, local edits, wrong origins, divergent history, archive installs, symlinks, missing refs, platform gates, Docker failures, help, reference injection, interrupted bootstrap and .env permissions.
- Simulated Linux/WSL platform checks are not real Windows/WSL installation tests.
- No Docker services were started and no real model providers were called.
- Full installation on every OS/architecture remains UNVERIFIED.
- Commit pinning does not authenticate a publisher independently or pin all Docker images and transitive dependencies.

## Repository safety and copying
User explicitly chose to keep the repository public. Its visibility was not changed.
Public source remains viewable, downloadable and forkable; installation security cannot prevent copying.
No new license or claim of ownership over third-party components was added.

At inspection, main was unprotected and the repository ruleset list was empty. No repository policy settings were changed. Secret-scanning alerts, push protection, collaborator access and account security settings have not been verified with this connection. These are remaining repository-hardening work, not completed protections.

Previous open issues still apply: two moderate React Router dependency entries, unsigned release provenance, and uncapped/unknown-price spending limitations. This work is not a complete penetration test or a guarantee of security.

Sources:
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- https://docs.docker.com/engine/install/

Implementation and final verification: Codex. Workers/Hermes are unavailable; no private data was delegated.
