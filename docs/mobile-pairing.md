# Phone pairing, transport and push

The iPhone and Android companion app (`mobile/`, see `mobile/README.md`) is a remote for the Workbench that runs on your
computer (the desktop app) or your server (Docker). The heavy work (chat turns, Rooks Code, Rooks Browser, schedules)
stays there. The phone pairs once, then chats, approves held actions, follows runs and glances at tasks and notes.

Backend code: `workbench/backend/app/mobile.py` (pairing, device routes, LAN listener) and `app/push.py` (notifications).
Tests: `workbench/backend/tests/test_mobile.py`.

## Pairing

1. Signed in on the desktop or web app, open **Settings → Phone → Pair a phone**. Workbench shows a QR code and an
   8-character code (for example `N6CV-ATBH`). The code works once and expires after 5 minutes. Only its SHA-256 is
   stored.
2. The phone scans the QR code, or you type the address and code. The QR code holds
   `aiworkspace://pair?c=<CODE>&u=<address>&u=<address>&n=<workspace name>`; addresses are in the order to try.
3. The phone calls `POST /api/mobile/pair/claim {code, name, platform}` and receives a device token (`wbd_…`). The app
   keeps it in the phone's secure storage (iOS Keychain, Android Keystore through `expo-secure-store`). Workbench keeps
   only its SHA-256.
4. Settings lists paired phones with when they were last seen. **Unpair** revokes a phone at once; the app can also
   unpair itself (`DELETE /api/mobile/me`).

Ten wrong codes from one address in five minutes are refused with 429 for the rest of the window.

## What a device token can do

A device token is not a session. It reaches only `/api/mobile/*`:

| Route | Use |
| --- | --- |
| `GET /api/mobile/ping` (no token) | Which address answers; workspace name |
| `POST /api/mobile/pair/claim` (no token) | Exchange a pairing code for a device token |
| `GET /api/mobile/me`, `DELETE /api/mobile/me` | This phone; unpair itself |
| `GET /api/mobile/home` | Glance: pending approvals, running count, last runs, open tasks, notes |
| `GET /api/mobile/approvals`, `POST /api/mobile/approvals/{id} {approve}` | Approvals inbox; one-tap approve or deny (decided once, 409 if the desk answered first) |
| `GET /api/mobile/runs`, `GET /api/mobile/runs/{id}` | Run log and one run's trace (receipts, tool calls, cost) |
| `GET /api/mobile/tasks`, `PATCH /api/mobile/tasks/{id} {status}` | Tasks; tick one off |
| `GET /api/mobile/notes` | Notes, pinned first |
| `GET /api/mobile/conversations`, `GET /api/mobile/conversations/{id}` | Chats from every channel |
| `GET /api/mobile/models` | Model picker (cached list) |
| `POST /api/mobile/attachments {kind, mime, data}` | Photo or voice note, base64, 10 MB max, saved in `DATA_DIR/mobile_uploads` |
| `POST /api/mobile/chat {message, conversation_id?, model?, attachments?}` | One turn, streamed as server-sent events, channel `mobile` |
| `PUT /api/mobile/push-token {token}` | Register the phone's Expo push token |

It cannot read or change Settings, model keys, mail accounts, the password, other phones, or start a pairing. Writes
need `X-Workbench: 1` like every other client. Decisions made on the phone are recorded in the run log with channel
`mobile`.

Owner routes (signed-in session only): `POST /api/mobile/pair/start`, `GET /api/mobile/devices`,
`DELETE /api/mobile/devices/{id}`, `GET|PUT /api/mobile/settings {lan_enabled, relay_url, push_enabled}`.

Attachments are stored and referred to in the message. Workbench does not transcribe voice notes or read photos yet;
the assistant is told so and asks what they show.

## On your Wi-Fi (works now)

**Desktop app.** The desktop app's server listens on 127.0.0.1 only. Turn on **Settings → Phone → Phone access on this
Wi-Fi** and Workbench starts a second listener on `0.0.0.0:8766` (`WORKBENCH_MOBILE_PORT`) that answers only
`/api/mobile/*` and `/healthz`. Everything else (the web app, `/api/*`, `/mcp`, `/v1`, `/tools`) is 404 there. The switch
is remembered and the listener comes back when the app starts. The pairing QR code lists this machine's private
addresses, for example `http://192.168.1.20:8766`.

