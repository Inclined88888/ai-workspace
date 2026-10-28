#!/usr/bin/env bash
# One command: upgrade (or install) the ROOKS server, install the `rooks` terminal command, put it on your PATH, and
# connect it to your ROOKS. On Linux it also builds the desktop app if Node.js 18+ is installed.
#
#   curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/development/rooks-v15/scripts/rooks-all.sh | bash
#
# It asks for your ROOKS password once (to make the terminal token); the password is not stored anywhere.
# Making a terminal token replaces the previous one: an older `cli/ws` on another computer must reconnect.
# Never uses sudo. Keeps your data, settings and add-ons.
set -euo pipefail

REF="${AI_WORKSPACE_REF:-development/rooks-v15}"
DIR="${AI_WORKSPACE_DIR:-$HOME/ai-workspace}"
CLI_HOME="${ROOKS_CLI_HOME:-$HOME/.rooks-cli}"
BIN="$HOME/.local/bin"
say() { printf '\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || fail "Install curl first."
command -v python3 >/dev/null || fail "Install Python 3.10 or later first."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail "The rooks command needs Python 3.10 or later; this is $(python3 -V). On a Mac: brew install python"

# 1. The server: install.sh fetches $REF (fast-forward only, refuses local edits) and runs setup.sh unattended
if [[ -z "${ROOKS_SKIP_SERVER:-}" ]]; then
  say "1/4  Upgrading the ROOKS server in $DIR to $REF"
  curl -fsSL "https://raw.githubusercontent.com/Inclined88888/ai-workspace/$REF/install.sh" | AI_WORKSPACE_REF="$REF" AI_WORKSPACE_DIR="$DIR" bash -s -- --yes
fi
port=$(grep -E '^WORKBENCH_PORT=' "$DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || true)
URL="${ROOKS_URL:-http://localhost:${port:-3100}}"
say "     Waiting for ROOKS at $URL"
for _ in $(seq 1 90); do curl -fsS "$URL/healthz" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS "$URL/healthz" >/dev/null 2>&1 || fail "ROOKS did not answer at $URL. Check: cd $DIR && docker compose ps"

# 2. The terminal command, in its own environment (works where pip refuses system installs), linked on your PATH
say "2/4  Installing the rooks command"
python3 -m venv "$CLI_HOME"
"$CLI_HOME/bin/pip" install -q --upgrade "$DIR/cli"
mkdir -p "$BIN"
ln -sf "$CLI_HOME/bin/rooks" "$BIN/rooks"
new_path=""
if [[ ":$PATH:" != *":$BIN:"* ]]; then  # put ~/.local/bin on the PATH of new terminals, once
  case "${SHELL:-}" in *zsh) rc="$HOME/.zshrc" ;; *) rc="$HOME/.bashrc" ;; esac
  grep -q '# rooks: ~/.local/bin' "$rc" 2>/dev/null || printf '\nexport PATH="$HOME/.local/bin:$PATH"  # rooks: ~/.local/bin\n' >> "$rc"
  export PATH="$BIN:$PATH"
  new_path="$rc"
fi
"$BIN/rooks" --version

# 3. Connect: sign in once to make a terminal token, hand it to rooks on standard input
say "3/4  Connecting the rooks command to $URL"
jar=$(mktemp); trap 'rm -f "$jar"' EXIT
if [[ -n "${ROOKS_PASSWORD:-}" ]]; then pw="$ROOKS_PASSWORD"
else printf 'Your ROOKS password (not shown, not stored): ' >/dev/tty; IFS= read -rs pw </dev/tty; echo >/dev/tty; fi
body=$(python3 -c 'import json,sys; print(json.dumps({"password": sys.argv[1]}))' "$pw")
code=$(curl -s -o /dev/null -w '%{http_code}' -c "$jar" -H 'X-Workbench: 1' -H 'Content-Type: application/json' -d "$body" "$URL/api/login")
unset pw body
if [[ "$code" != "200" ]]; then
  fail "Sign-in failed ($code). If this is a new install, open $URL first to choose a password, then run this again."
fi
token=$(curl -fsS -b "$jar" -X POST -H 'X-Workbench: 1' "$URL/api/cli-token" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
printf '%s\n' "$token" | "$BIN/rooks" connect "$URL" --token-stdin
unset token
"$BIN/rooks" doctor || true

# 4. Desktop app (Linux, if Node.js 18+ is installed)
say "4/4  Desktop app"
if [[ "$(uname -s)" == "Linux" ]] && command -v npm >/dev/null && node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)' 2>/dev/null; then
  log="$DIR/desktop/build.log"
  echo "     Building (a few minutes; log: $log)"
  if (cd "$DIR/desktop" && npm ci --no-audit --no-fund && npx electron-builder --linux AppImage --x64 --publish never) >"$log" 2>&1; then
    app=$(ls "$DIR"/desktop/dist/ROOKS-*.AppImage | head -1)
    install -m 755 "$app" "$BIN/rooks-desktop"
    mkdir -p "$HOME/.local/share/applications"
    printf '[Desktop Entry]\nName=ROOKS\nExec=%s\nType=Application\nCategories=Office;\nTerminal=false\n' "$BIN/rooks-desktop" > "$HOME/.local/share/applications/rooks.desktop"
    echo "Installed: rooks-desktop (in your apps menu as ROOKS). AppImages need libfuse2: sudo apt install libfuse2t64"
  else
    tail -5 "$log"
    echo "The desktop build failed (above); the server and the rooks command are ready. See $DIR/docs/DESKTOP.md"
  fi
else
  echo "Skipped: the desktop app is built on Linux with Node.js 18 or later (Windows and macOS builds are not ready yet)."
fi

say "Done. Open $URL in your browser, or type: rooks"
[[ -z "$new_path" ]] || echo "'rooks' is in ~/.local/bin, which $new_path puts on your PATH: open a new terminal to use it."
