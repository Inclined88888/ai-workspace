# Coding tooling

Optional developer tools. None of them run in CI yet; existing checks (`./scripts/test-all.sh`) are unchanged.

## Biome 2.6.4 (lint, frontend) MIT OR Apache 2.0

Pinned dev dependency in `workbench/frontend`. Config: `workbench/frontend/biome.json` (linter only, formatter off,
scope `src/**`, honours `.gitignore`).

```sh
cd workbench/frontend
npm run lint:biome -- src/account.ts   # one file
npm run lint:biome -- src              # whole app (about 800 existing findings, mostly useButtonType)
```

Fix findings file by file. Wire it into CI only once `src` is clean.

## rumdl 0.3.2 (Markdown lint) MIT

Not a dependency; run on demand through uv:

```sh
uvx rumdl@0.3.2 check AGENTS.md
```

## Playwright (browser tests) Apache 2.0

Already used by `rooks-browser` and the CI browser jobs (`python -m playwright install chromium`). Nothing added.

## AI coding agents

OpenCode (MIT) and Aider (Apache 2.0) are free software but need a model provider key or plan to do anything.
Install them per user, not per project; Rooks itself (`rooks`) is this repo's own terminal agent.
