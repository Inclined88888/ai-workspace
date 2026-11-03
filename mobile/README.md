# AI Workspace for iPhone and Android

The phone is the remote; your computer does the work. This app pairs with the Workbench on your computer (the desktop
app, or a Docker install) and lets you chat with it, approve or deny what it wants to do, follow its runs and receipts,
and glance at today's tasks and notes. Models, keys, files, browsing and code all stay on the computer.

Expo SDK 58 · React Native 0.88 · TypeScript · Expo Router. Same editorial look as the desktop app: warm paper and ink,
Bodoni Moda headlines, Hanken Grotesk text, one cherry-red accent, hairline rules, square corners. Light first; Ink
(dark) and System are in Settings.

| Screen | What it does |
| --- | --- |
| **Today** | Date masthead, what needs you, an "Ask your computer…" box, approvals waiting, jobs on the desk, open tasks (tap to move one along), notes |
| **Chats** | Every conversation from the desktop, terminal, Telegram and this phone |
| **Chat** | Streaming-style reveal, Markdown with code blocks (copy), tool receipts, inline approvals, photo/file attach, on-device dictation |
| **Approvals** | One-tap Approve / Deny with the exact arguments; badge on the tab |
| **Runs** | Live status, cost, "Needs a look" filter; each run opens as a receipt with its trace |
| **Settings** | Paired computer and address, model, Paper / Ink / System, haptics, notifications, privacy, unpair |
| **Pair** | Scan the QR code from the computer, or type its address and code |

## Run it

```bash
cd mobile
npm install
npx expo start            # scan the QR with Expo Go (Android) or the Camera app (iPhone)
```

Then on the computer: **Settings → Phone → Phone access on this Wi-Fi** (desktop app), **Pair a phone**, and scan the
code from the app's Pair screen. Phone and computer must be on the same Wi-Fi.

**Expo Go or a development build?** Expo Go is enough for chat, approvals, runs, pairing and photos. Two things need a
development build because they use native code Expo Go does not ship:

- **Dictation** (`expo-speech-recognition`, on-device speech to text). In Expo Go the Voice button explains this
  instead.
- **Push notifications** (Expo Go cannot receive remote push since SDK 53).

```bash
npm install -g eas-cli
eas login
eas init                                          # writes extra.eas.projectId into app.json
eas build --profile development --platform ios    # or android; install it, then `npx expo start --dev-client`
```

Checks: `npm test` (jest, pure logic), `npm run typecheck`, `npm run lint`, `npm run doctor`, `npm run export:web`.

## Pairing and the connection

1. The computer shows a QR code holding `aiworkspace://pair?c=<code>&u=<address>…&n=<name>`. The code is single-use
   and expires after five minutes.
2. The app tries each address in order (`GET /api/mobile/ping`), then claims the code
   (`POST /api/mobile/pair/claim`) and receives a **device token** (`wbd_…`).
3. Every later request sends `Authorization: Bearer wbd_…` to `/api/mobile/*` only. When the app comes back to the
   foreground it re-checks which address answers (Wi-Fi first, then the away-from-home address).

