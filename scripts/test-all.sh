#!/usr/bin/env bash
# Run every test suite in the repo. Provider keys are hidden from the tests, so nothing calls a real model.
#
#   scripts/test-all.sh            backend, Rooks Code, frontend, scorecard selftest, compose files
#   scripts/test-all.sh --quick    skip the frontend build and the backend suite's slow sandbox tests
#   scripts/test-all.sh --live     also run the 11 hard benchmark tasks against a real model (NVIDIA_API_KEY)
#
# Needs: uv (https://docs.astral.sh/uv/) for the Python 3.12 venv, npm for the frontend, docker for the compose check.
# The venv lives in ~/.venvs/aiw (override with AIW_VENV); it is created on first run.
set -uo pipefail
cd "$(dirname "$0")/.."

QUICK=false; LIVE=false
for a in "$@"; do
  case "$a" in
    --quick) QUICK=true ;;
    --live) LIVE=true ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done

VENV="${AIW_VENV:-$HOME/.venvs/aiw}"
PY="$VENV/bin/python"
FAILED=()
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok() { printf '\033[32mPASS\033[0m %s\n' "$*"; }
bad() { printf '\033[31mFAIL\033[0m %s\n' "$*"; FAILED+=("$1"); }

# Every *_API_KEY / *_TOKEN in this shell is hidden from the tests (a real key would make them call real providers)
NOKEYS=(env)
while IFS='=' read -r name _; do
  [[ "$name" == *_API_KEY || "$name" == *_TOKEN ]] && NOKEYS+=(-u "$name")
done < <(env)

step "Python venv ($VENV)"
if [[ ! -x "$PY" ]]; then
  command -v uv >/dev/null || { echo "uv is needed: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
  uv venv -q -p 3.12 "$VENV" || exit 1
fi
VIRTUAL_ENV="$VENV" uv pip install -q -r workbench/backend/requirements.txt -r workbench/backend/requirements-dev.txt \
  -r workbench/backend/requirements-files.txt "prompt_toolkit>=3.0.43" "rich>=13.7" && ok "dependencies" || bad "dependencies"
# prompt_toolkit and rich are Rooks Code's interactive terminal (rooks-code/pyproject.toml): without them its tests skip

step "Backend (workbench/backend)"
EXTRA=(); $QUICK && EXTRA=(-k "not sandbox and not run_python")
(cd workbench/backend && "${NOKEYS[@]}" "$PY" -m pytest -q -p no:cacheprovider -W ignore --ignore=bench ${EXTRA[@]+"${EXTRA[@]}"}) \
  && ok "backend" || bad "backend"

step "Scorecard selftest (every benchmark check judged against gold answers, no model)"
(cd workbench/backend && "${NOKEYS[@]}" APP_SECRET="$(printf 'a%.0s' {1..40})" TOOLS_API_KEY="$(printf 'k%.0s' {1..30})" \
  DATA_DIR="$(mktemp -d)" "$PY" bench/scorecard.py --selftest 2>&1 | grep -E "selftest|PROBLEM") | tee /dev/stderr | grep -q " 0 problems" \
  && ok "scorecard selftest" || bad "scorecard selftest"

step "Rooks Code (rooks-code)"
(cd rooks-code && "${NOKEYS[@]}" "$PY" -m pytest -q -p no:cacheprovider -W ignore tests) && ok "rooks" || bad "rooks"

if ! $QUICK; then
  step "Frontend (workbench/frontend: typecheck, build, tests)"
  (cd workbench/frontend && { [[ -d node_modules ]] || npm ci --silent; } && npm run build --silent >/dev/null && npm test 2>&1 | tail -3) \
    && ok "frontend" || bad "frontend"
fi

step "Compose files (render without a .env, with and without the prebuilt image)"
if command -v docker >/dev/null; then
  tmp=$(mktemp -d); [[ -f .env ]] && mv .env "$tmp/.env"
  docker compose config -q 2>/dev/null && ok "docker-compose.yml without .env" || bad "docker-compose.yml without .env"
  WORKBENCH_IMAGE=ghcr.io/inclined88888/ai-workspace-workbench:edge COMPOSE_FILE=docker-compose.yml:docker-compose.prebuilt.yml \
    docker compose config -q 2>/dev/null && ok "prebuilt override" || bad "prebuilt override"
  [[ -f "$tmp/.env" ]] && mv "$tmp/.env" .env
  bash -n setup.sh && ok "setup.sh syntax" || bad "setup.sh syntax"
else
  echo "docker not installed: skipped"
fi

if $LIVE; then
  step "Live: 11 hard benchmark tasks on NVIDIA Nemotron Ultra (free tier, a few minutes)"
  if [[ -z "${NVIDIA_API_KEY:-}" ]]; then
    bad "live bench (NVIDIA_API_KEY not set)"
  else
    (cd workbench/backend && NV_BENCH_KEY="$NVIDIA_API_KEY" env -u NVIDIA_API_KEY "$PY" bench/scorecard.py --provider nvidia \
      --key-env NV_BENCH_KEY --model nvidia/nemotron-3-ultra-550b-a55b --only hard --pace 3 --retries 5 \
      --out "bench/results/hard-live-$(date +%Y%m%d-%H%M).jsonl" 2>&1 | grep -E "^(PASS|FAIL)|^\| ") && ok "live bench ran" || bad "live bench"
  fi
fi

echo
if ((${#FAILED[@]})); then
  printf '\033[31m%d failed:\033[0m %s\n' "${#FAILED[@]}" "${FAILED[*]}"; exit 1
fi
printf '\033[32mAll passed.\033[0m\n'
