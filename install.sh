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
#   rooks-desk      Rooks Desk, computer use: your apps, terminal and files, asking before anything risky
#                   (macOS: run `rooks-desk setup` once to grant Screen Recording + Accessibility)
# Only prerequisite: curl (preinstalled on macOS). Python 3.12 is fetched by uv (astral.sh) into your home folder.
#
# Options:  --no-start (don't open the app at the end)  --no-browser (skip the Chromium download)
#           --no-desk (don't install rooks-desk, computer use)
#           --native auto|yes|no (native installer from GitHub releases; default auto)
#           --no-helpers (don't offer the optional helper apps: Tesseract, FFmpeg, LibreOffice)
# Settings: AI_WORKSPACE_REF (branch/tag when downloading, default main)  AI_WORKSPACE_REPO (owner/repo)
#           AI_WORKSPACE_HELPERS=yes|no (answer the helper-apps question in advance)
#           AI_WORKSPACE_HOME (default ~/.ai-workspace)  AI_WORKSPACE_BIN (default ~/.local/bin)
# Run it again to upgrade. Your chats, keys and settings are kept. Uninstall: QUICKSTART.md.
# Works with the bash 3.2 that ships with macOS.

# Tesseract (scanned pages), FFmpeg (video details), LibreOffice (old .doc/.xls/.ppt). Never fails the install.
optional_helpers() {
  local answer=$1 missing="" cmd=""
  command -v tesseract >/dev/null 2>&1 || missing="$missing tesseract"
  command -v ffprobe >/dev/null 2>&1 || missing="$missing ffmpeg"
  { command -v soffice || command -v libreoffice || [ -d /Applications/LibreOffice.app ]; } >/dev/null 2>&1 || missing="$missing libreoffice"
  [ -n "$missing" ] || return 0
  case "$answer" in no|0|false) return 0 ;; esac
  if [ "$answer" = ask ] && [ -r /dev/tty ] && [ -t 1 ]; then
    printf '\nOptional: install free helper apps so Files can read more (%s)? [Y/n] ' "${missing# }"
    local reply=""
    read -r -t 30 reply </dev/tty || reply=""
    case "$reply" in n|N|no|No) printf '  Skipped. The Files page can install them later.\n'; return 0 ;; esac
  fi
  if [ "$(uname -s)" = Darwin ]; then
    if ! command -v brew >/dev/null 2>&1; then
      printf '  Helper apps skipped (Homebrew not found). The Files page explains how to add them.\n'; return 0
    fi
    local m
    for m in $missing; do
      printf '  Installing %s (optional)…\n' "$m"
      if [ "$m" = libreoffice ]; then cmd="brew install --cask --quiet libreoffice"; else cmd="brew install --quiet $m"; fi
      $cmd >/dev/null 2>&1 || printf '  %s skipped (install failed). The Files page can retry.\n' "$m"
    done
  elif command -v apt-get >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    local pkgs=""
    case "$missing" in *tesseract*) pkgs="$pkgs tesseract-ocr" ;; esac
    case "$missing" in *ffmpeg*) pkgs="$pkgs ffmpeg" ;; esac
    case "$missing" in *libreoffice*) pkgs="$pkgs libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress" ;; esac
    printf '  Installing helper apps (optional):%s…\n' "$pkgs"
    # shellcheck disable=SC2086
    sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $pkgs >/dev/null 2>&1 \
      || printf '  Helper apps skipped (install failed). The Files page can retry.\n'
  else
    printf '  Helper apps skipped (needs an admin password). To add them:  sudo apt install tesseract-ocr ffmpeg libreoffice\n'
  fi
  return 0
}

# Everything runs inside main, so a download cut off halfway executes nothing.
main() {
  set -euo pipefail
  local repo=${AI_WORKSPACE_REPO:-Inclined88888/ai-workspace}
  local ref=${AI_WORKSPACE_REF:-main}
  local bin=${AI_WORKSPACE_BIN:-$HOME/.local/bin}
  local start=true helper_args=() helpers=${AI_WORKSPACE_HELPERS:-ask}
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-start) start=false ;;
      --no-browser) helper_args+=(--no-browser) ;;
      --no-desk) helper_args+=(--no-desk) ;;
      --no-helpers) helpers=no ;;
      --native) shift; helper_args+=(--native "${1:-auto}") ;;
      --native=*) helper_args+=(--native "${1#--native=}") ;;
      -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || true; return 0 ;;
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
  export AI_WORKSPACE_ORIG_PATH=$PATH  # so the helper can tell whether $bin is on the user's own PATH
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

  # 4. Optional helper apps for the Files page (scanned pages, video details, old Office files). Quiet, skipped on
  #    any failure, never stops the install; the Files page offers them again later with one Install button.
  optional_helpers "$helpers" || true

  if $start; then
    printf '\nStarting AI Workspace…\n'
    "$bin/ai-workspace" || true
  fi
}

main "$@"
