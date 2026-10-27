#!/usr/bin/env bash
# Safe ROOKS upgrade from an update package (rooks-update-*.tar.gz), with a backup and a way back.
#
#   upgrade.sh PACKAGE.tar.gz          check, back up, apply, then run ./setup.sh
#   upgrade.sh --check PACKAGE.tar.gz  show what would change; change nothing
#   upgrade.sh --rollback [BACKUP]     put back the code from the newest (or given) backup
#   upgrade.sh --list-backups
#
# Options:
#   --yes             do not ask before applying
#   --overwrite       replace files you edited yourself (they are still in the backup)
#   --no-setup        do not run ./setup.sh afterwards
#   --setup "ARGS"    arguments for ./setup.sh (default: none, which keeps your add-ons)
#   --sha256 HEX      expected checksum, when there is no PACKAGE.sha256 file next to the package
#   --no-verify       skip the checksum (not recommended)
#
# Never touched: .env, your data (Docker volumes), and files that are not part of ROOKS.
# Settings: ROOKS_DIR (install folder, default ~/ai-workspace), ROOKS_BACKUP_DIR (default ~/ai-workspace-backups).
# Works with the bash 3.2 that ships with macOS.

main() {
  set -euo pipefail
  local dir=${ROOKS_DIR:-$HOME/ai-workspace}
  local backups=${ROOKS_BACKUP_DIR:-$HOME/ai-workspace-backups}
  local mode=apply yes=false overwrite=false run_setup=true setup_args="" want_sha="" verify=true pkg="" target=""

  say() { printf '%s\n' "$*"; }
  bold() { printf '\033[1m%s\033[0m\n' "$*"; }
  fail() { printf '\033[31mStopped:\033[0m %s\n' "$*" >&2; exit 1; }
  sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

  while [ $# -gt 0 ]; do
    case "$1" in
      --check) mode=check ;;
      --rollback) mode=rollback; if [ $# -gt 1 ] && [ "${2#--}" = "$2" ]; then target=$2; shift; fi ;;
      --list-backups) mode=list ;;
      --yes|-y) yes=true ;;
      --overwrite) overwrite=true ;;
      --no-setup) run_setup=false ;;
      --setup) setup_args=${2:-}; shift ;;
      --sha256) want_sha=${2:-}; shift ;;
      --no-verify) verify=false ;;
      -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
      -*) fail "Unknown option $1" ;;
      *) pkg=$1 ;;
    esac
    shift
  done

  local tty=false
  if (exec </dev/tty) 2>/dev/null; then tty=true; fi
  confirm() { $yes && return 0; $tty || return 1; local a=""; read -r -p "$1 [y/N]: " a </dev/tty || true; case "$a" in [Yy]*) return 0 ;; esac; return 1; }

  if [ "$mode" = list ]; then
    ls -1t "$backups"/rooks-*.tar.gz 2>/dev/null || say "No backups in $backups"
    exit 0
  fi

  if [ "$mode" = rollback ]; then
    [ -n "$target" ] || target=$(ls -1t "$backups"/rooks-*.tar.gz 2>/dev/null | head -1 || true)
    [ -n "$target" ] && [ -f "$target" ] || fail "No backup found in $backups."
    [ -d "$dir" ] || fail "$dir does not exist."
    bold "Roll back $dir to $(basename "$target")"
    say "The current folder is kept as $dir.before-rollback-<time>, so this can be undone too."
    confirm "Continue?" || fail "Cancelled."
    local tmp; tmp=$(mktemp -d)
    tar xzf "$target" -C "$tmp"
    [ -f "$tmp/$(basename "$dir")/docker-compose.yml" ] || { rm -rf "$tmp"; fail "That backup does not look like a ROOKS folder."; }
    local aside; aside="$dir.before-rollback-$(date +%Y%m%d-%H%M%S)"
    mv "$dir" "$aside"
    mv "$tmp/$(basename "$dir")" "$dir"
    rm -rf "$tmp"
    say "Code restored. Your data was not changed (database changes in updates only add columns, so older code still reads it)."
    if $run_setup && [ -x "$dir/setup.sh" ]; then (cd "$dir" && ./setup.sh $setup_args); else say "Now run: cd $dir && ./setup.sh"; fi
    exit 0
  fi

  [ -n "$pkg" ] || fail "Give the update package, for example: upgrade.sh ~/Downloads/rooks-update-v7.tar.gz"
  [ -f "$pkg" ] || fail "Package not found: $pkg"
  [ -d "$dir" ] && [ -f "$dir/docker-compose.yml" ] || fail "No ROOKS install at $dir (set ROOKS_DIR if it is elsewhere)."

  # 1. Integrity
  local got; got=$(sha "$pkg")
  if $verify; then
    if [ -z "$want_sha" ] && [ -f "$pkg.sha256" ]; then want_sha=$(cut -d' ' -f1 < "$pkg.sha256"); fi
    [ -n "$want_sha" ] || fail "No checksum: put $(basename "$pkg").sha256 next to the package, pass --sha256, or (not recommended) --no-verify."
    [ "$got" = "$want_sha" ] || fail "Checksum does not match. The package is damaged or not the one published. Expected $want_sha, got $got."
    say "Checksum OK ($got)"
  else
    say "Checksum not verified (--no-verify). SHA-256 of the package: $got"
  fi

  # 2. Safe extraction: one top folder, no absolute paths, no "..", no hard links, and symbolic links only when they
  #    point to a relative path without ".." (so they stay inside ROOKS)
  local listing; listing=$(tar tzvf "$pkg") || fail "The package is not a readable .tar.gz."
  if printf '%s\n' "$listing" | grep -qE '^h'; then fail "The package contains hard links. Not applying it."; fi
  if printf '%s\n' "$listing" | grep -E '^l' | sed 's/.* -> //' | grep -qE '(^/|(^|/)\.\.(/|$))'; then
    fail "The package contains a link that points outside ROOKS. Not applying it."
  fi
  local names; names=$(tar tzf "$pkg")
  if printf '%s\n' "$names" | grep -qE '(^/|(^|/)\.\.(/|$))'; then fail "The package contains unsafe paths. Not applying it."; fi
  local top; top=$(printf '%s\n' "$names" | head -1 | cut -d/ -f1)
  if printf '%s\n' "$names" | grep -vqE "^$top(/|$)"; then fail "The package has more than one top folder. Not applying it."; fi
  local work; work=$(mktemp -d)
  trap "rm -rf '$work'" EXIT
  tar xzf "$pkg" -C "$work"
  local new="$work/$top"
  [ -f "$new/docker-compose.yml" ] && [ -f "$new/setup.sh" ] && [ -d "$new/workbench" ] || fail "This is not a ROOKS update package."
  [ ! -e "$new/.env" ] || fail "The package contains a .env file. Updates never ship secrets. Not applying it."
  # Compatibility: the Docker Compose project name decides which data volumes are used. A different name would start
  # ROOKS with empty data and leave yours behind, so such a package is refused.
  local old_name new_name
  old_name=$(sed -n 's/^name:[[:space:]]*//p' "$dir/docker-compose.yml" | head -1)
  new_name=$(sed -n 's/^name:[[:space:]]*//p' "$new/docker-compose.yml" | head -1)
  if [ -n "$old_name" ] && [ "$old_name" != "$new_name" ]; then
    fail "This package uses the Docker project name '$new_name', but your install uses '$old_name'. Applying it would hide your data. Not applying it."
  fi

  # 3. What changes. Paths are relative; .env, .rooks and Git metadata are never part of an update.
  (cd "$new" && find . -type f ! -path './.git/*' ! -path './.rooks/*' ! -name .env | sed 's|^\./||' | LC_ALL=C sort) > "$work/new.list"
  local manifest="$dir/.rooks/manifest.sha256"
  local changed=0 added=0 conflicts="" removed="" same=0 f
  while IFS= read -r f; do
    if [ ! -e "$dir/$f" ]; then added=$((added + 1)); continue; fi
    local cur newh; cur=$(sha "$dir/$f"); newh=$(sha "$new/$f")
    if [ "$cur" = "$newh" ]; then same=$((same + 1)); continue; fi
    changed=$((changed + 1))
    if [ -f "$manifest" ]; then
      local was; was=$(awk -v p="$f" '{h=$1; $1=""; sub(/^ +/,""); if ($0==p) print h}' "$manifest")
      # Edited since the last upgrade, or a file of yours at a path this version now uses: your change, not ours
      if [ "$was" != "$cur" ]; then conflicts="$conflicts$f"$'\n'; fi
    fi
  done < "$work/new.list"
  # A git checkout: uncommitted edits to files this update changes are yours, record or not
  if [ -d "$dir/.git" ] && command -v git >/dev/null 2>&1; then
    (cd "$dir" && git status --porcelain 2>/dev/null | sed -E 's/^.. //; s/.* -> //') > "$work/git.list" || true
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      if grep -qxF "$f" "$work/new.list" && [ -f "$dir/$f" ] && [ "$(sha "$dir/$f")" != "$(sha "$new/$f")" ]; then
        # Not yours if it is exactly what the last ROOKS upgrade wrote (git sees earlier updates as uncommitted too)
        if [ -f "$manifest" ] && [ "$(awk -v p="$f" '{h=$1; $1=""; sub(/^ +/,""); if ($0==p) print h}' "$manifest")" = "$(sha "$dir/$f")" ]; then
          continue
        fi
        case "$conflicts" in *"$f"*) ;; *) conflicts="$conflicts$f (uncommitted change in git)"$'\n' ;; esac
      fi
    done < "$work/git.list"
  fi
  if [ -f "$manifest" ]; then
    # Files the previous version shipped and this one does not: removed if you did not edit them
    while IFS= read -r line; do
      local h p; h=${line%% *}; p=${line#* }; p=${p# }
      grep -qxF "$p" "$work/new.list" && continue
      [ -f "$dir/$p" ] || continue
      if [ "$(sha "$dir/$p")" = "$h" ]; then removed="$removed$p"$'\n'; else conflicts="$conflicts$p (removed in this version)"$'\n'; fi
    done < "$manifest"
  fi

  bold "ROOKS update: $(basename "$pkg")"
  say "  New files: $added   Changed: $changed   Unchanged: $same   Removed: $(printf '%s' "$removed" | grep -c . || true)"
  if [ ! -f "$manifest" ]; then
    say "  This install has no record of the last update, so edits you made yourself cannot be told apart from older"
    say "  ROOKS code. Every changed file is in the backup, and after this update ROOKS keeps that record."
  fi
  if [ -n "$conflicts" ]; then
    say "  Files you edited yourself that this update would replace:"
    printf '%s' "$conflicts" | sed 's/^/    /'
    if ! $overwrite; then
      [ "$mode" = check ] && exit 2
      fail "Nothing was changed. Keep a copy of your edits, then run again with --overwrite (they are also saved in the backup)."
    fi
  fi
  [ "$mode" = check ] && { say "Check only: nothing was changed."; exit 0; }
  confirm "Back up and apply this update?" || fail "Cancelled. Nothing was changed."

  # 4. Backup: the code folder (including .env, so a rollback is complete) and, if running, a copy of the database
  mkdir -p "$backups"; chmod 700 "$backups"
  local stamp; stamp=$(date +%Y%m%d-%H%M%S)
  local bk="$backups/rooks-$stamp.tar.gz"
  (umask 077; tar czf "$bk" -C "$(dirname "$dir")" --exclude=node_modules --exclude=dist --exclude=__pycache__ --exclude=.pytest_cache "$(basename "$dir")")
  say "Backup: $bk"
  if command -v docker >/dev/null 2>&1 && (cd "$dir" && docker compose ps --status running --services 2>/dev/null | grep -qx workbench); then
    if (cd "$dir" && docker compose exec -T workbench python -c "import sqlite3; s=sqlite3.connect('/data/workbench.db'); d=sqlite3.connect('/data/upgrade-backup.db'); s.backup(d); d.close()" \
        && docker compose cp workbench:/data/upgrade-backup.db "$backups/rooks-$stamp.db" >/dev/null \
        && docker compose exec -T workbench rm -f /data/upgrade-backup.db); then
      chmod 600 "$backups/rooks-$stamp.db"; say "Database copy: $backups/rooks-$stamp.db"
    else
      say "Could not copy the database (the code backup is still complete). Continuing."
    fi
  fi

  # 5. Apply
  while IFS= read -r f; do
    mkdir -p "$dir/$(dirname "$f")"
    cp -p "$new/$f" "$dir/$f"
  done < "$work/new.list"
  printf '%s' "$removed" | while IFS= read -r f; do [ -n "$f" ] && rm -f "$dir/$f"; done
  # Symbolic links (already checked to stay inside): create or update, but never replace a real file or folder
  (cd "$new" && find . -type l | sed 's|^\./||') | while IFS= read -r l; do
    local tgt; tgt=$(readlink "$new/$l")
    if [ -L "$dir/$l" ] || [ ! -e "$dir/$l" ]; then mkdir -p "$dir/$(dirname "$l")"; ln -sfn "$tgt" "$dir/$l"
    else say "  Kept your $l (a real file or folder where this version has a link)."; fi
  done
  mkdir -p "$dir/.rooks"
  : > "$manifest.tmp"
  while IFS= read -r f; do printf '%s  %s\n' "$(sha "$dir/$f")" "$f" >> "$manifest.tmp"; done < "$work/new.list"
  mv "$manifest.tmp" "$manifest"
  printf 'package=%s\nsha256=%s\nbackup=%s\nat=%s\n' "$(basename "$pkg")" "$got" "$bk" "$stamp" > "$dir/.rooks/last-upgrade"
  say "Update applied."

  # 6. Start it
  if $run_setup; then
    if ! (cd "$dir" && ./setup.sh $setup_args); then
      say ""
      say "Setup did not finish. To go back to the previous version:"
      say "  bash $dir/scripts/upgrade.sh --rollback $bk"
      exit 1
    fi
  else
    say "Now run: cd $dir && ./setup.sh"
  fi
  say "If anything is wrong: bash $dir/scripts/upgrade.sh --rollback $bk"
}

main "$@"