**Away from home.** The app only needs one more address that reaches the same computer. The recommended route is
Tailscale (end-to-end encrypted, only your own devices); Cloudflare Tunnel also works. The interface a relay must
meet, and the plan for a hosted relay of our own (not built yet), are in
[`docs/mobile-pairing.md`](../docs/mobile-pairing.md#away-from-home-relay-interface). In the app the transport is just
the ordered address list in the pairing (`src/lib/storage.ts`, `firstReachable` in `src/lib/api.ts`): a relay adds an
address, nothing else changes.

## Security

- The device token lives in **SecureStore** (iOS Keychain / Android Keystore, this device only, after first unlock).
  The computer keeps only its SHA-256. The web build used for previews keeps it in `sessionStorage` for the tab only.
- Devices are **revocable**: Settings → Phone on the computer lists phones and unpairs them; the app can also unpair
  itself (Settings → Unpair this phone).
- The token reaches only the phone routes. It cannot read Settings, model keys, mail accounts, the password or other
  phones, and cannot start a pairing.
- **Every phone action is a receipt.** Chats run with channel `mobile`; approvals decided on the phone are recorded
  with channel `mobile`; both show up in Runs on the computer and the phone.
- Writes carry `X-Workbench: 1`; chat sends an `Idempotency-Key` so a retried send never runs twice.
- No secrets in the repo: no API keys, no push credentials. `eas.json` and `app.json` contain none; `.gitignore`
  excludes keystores, `.p8`, `.p12`, `.jks` and `.env*.local`.
- Notifications carry ids and a short line only, never message content or arguments.

## Push notifications

Two kinds: **Approval needed** and **Run finished / needs a look**. Tapping one opens the approval or the run.
Nothing is sent until the computer has **Settings → Phone → Push notifications** on and the phone allowed
notifications. For a real build you also need, once, in the Expo project (`eas credentials`):

- **iOS:** an Apple Push Notifications key (`.p8`). EAS can create it from your Apple Developer account.
- **Android:** a Firebase project and its **FCM V1** service-account key, uploaded to EAS.
- The EAS project id in `app.json` → `extra.eas.projectId` (`eas init`).

Full details and what the computer sends: [`docs/mobile-pairing.md`](../docs/mobile-pairing.md#push-notifications-inactive-until-configured).

## Testing with friends, then the stores

Before shipping, change the placeholder identifiers in `app.json` (`ios.bundleIdentifier` and `android.package`,
both `com.example.aiworkspace`) to ones you own, and replace the icon and splash images in `assets/`.

| | iPhone | Android |
| --- | --- | --- |
| Account | Apple Developer Program, **US$99 a year** | Google Play Console, **US$25 once** |
| Internal testing | **TestFlight**: `eas build -p ios --profile production` then `eas submit -p ios`. Add testers in App Store Connect; internal testers need no review. | **Internal testing track**: `eas build -p android --profile production` then `eas submit -p android` (the submit profile targets the `internal` track). Or share the APK from `eas build -p android --profile preview` directly. |
| Store release | Promote the TestFlight build in App Store Connect, fill in the listing and privacy answers, submit for review. | Promote internal → closed/open testing → production in Play Console; new personal accounts must run a closed test before production. |

`eas.json` profiles: `development` (dev client, internal distribution), `preview` (Android APK for sideloading),
`production` (store builds, build numbers auto-increment). Builds run on EAS servers; the free tier has a monthly
quota, and `eas build --local` builds on your own Mac (iOS) or machine (Android).

The privacy answers are short: the app talks only to the computer you paired, collects no analytics, and sends a push
token to Expo only if you allow notifications.

## Known limits

- A chat started on the phone streams over one HTTP request. If the app is backgrounded or the connection drops, the
  computer keeps working and the answer appears in the chat (and in Runs) when you come back; the live reveal is lost.
- The computer answers a turn in one piece; the word-by-word reveal is the phone pacing it (at most ~2.2 s).
- Photos and voice notes are saved on the computer and mentioned in the message, but the assistant does not read
  photos or transcribe voice notes yet. Dictation (speech to text on the phone) does work in a development build.
- Scanning needs the camera, so on the web preview pairing is by typed address and code.
- The hosted relay is designed but not built; use Tailscale or Cloudflare Tunnel for away-from-home access.

## Layout

```
src/app/          screens (Expo Router): (tabs)/ index chats approvals runs settings · chat/[id] · run/[id] · pair · scan
src/components/   ui.tsx (type, rules, buttons, tags, motion) · Markdown · ApprovalCard · RunRow · Connection
src/lib/          api (client + SSE chat) · session · storage (SecureStore) · pairing · markdown · reveal · turn
                  format · hooks · push · voice · attach · haptics · theme (design tokens)
src/lib/__tests__ jest tests for the pure logic
```