- macOS asks once whether "AI Workspace" may accept incoming connections: allow it.
- Windows Defender Firewall asks the same on first use: allow it on **private** networks only.
- Plain HTTP on your own network: the device token travels unencrypted on the Wi-Fi. Use it at home, not on public Wi-Fi.
  Away from home, use a relay with HTTPS (below).

**Docker install.** Publish Workbench on your network with `BIND_ADDRESS=0.0.0.0` in `.env` (it is `127.0.0.1` by
default), then list the address the phone should use, for example `MOBILE_URLS=http://192.168.1.20:3100`. Opening
Settings on that address also offers it. On Docker the whole web app is reachable on that port (it always was with
`BIND_ADDRESS=0.0.0.0`); it still needs the password.

## Away from home (relay interface)

The phone needs one more base URL that reaches the same Workbench from anywhere. Save it in **Settings → Phone → Away
from home** (or `MOBILE_RELAY_URL`). It goes last in the QR code's address list: the phone tries the Wi-Fi address
first, then the relay. The device token is the same on every address.

Anything that gives that URL works. A relay must:

1. forward HTTPS to the Workbench listener (desktop: `127.0.0.1:8766`; Docker: the Workbench port),
2. pass `Authorization` and `X-Workbench` headers unchanged,
3. stream responses without buffering (chat is server-sent events),
4. ideally forward only `/api/mobile/*` and `/healthz`. The desktop LAN listener already enforces this.

**Tailscale (recommended, free for personal use).** Install Tailscale on the computer and the phone and sign in to both
with the same account. On the computer run:

```
tailscale serve --bg --https=443 http://127.0.0.1:8766     # desktop app (turn on Wi-Fi access first)
tailscale serve --bg --https=443 http://127.0.0.1:3100     # Docker install
```

Then save `https://<computer-name>.<your-tailnet>.ts.net` as the relay address. Traffic is end-to-end encrypted and only
your own devices can reach it. Do not use `tailscale funnel` (that is public).

**Cloudflare Tunnel.** `cloudflared tunnel --url http://127.0.0.1:8766` (or a named tunnel with Cloudflare Access in
front). This makes the phone routes reachable from the internet, protected by the device token; add Access if you
can.

**A small relay of our own** (not built). The interface above is all it needs: a hosted service the desktop app keeps
an outbound WebSocket to, forwarding the phone's HTTPS requests for `/api/mobile/*` over it. It would remove the
Tailscale step for non-technical users. It is not implemented; `relay_url` is the hook it would fill in.

## Push notifications (inactive until configured)

Workbench sends two notifications through Expo's push service:

- **Approval needed**: an action was held for approval (not for unattended runs, which refuse instead).
- **Run finished / Run needs a look**: a turn finished that started on the phone, from a schedule or from a company
  agent. Turns started at the desk do not notify.

A notification carries a title, one short line and ids (`kind`, `id`, `conversation_id`). No message content, no
arguments, no secrets. Every send goes through the egress audit log (it leaves your network, to `exp.host`).

Nothing is sent until all of these are true:

1. **Settings → Phone → Push notifications** is on (`mobile_push_enabled`).
2. The phone app was built with push credentials and the user allowed notifications. The app then registers its Expo
   push token (`PUT /api/mobile/push-token`). Settings shows "notifications on" for that phone.
3. Credentials, once, in the Expo project (`eas credentials`):
   - an Expo account and an EAS project id in `mobile/app.json` (`extra.eas.projectId`),
   - **iOS:** an Apple Push Notifications key (`.p8`) from the Apple Developer account (US$99/year); EAS can create it,
   - **Android:** a Firebase project and its FCM V1 service-account key uploaded to EAS (free).
4. Optional: if "enhanced push security" is on in the Expo project, set `EXPO_ACCESS_TOKEN` where Workbench runs.

Expo Go cannot receive remote push notifications (since SDK 53, on both platforms). Use a development build
(`eas build --profile development`), TestFlight or a Play internal-testing build.
