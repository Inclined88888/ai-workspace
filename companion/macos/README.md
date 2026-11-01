# Rooks Companion (macOS)

A macOS notch companion app that displays live AI agent activity, approvals, and questions from the Rooks companion backend.

## Features

- **Notch-hugging panel**: Borderless NSPanel positioned at top-center, automatically detects screen notch and safe areas
- **Collapsed pill**: Shows mascot (idle/working/waiting/done) with one-line status
- **Expanded view**: Click or hover to expand - shows usage, agents with recent steps, and pending requests
- **Request handling**: Permission requests (Allow/Deny/Always), question requests (option buttons + text input)
- **File drop**: Drop files onto the pill/panel to send to `/api/companion/drop` and auto-open returned URL
- **Menu bar integration**: Show/Hide, Open Rooks, Launch at Login, Quit
- **Auto-reconnect**: Exponential backoff reconnection to companion backend
- **Token refresh**: Automatically re-reads `~/.rooks/companion.json` on 401/unauthorized

## Architecture

```
Sources/
├── CompanionCore/          # Shared library (networking, models, SSE parser, state reducer)
│   ├── Models.swift        # Codable models matching backend API
│   ├── Networking.swift    # HTTP client, SSE client, StateStore actor
│   └── Utilities.swift     # Mood→mascot mapping, formatting helpers
└── RooksCompanion/         # Executable target
    ├── App.swift           # AppDelegate, NSPanel setup, status item menu
    ├── PanelViewModel.swift # ObservableObject bridging StateStore to SwiftUI
    └── PanelView.swift     # SwiftUI views (collapsed pill, expanded panel)
```

## Requirements

- macOS 13.0+
- Swift 5.9+
- Rooks companion backend running (writes `~/.rooks/companion.json`)

## Building

```bash
# From repo root
./scripts/build-app.sh [--release]
```

This creates `RooksCompanion.app` in `.build/<config>/`.

Or build with SwiftPM directly:
```bash
cd companion/macos
swift build -c release
```

## Running

The app runs as an accessory (LSUIElement=1) - no dock icon, just the notch panel and menu bar item.

```bash
# Run directly
./.build/release/RooksCompanion

# Or open the app bundle
open .build/release/RooksCompanion.app
```

## Configuration

The app discovers the companion backend via `~/.rooks/companion.json` (or `ROOKS_COMPANION_FILE` env var):

```json
{
  "url": "http://127.0.0.1:9999",
  "token": "secret-token",
  "pid": 12345
}
```

A new token is generated on each server start. The app automatically re-reads the file on 401 errors.

## API Endpoints Used

- `GET /api/companion/state` - Full state snapshot
- `GET /api/companion/stream` - Server-sent events (state changes + heartbeat)
- `POST /api/companion/requests/{id}/decide` - Answer permission/question requests
- `POST /api/companion/drop` - File drop endpoint
- `DELETE /api/companion/rules/{index}` - Remove "Always" rules

Headers sent with every request:
- `Authorization: Bearer <token>`
- `X-Workbench: 1`

## Testing

```bash
cd companion/macos
swift test
```

Tests cover:
- Model decoding/encoding (CompanionState, Agent, Request, etc.)
- SSE parsing (multi-line data, pings, comments, chunked streaming)
- Config discovery and caching
- Request/decide/drop payload building
- Mood→mascot mapping and formatting utilities

## Menu Bar Items

| Item | Action |
|------|--------|
| Show Companion | Toggle panel visibility |
| Open Rooks | Open `rooks://` URL |
| Launch at Login | Toggle SMAppService registration |
| Quit | Terminate app |

## Keyboard Shortcuts (Expanded Panel)

- `Esc` - Collapse panel
- `Return` - Allow top permission request
- Type + `Return` - Submit answer for question requests

## Mascot States

| Mood | Symbol | Animation | Color |
|------|--------|-----------|-------|
| Idle | `circle.fill` | None | Secondary |
| Working | `gearshape.2.fill` | Rotate | Blue |
| Waiting | `exclamationmark.circle.fill` | Bounce | Orange |
| Done | `checkmark.circle.fill` | Pulse | Green |

## Agent Status Indicators

- **Idle**: Gray circle
- **Working**: Blue pulsing circle
- **Thinking**: Purple brain icon
- **Waiting**: Orange clock
- **Done**: Green checkmark
- **Error**: Red triangle

## License

Part of the Rooks project. See root LICENSE.