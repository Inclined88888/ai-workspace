# Third party notices

ROOKS bundles or installs the components below. Versions are the pinned ones in
`workbench/backend/requirements.txt` and `workbench/frontend/package.json`. Licences were read from each package's
installed metadata on 2026-10-27. Each package's own licence file, shipped inside the package, is the authority.

No source code from the projects named in `docs/RESEARCH.md` was copied into ROOKS. Ideas were adapted and the
code was written fresh.

## Backend (Python)

| Package | Version | Licence |
|---|---|---|
| fastapi | 0.115.12 | MIT |
| uvicorn | 0.34.2 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| cryptography | 44.0.3 | Apache-2.0 OR BSD-3-Clause |
| icalendar | 6.1.3 | BSD |
| recurring-ical-events | 3.7.0 | GPL-3.0-or-later (see the note below) |
| caldav | 1.4.0 | GPL and Apache classifiers (dual licence; see the note below) |
| trafilatura | 2.0.0 | Apache-2.0 |
| python-dateutil | 2.9.0.post0 | Apache-2.0 / BSD dual licence |
| mcp | 2.1.1 | MIT |
| croniter | 6.2.4 | MIT |
| tzdata | 2026.5 | Apache-2.0 |
| pypdf | 6.21.2 | BSD-3-Clause |

**Note on GPL packages.** `recurring-ical-events` reports GPL-3.0-or-later. `caldav` reports both GPL and Apache.
Running ROOKS for yourself raises no issue. If you redistribute a built ROOKS image or bundle, check what these
licences require of you, or replace them. This note is not legal advice.

## Frontend (JavaScript)

| Package | Version | Licence |
|---|---|---|
| react, react-dom | 18.3.1 | MIT |
| react-router-dom | 6.30.1 | MIT |
| marked | 15.0.12 | MIT |
| dompurify | 3.2.6 | MPL-2.0 OR Apache-2.0 |
| lucide-react | 0.511.0 | ISC |

## Fonts (bundled; nothing is loaded from font services)

Every font below is under the SIL Open Font License 1.1 (OFL-1.1), through @fontsource 5.3.0. Under the OFL these
fonts may be bundled and redistributed, but they may not be sold on their own.

- Inter
- IBM Plex Sans
- Manrope
- Nunito
- Literata
- JetBrains Mono
- Atkinson Hyperlegible
- OpenDyslexic

## ROOKS Companion (optional, installed on your computer only if you want Computer Use)

| Package | Version | Licence | Used for |
|---|---|---|---|
| playwright (Python) | 1.64.1 | Apache-2.0 | ROOKS's own browser |
| python-xlib | 0.33 | LGPL-2.1+ | Linux X11 desktop control (imported, not modified or bundled) |
| openpyxl | 3.1.5 | MIT | Optional: .xlsx editing in the companion (imported if installed, not bundled) |
| python-docx | 1.2.0 | MIT | Optional: .docx editing in the companion (imported if installed, not bundled) |

On macOS the companion uses only Python's standard library and tools that ship with macOS.

## Optional add-on containers (not part of the core image; pulled only when you turn them on)

| Add-on | Image | Licence |
|---|---|---|
| Search | searxng/searxng | AGPL-3.0 |
| Local models | ollama/ollama | MIT |
| Chat UI | ghcr.io/open-webui/open-webui | See the Open WebUI repository; its licence has changed between releases |
| Agent | nousresearch/hermes-agent | MIT |
| Company | ghcr.io/paperclipai/paperclip | See the Paperclip repository |

These add-ons run as separate, unmodified containers that ROOKS talks to over HTTP. Their licences apply to the
add-ons themselves, not to ROOKS.
