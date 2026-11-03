# Ask from anywhere, voice, and meeting notes (v0.2)

Branch `feat/v02-anywhere-voice`. Plain-English guide first, technical notes after.

## What you get

### Ask from anywhere (Free)
- Press **Option+Space** (Mac) or **Alt+Space** (Windows) in any app. A small ask bar floats over what you're doing.
- If you'd selected some text, it's already attached. If not, tap **Use what I copied** to attach the clipboard.
- With Rooks Desk on, tap **Screenshot** to attach a picture of the window you were in (you can make that automatic in Voice settings).
- Ask a question or tap a quick action (Explain, Summarise, Reply, Fix the writing, Translate…). The answer streams in.
- **Paste back** puts the answer where your cursor was (with Rooks Desk), or copies it so you can paste. **Do it in Chat** opens the full app with your request ready.
- Change the shortcut in **Settings → Ask from anywhere & voice**. Press Esc to close the bar.

### Voice
- **Dictation (Free):** the microphone button in Chat. Click to talk (it stops when you stop), or hold it down and let go.
- **Talk (Pro):** a hands-free conversation on the Talk page. Optional wake word ("hey workspace", changeable) when speech recognition runs on your computer. Start speaking while it's talking and it stops to listen (barge-in). Say "stop listening" to end.
- **Approvals out loud:** when the assistant wants to send, post, pay or delete, the usual approval card appears **and** it asks you out loud. Only a clear "yes, send it" approves; "no", "wait", "maybe", questions and anything muddled are not a yes. The computer checks your words again before anything runs. You can always tap the card instead.
- **Speech to text:** on your computer (faster-whisper, private, if installed) → a cloud service (if you've added a key) → your browser/OS dictation.
- **Reading out:** your computer's voices (free, offline) or an optional cloud voice.
- **Phone:** in a chat, tap **Talk** at the top. Say your message, hear the answer, and answer approvals out loud (card still shown). Needs a dev/store build with speech recognition; Talk is Pro, the Voice dictation button stays free.

### Meeting notes (Pro)
- **Meetings → Record**. You must tick "I've told everyone this meeting is being recorded" first. Optionally include the computer's sound (the other people on a call).
- Or **Import** a recording (audio/video) or a transcript (.txt, .vtt, .srt, .md).
- **Write the notes** gives a summary, decisions, and actions with owners and dates.
- Tick actions and **Add to Tasks** (with an optional project).
- **Follow-up email:** edit it, then **Save as draft**. It is **never sent** from here.
- Audio is transcribed in 30-second pieces and not kept; only the text is saved. Delete removes the meeting and its transcript.

## Tiers (`backend/app/features_v02.py`)
| Feature | Tier |
|---|---|
| `ask_anywhere` | free |
| `voice_dictation` | free |
| `voice_conversation` (Talk, wake word, spoken approvals) | pro |
| `meeting_companion` | pro |

`features_v02.allowed()` already calls `account.allowed(feature)` when PR #32's `app/account.py` exists; until then (or if the plan check errors) everything is allowed. Pro features answer HTTP 402 with a plain-English message when not allowed.

## Technical notes
- Backend: `app/anywhere.py` (`/api/anywhere/*`), `app/voice.py` (`/api/voice/*`, `/api/mobile/voice/*`), `app/meetings.py` (`/api/meetings/*`). Spoken approvals: `voice.spoken_decision()` re-parses the words server-side, then calls the normal `actions` approval path. Follow-up drafts go through `tools.save_draft` and return `sent: false`.
- Rooks Desk: `grab.py` + `/api/grab` (selected text via a copy keystroke with clipboard restore, active-window screenshot) and `/api/paste`.
- Desktop (Tauri): `src-tauri/src/anywhere.rs` — global shortcut (`tauri-plugin-global-shortcut`), the `askbar` window, `get/set_ask_shortcut`, `hide_ask_bar`, `show_main_window`. On press it asks the backend to grab the selection *before* the bar takes focus. Mac `Info.plist` adds microphone / speech usage text; entitlements add audio input.
- Frontend: `src/voice/*` (logic, engine, DictateButton, CSS), pages `AskBar`, `Talk`, `VoiceSettings`, `Meetings`.
- Mobile: `src/lib/voiceMode.ts` (expo-speech), `Client.voiceDecide/voiceStatus`, Talk toggle in `app/chat/[id].tsx`.

## Not yet verified
No real Mac/Windows run, microphone, faster-whisper model or system-audio capture was available while building this. The global shortcut, the copy-selection grab, paste-back, speech recognition in the Mac webview and `getDisplayMedia` audio need checking on clean installs before this is called done.
