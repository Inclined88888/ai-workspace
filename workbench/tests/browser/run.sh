#!/usr/bin/env bash
# Runs every browser test against a fresh ROOKS on temporary data, with the stand-in AI (fake_ai.py).
# Needs the backend's Python requirements plus Playwright (pip install playwright && python3 -m playwright install chromium).
# Optional: AXE_JS=/path/to/axe.min.js for accessibility checks, ROOKS_SHOTS=folder for screenshots,
# ROOKS_CLI_BIN=folder with codex and claude (scenarios F and G), ROOKS_AIRLLM_URL=a running AirLLM bridge (scenario H).
set -u
cd "$(dirname "$0")"
PY="${ROOKS_PY:-python3}"
(cd ../../frontend && npm run build > /dev/null) || { echo "The frontend did not build"; exit 1; }
fail=0
for t in test_journey.py test_keyboard.py test_regressions.py test_v13_scenarios.py test_v14_shell.py test_v15_web.py screens.py; do
  echo "== $t"
  "$PY" "$t" || fail=1
done
echo "== perf.py (measures only, never fails)"
"$PY" perf.py || true
exit $fail
