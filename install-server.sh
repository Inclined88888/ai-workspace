#!/usr/bin/env bash
# One command install for AI Workspace:
#
#   curl -fsSL https://raw.githubusercontent.com/Inclined88888/ai-workspace/main/install-server.sh | bash
#
# Pass setup options after `bash -s --`:
#
#   curl -fsSL .../install-server.sh | bash -s -- --with search,browser
#   curl -fsSL .../install-server.sh | bash -s -- --yes
#
# Running it again upgrades in place: code is updated, .env, secrets and data are kept.
# Settings (environment variables):
#   AI_WORKSPACE_DIR   install folder            (default: ~/ai-workspace)
#   AI_WORKSPACE_REPO  GitHub owner/repo         (default: Inclined88888/ai-workspace)
#   AI_WORKSPACE_REF   branch or tag             (default: main)
# Works with the bash 3.2 that ships with macOS.

# Everything runs inside main, so a download cut off halfway executes nothing.
main() {
  set -euo pipefail

  local repo=${AI_WORKSPACE_REPO:-Inclined88888/ai-workspace}
  local ref=${AI_WORKSPACE_REF:-main}
  local dir=${AI_WORKSPACE_DIR:-$HOME/ai-workspace}
  local url="https://github.com/$repo.git"
  case "$repo" in /*|./*|file://*) url=$repo ;; esac # local path, for testing

  bold() { printf '\033[1m%s\033[0m\n' "$*"; }
  info() { printf '  %s\n' "$*"; }
  fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

  # Under `curl | bash` stdin is the script, so prompts must read the terminal.
  local tty=false
  if (exec </dev/tty) 2>/dev/null; then tty=true; fi
  confirm() { # confirm "Question" -> 0 for yes
    $tty || return 1
    local ans=""
    read -r -p "  $1 [y/N]: " ans </dev/tty || true
    [[ "$ans" =~ ^[Yy] ]]
  }

  bold "AI Workspace installer"
  local os
  os=$(uname -s)
  case "$os" in
    Linux|Darwin) ;;
    *) fail "Unsupported system $os. Use Linux or macOS (on Windows, run this inside WSL 2)." ;;
  esac
  command -v curl >/dev/null 2>&1 || fail "curl is required."

  # Docker
  if ! command -v docker >/dev/null 2>&1; then
    if [[ "$os" == Darwin ]]; then
      fail "Docker is not installed. Install Docker Desktop (docker.com) or OrbStack (orbstack.dev), start it once, then run this command again."
    fi
    info "Docker is not installed."
    if confirm "Install it now with Docker's official script (get.docker.com, needs sudo)?"; then
      curl -fsSL https://get.docker.com | sh
      if [[ $(id -u) -ne 0 ]] && ! docker info >/dev/null 2>&1; then
        sudo usermod -aG docker "$USER"
        fail "Docker is installed. Log out and back in (so your user can use Docker), then run this command again."
      fi
    else
      fail "Install Docker Engine (https://docs.docker.com/engine/install/), then run this command again."
    fi
  fi
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing. Install the docker-compose-plugin package (or update Docker Desktop)."
  if ! docker info >/dev/null 2>&1; then
    if [[ "$os" == Darwin ]]; then
      fail "Docker is not running. Start Docker Desktop or OrbStack, then run this command again."
    elif [[ $(id -u) -ne 0 ]] && sudo -n docker info >/dev/null 2>&1; then
      fail "Your user cannot use Docker. Run: sudo usermod -aG docker $USER  then log out and back in, and run this command again."
    else
      fail "Docker is not running. Start it (sudo systemctl start docker), then run this command again."
    fi
  fi

  # Code: clone, or update an existing install.
  # On a Mac /usr/bin/git is a stub until the Command Line Tools are installed, so check for those.
  local git_ok=false
  if command -v git >/dev/null 2>&1; then
    if [[ "$os" != Darwin ]] || xcode-select -p >/dev/null 2>&1 || [[ "$(command -v git)" != /usr/bin/git ]]; then git_ok=true; fi
  fi
  if $git_ok && [[ -d "$dir/.git" ]]; then
    bold "Updating $dir"
    git -C "$dir" fetch origin "$ref" || fail "Could not fetch $ref from $url."
    git -C "$dir" merge --ff-only FETCH_HEAD || fail "$dir has local changes that conflict with the update. Commit or discard them, then run this again."
  elif [[ -e "$dir" && ! -f "$dir/setup.sh" ]]; then
    fail "$dir already exists and is not an AI Workspace install. Set AI_WORKSPACE_DIR to another folder."
  elif $git_ok && [[ ! -e "$dir" ]]; then
    bold "Downloading to $dir"
    git clone --depth 1 --branch "$ref" "$url" "$dir" || fail "Could not download $url (branch $ref). Is the repository public?"
  else
    # No git, or an earlier install that was unpacked from an archive: unpack over it. .env is not in the archive, so it is kept.
    bold "Downloading to $dir"
    mkdir -p "$dir"
    curl -fsSL "https://codeload.github.com/$repo/tar.gz/$ref" | tar xz -C "$dir" --strip-components 1 \
      || fail "Could not download $repo ($ref). Is the repository public?"
  fi

  # Setup
  cd "$dir"
  if $tty; then
    ./setup.sh "$@" </dev/tty
  else
    info "No terminal for questions: using defaults (add keys later in Settings)."
    ./setup.sh --yes "$@"
  fi
  echo
  info "Installed in $dir. Run this command again any time to upgrade."
}

main "$@"
