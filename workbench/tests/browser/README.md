# Browser tests

Real journeys in Chromium against a real ROOKS server started on temporary data. The AI is a stand-in
(`fake_ai.py`): an OpenAI compatible server with scripted replies, and an OpenRouter shaped endpoint with
real-format prices. **Passing proves the interface and the server work together. It proves nothing about a real AI
provider.**

| File | What it checks |
|---|---|
| `test_journey.py` | A new person: set up, connect free AI, find every area, ask for a reminder and see the task, finish it, write a note, plan a mission, change settings, Free Cloud AI holds |
| `test_keyboard.py` | Keyboard only: skip link, focus ring, sidebar, adding a task, search palette (focus returns on Escape), opening notes and conversations, Shift+Enter and Enter, the phone menu |
| `test_regressions.py` | One check per bug fixed in v12, each saying what used to go wrong |
| `test_v13_scenarios.py` | Scenarios A to J of v13 through the real app: research, spreadsheets, documents, missions, Codex and Claude Code through the real companion (set `ROOKS_CLI_BIN`, else SKIPPED), local engines and AirLLM (`ROOKS_AIRLLM_URL`), voice and check-ins, unavailable models. Each check is labelled with what stood in (stand-in AI, official CLI, engine stand-in) |
| `resources.py` | Server memory, idle CPU and API latency of a checkout: `python3 resources.py <checkout>` |
| `screens.py` | Every page at desktop, tablet and phone, light and dark: screenshot, no sideways scrolling, the page never scrolls past the window, no page errors, accessibility (with `AXE_JS`) |
| `perf.py` | Measures load, page changes, a long conversation, 1,000 tasks, charts and idle requests; prints JSON |

Run everything: `./run.sh`. Run one: `python3 test_journey.py`.

Environment: `ROOKS_PY` (Python with the backend requirements, default `python3`), `ROOKS_CHROMIUM` (a Chromium
binary, default Playwright's), `AXE_JS` (path to axe-core's `axe.min.js`; without it accessibility checks are
skipped and say so), `ROOKS_SHOTS` (where screenshots go).

axe-core is not a project dependency. To use it: `npm install --prefix /tmp/axe axe-core@4.10.2` and set
`AXE_JS=/tmp/axe/node_modules/axe-core/axe.min.js`.
