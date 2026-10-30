#!/usr/bin/env bash
# AI Workspace: one-command install for macOS and Linux (Windows: install.ps1).
#
#   From the downloaded folder (zip or clone):   bash install.sh
#   Straight from GitHub:                        curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install.sh | bash
#
# Then start everything with:   ai-workspace
#
# Installs per user, no admin password, no Docker:
#   ai-workspace    the app (native app when a published release has one, else its own Chrome/Edge app window)
#   rooks           Rooks Code, the terminal coding agent
#   rooks-browser   Rooks Browser, the AI browser agent (uses your Chrome or Edge; else downloads Chromium)
# Only prerequisite: curl (preinstalled on macOS). Python 3.12 is fetched by uv (astral.sh) into your home folder.
#
# Options:  --no-start (don't open the app at the end)  --no-browser (skip the Chromium download)
#           --native auto|yes|no (native installer from GitHub releases; default auto)
# Settings: AI_WORKSPACE_REF (branch/tag when downloading, default main)  AI_WORKSPACE_REPO (owner/repo)
#           AI_WORKSPACE_HOME (default ~/.ai-workspace)  AI_WORKSPACE_BIN (default ~/.local/bin)
# Run it again to upgrade. Your chats, keys and settings are kept. Uninstall: QUICKSTART.md.
# Works with the bash 3.2 that ships with macOS.

# Everything runs inside main, so a download cut off halfway executes nothing.
main() {
  set -euo pipefail
  local repo=${AI_WORKSPACE_REPO:-Inclined88888/ai-workspace}
  local ref=${AI_WORKSPACE_REF:-main}
  local bin=${AI_WORKSPACE_BIN:-$HOME/.local/bin}
  local start=true helper_args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-start) start=false ;;
      --no-browser) helper_args+=(--no-browser) ;;
      --native) shift; helper_args+=(--native "${1:-auto}") ;;
      --native=*) helper_args+=(--native "${1#--native=}") ;;
      -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || true; return 0 ;;
      *) printf 'Unknown option %s\n' "$1" >&2; return 2 ;;
    esac
    shift
  done

  bold() { printf '\033[1m%s\033[0m\n' "$*"; }
  fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

  bold "AI Workspace installer"
  case "$(uname -s)" in
    Darwin|Linux) ;;
    *) fail "Unsupported system $(uname -s). On Windows run install.ps1 in PowerShell." ;;
  esac
  command -v curl >/dev/null 2>&1 || fail "curl is required (sudo apt install curl)."
  command -v tar >/dev/null 2>&1 || fail "tar is required."

  # 1. The source: this folder when run from a zip or clone, else a download of $ref (no git needed).
  local src="" here=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    [ -f "$here/workbench/backend/requirements.txt" ] && [ -d "$here/rooks-code" ] && src=$here
  fi
  if [ -z "$src" ]; then
    local tmp
    tmp=$(mktemp -d)
    printf '  Downloading %s (%s)…\n' "$repo" "$ref"
    curl -fsSL "https://codeload.github.com/$repo/tar.gz/$ref" | tar -xz -C "$tmp" || fail "Could not download $repo at $ref."
    src=$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  fi
  printf '  Source: %s\n' "$src"

  # 2. uv: one small binary that brings its own Python, so nothing else has to be installed first.
  export PATH="$bin:$HOME/.cargo/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    printf '  Installing uv (Python manager, astral.sh) into %s…\n' "$bin"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$bin" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null \
      || fail "Could not install uv. See https://docs.astral.sh/uv/getting-started/installation/"
  fi
  export UV
  UV=$(command -v uv)

  # 3. Everything else is the same on every OS: desktop/launcher/install_helper.py.
  "$UV" run --quiet --no-project --python 3.12 "$src/desktop/launcher/install_helper.py" --src "$src" --bin "$bin" \
    ${helper_args[@]+"${helper_args[@]}"}

  if $start; then
    printf '\nStarting AI Workspace…\n'
    "$bin/ai-workspace" || true
  fi
}

main "$@"
