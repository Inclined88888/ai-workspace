#!/usr/bin/env bash
# ROOKS installer: macOS, Linux and Windows through WSL2; Bash 3.2+.
# Run with AI_WORKSPACE_REF=<full commit SHA> to install a reviewed revision.
# Requires Git and a running Docker engine with Compose v2.
# Existing installs must be clean, from the expected origin, and fast-forwardable.
# No system packages are installed and no sudo commands are run.

# A truncated curl | bash download cannot call main until the final line arrives.
main() (
  set -euo pipefail
  # The code must stay readable by the containers' non-root users; secrets are protected separately (setup.sh
  # keeps .env at mode 600). An owner-only umask here made every container fail to read its own code.
  umask 022
  local repo=${AI_WORKSPACE_REPO:-Inclined88888/ai-workspace}
  local ref=${AI_WORKSPACE_REF:-main}
  local dir=${AI_WORKSPACE_DIR:-$HOME/ai-workspace}
  local url staging="" os arch tty=false
  info() { printf '  %s\n' "$*"; }
  fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
  cleanup() { if [[ -n "$staging" && -d "$staging" ]]; then rm -rf -- "$staging"; fi; }
  trap cleanup EXIT

  case "${1:-}" in
    -h|--help)
      info 'ROOKS: install.sh [--yes] [--with search,browser,...]'
      info 'AI_WORKSPACE_DIR: destination (default ~/ai-workspace)'
      info 'AI_WORKSPACE_REF: branch, tag or full commit SHA (default main)'
      info 'Requires Git and Docker with Compose v2; supports macOS, Linux and WSL2.'
      return 0 ;;
  esac
  [[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || fail 'Invalid branch, tag or commit reference.'
  case "$repo" in
    /*) url=$repo ;; # local repository fixtures or explicitly trusted local source
    *) [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail 'Repository must be GitHub owner/name.'
       url="https://github.com/$repo.git" ;;
  esac
  [[ "$dir" == /* && "$dir" != / ]] || fail 'AI_WORKSPACE_DIR must be an absolute directory path, not /.'
  [[ ! -L "$dir" ]] || fail 'Refusing a symbolic-link installation directory.'

  printf 'ROOKS installer\n'
  os=$(uname -s); arch=$(uname -m)
  case "$os" in
    Linux|Darwin) ;;
    *) fail "Unsupported system $os. On Windows, use a WSL2 Linux terminal with Docker integration." ;;
  esac
  case "$arch" in
    x86_64|amd64|arm64|aarch64) ;;
    *) fail "Unsupported architecture $arch. This installer targets 64-bit Intel/AMD and ARM." ;;
  esac
  command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1 || fail 'Install Git first (on macOS: xcode-select --install).'
  command -v docker >/dev/null 2>&1 || fail 'Install and start Docker with Compose v2 first: https://docs.docker.com/get-started/get-docker/'
  docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is required.'
  docker info >/dev/null 2>&1 || fail 'Docker is not running or your user cannot access it. Start Docker and check your permissions.'

  if [[ -e "$dir" ]]; then
    [[ -d "$dir/.git" && ! -L "$dir/.git" ]] || fail "$dir exists but is not a standalone Git install. Choose another AI_WORKSPACE_DIR."
    [[ "$(git -C "$dir" rev-parse --show-toplevel)" == "$(cd "$dir" && pwd -P)" ]] || fail 'Unexpected Git working directory.'
    local origin
    origin=$(git -C "$dir" remote get-url origin) || fail 'The install has no origin.'
    [[ "$origin" == "$url" ]] || fail 'Existing install has a different source. Nothing was updated.'
    [[ -z "$(git -C "$dir" status --porcelain --untracked-files=normal)" ]] || fail 'Local edits or untracked files found. Save them before updating; nothing was overwritten.'
    info "Fetching $repo at $ref"
    git -C "$dir" fetch origin "$ref" || fail 'Download failed. Existing code and data were kept.'
    # Refuse divergent history and downgrades; never reset or overwrite local work.
    git -C "$dir" -c core.hooksPath=/dev/null -c submodule.recurse=false merge --ff-only FETCH_HEAD || fail 'Update is not a fast-forward. Review the history manually.'
  else
    mkdir -p "$(dirname "$dir")"
    staging=$(mktemp -d "$(dirname "$dir")/.rooks-download.XXXXXX")
    git init -q "$staging"
    git -C "$staging" remote add origin "$url"
    git -C "$staging" fetch --depth 1 origin "$ref" || fail 'Download failed. Check repository access and the requested revision.'
    git -C "$staging" -c core.hooksPath=/dev/null -c submodule.recurse=false checkout -q --detach FETCH_HEAD
    [[ -f "$staging/setup.sh" && ! -L "$staging/setup.sh" ]] || fail 'Downloaded revision has no regular setup.sh.'
    # -T is unavailable on macOS. A destination appearing during download must
    # not turn mv into a move-inside-an-existing-folder operation.
    [[ ! -e "$dir" && ! -L "$dir" ]] || fail 'Destination appeared during download. Nothing was installed.'
    mv "$staging" "$dir"
    staging=""
  fi

  local actual
  actual=$(git -C "$dir" rev-parse HEAD)
  # A full SHA is an exact pin, not a branch name. Check before executing setup.
  if [[ "$ref" =~ ^[a-fA-F0-9]{40}$ ]]; then
    [[ "$(printf '%s' "$actual" | tr A-F a-f)" == "$(printf '%s' "$ref" | tr A-F a-f)" ]] || fail 'Downloaded commit does not match the requested pin.'
  fi
  [[ -f "$dir/setup.sh" && ! -L "$dir/setup.sh" ]] || fail 'No regular setup.sh in this revision.'
  info "Revision: $actual"
  if (exec </dev/tty) 2>/dev/null; then tty=true; fi
  cd "$dir"
  if $tty; then
    bash ./setup.sh "$@" </dev/tty
  else
    info 'No terminal: using defaults; configure models later in Settings.'
    bash ./setup.sh --yes "$@"
  fi
  info "Installed in $dir. Interface: http://localhost:3100 (unless configured otherwise)."
  info 'Use the same pin to repeat this installation, or a newly reviewed pin to update.'
)

main "$@"
