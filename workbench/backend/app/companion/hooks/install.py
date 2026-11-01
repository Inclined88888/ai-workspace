#!/usr/bin/env python3
"""
Rooks Companion Hook Installer

Installs/uninstalls/checks the hook bridge for Claude Code, Codex CLI, and Gemini CLI.

Usage:
    install.py install [--agent claude|codex|gemini|all] [--home DIR]
    install.py uninstall [--agent claude|codex|gemini|all] [--home DIR]
    install.py status [--agent claude|codex|gemini|all] [--home DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Constants & Paths
# ──────────────────────────────────────────────────────────────────────────────

HOOK_SCRIPT_NAME = "rooks_companion_hook.py"
ROOKS_HOOKS_DIR = Path.home() / ".rooks" / "hooks"

# Agent config paths
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
GEMINI_SETTINGS = Path.home() / ".gemini" / "settings.json"

# Our hook marker for idempotent uninstall
HOOK_MARKER = "rooks-companion-hook"
INSTALL_TAG = f"rooks-companion-hook-{int(time.time())}"

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _backup_file(path: Path) -> Path | None:
    """Create a timestamped backup of a file. Returns backup path or None."""
    if not path.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".backup.{ts}")
    try:
        shutil.copy2(path, backup)
        return backup
    except Exception:
        return None


def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def _write_json(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)
        return True
    except Exception:
        return False


def _read_toml(path: Path) -> dict:
    """Read TOML using stdlib tomllib (Python 3.11+)."""
    try:
        if not path.exists():
            return {}
        import tomllib
        return tomllib.loads(path.read_text())
    except Exception:
        return {}


def _write_toml(path: Path, data: dict) -> bool:
    """Write TOML using a simple formatter for our specific structure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Use tomli_w if available, otherwise simple formatter
        try:
            import tomli_w
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(tomli_w.dumps(data))
            tmp.replace(path)
            return True
        except ImportError:
            # Fallback: simple formatter for our known structure
            lines = []
            # Top-level keys
            for k, v in data.items():
                if not isinstance(v, dict):
                    if isinstance(v, bool):
                        lines.append(f'{k} = {str(v).lower()}')
                    elif isinstance(v, str):
                        lines.append(f'{k} = "{v}"')
                    else:
                        lines.append(f'{k} = {v}')
            lines.append("")
            # Sections
            for section, sect_data in data.items():
                if isinstance(sect_data, dict):
                    # Special handling for hooks section which uses array of tables
                    if section == "hooks" and isinstance(sect_data, dict):
                        for k, val in sect_data.items():
                            if isinstance(val, list):
                                # Array of tables: [[hooks.k]]
                                for item in val:
                                    lines.append(f"[[hooks.{k}]]")
                                    for ik, iv in item.items():
                                        if ik == "hooks" and isinstance(iv, list):
                                            # Nested array of tables: [[hooks.k.hooks]]
                                            for hook in iv:
                                                lines.append(f"[[hooks.{k}.hooks]]")
                                                for hk, hv in hook.items():
                                                    if isinstance(hv, str):
                                                        lines.append(f'{hk} = "{hv}"')
                                                    elif isinstance(hv, bool):
                                                        lines.append(f'{hk} = {str(hv).lower()}')
                                                    else:
                                                        lines.append(f'{hk} = {hv}')
                                                lines.append("")
                                        elif isinstance(iv, str):
                                            lines.append(f'{ik} = "{iv}"')
                                        elif isinstance(iv, bool):
                                            lines.append(f'{ik} = {str(iv).lower()}')
                                        else:
                                            lines.append(f'{ik} = {iv}')
                                    lines.append("")
                            elif isinstance(val, str):
                                lines.append(f'{k} = "{val}"')
                            elif isinstance(val, bool):
                                lines.append(f'{k} = {str(val).lower()}')
                            else:
                                lines.append(f'{k} = {val}')
                        lines.append("")
                    else:
                        lines.append(f"[{section}]")
                        for k, val in sect_data.items():
                            if isinstance(val, dict):
                                # Nested table
                                lines.append(f"[{section}.{k}]")
                                for nk, nv in val.items():
                                    if isinstance(nv, list):
                                        items = ", ".join(f'"{i}"' for i in nv)
                                        lines.append(f'{nk} = [{items}]')
                                    elif isinstance(nv, str):
                                        lines.append(f'{nk} = "{nv}"')
                                    elif isinstance(nv, bool):
                                        lines.append(f'{nk} = {str(nv).lower()}')
                                    else:
                                        lines.append(f'{nk} = {nv}')
                                lines.append("")
                            elif isinstance(val, list):
                                items = ", ".join(f'"{i}"' for i in val)
                                lines.append(f'{k} = [{items}]')
                            elif isinstance(val, str):
                                lines.append(f'{k} = "{val}"')
                            elif isinstance(val, bool):
                                lines.append(f'{k} = {str(val).lower()}')
                            else:
                                lines.append(f'{k} = {val}')
                        lines.append("")
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text("\n".join(lines))
            tmp.replace(path)
            return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Hook Script Deployment
# ──────────────────────────────────────────────────────────────────────────────

def _deploy_hook_script(source_dir: Path) -> Path | None:
    """Copy hook script to ~/.rooks/hooks/. Returns destination path or None."""
    try:
        ROOKS_HOOKS_DIR.mkdir(parents=True, exist_ok=True)
        src = source_dir / HOOK_SCRIPT_NAME
        if not src.exists():
            return None
        dst = ROOKS_HOOKS_DIR / HOOK_SCRIPT_NAME
        shutil.copy2(src, dst)
        dst.chmod(0o755)
        return dst
    except Exception:
        return None


def _remove_hook_script() -> bool:
    """Remove the deployed hook script."""
    try:
        hook_path = ROOKS_HOOKS_DIR / HOOK_SCRIPT_NAME
        if hook_path.exists():
            hook_path.unlink()
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Claude Code Integration
# ──────────────────────────────────────────────────────────────────────────────

CLAUDE_HOOKS_CONFIG = {
    "hooks": {
        "SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} claude SessionStart", "name": "rooks-companion-session-start"}]}],
        "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} claude UserPromptSubmit", "name": "rooks-companion-user-prompt"}]}],
        "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} claude PreToolUse", "name": "rooks-companion-pre-tool"}]}],
        "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} claude PostToolUse", "name": "rooks-companion-post-tool"}]}],
        "PostToolUseFailure": [{"matcher": "", "hooks": [{"type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} claude PostToolUseFailure", "name": "rooks-companion-post-tool-failure"}]}],
        "Notification": [{"matcher": "", "hooks": [{"type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} claude Notification", "name": "rooks-companion-notification"}]}],
        "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} claude Stop", "name": "rooks-companion-stop"}]}],
        "SubagentStop": [{"matcher": "", "hooks": [{"type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} claude SubagentStop", "name": "rooks-companion-subagent-stop"}]}],
        "SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} claude SessionEnd", "name": "rooks-companion-session-end"}]}],
    }
}


def _merge_claude_hooks(existing: dict, new_hooks: dict) -> dict:
    """Merge our hooks into existing settings, tagging them for uninstall."""
    result = json.loads(json.dumps(existing))  # deep copy
    hooks = result.setdefault("hooks", {})
    for event, handlers in new_hooks.items():
        existing_handlers = hooks.get(event, [])
        # Filter out our old tagged handlers
        filtered = [h for h in existing_handlers if HOOK_MARKER not in json.dumps(h)]
        # Add our new handlers with tag
        for group in handlers:
            for hook in group.get("hooks", []):
                hook["name"] = f"{hook['name']}-{INSTALL_TAG}"
        filtered.extend(handlers)
        hooks[event] = filtered
    return result


def _remove_claude_hooks(settings: dict) -> dict:
    """Remove our tagged hooks from settings."""
    result = json.loads(json.dumps(settings))
    hooks = result.get("hooks", {})
    for event, handlers in list(hooks.items()):
        filtered = [h for h in handlers if HOOK_MARKER not in json.dumps(h)]
        if filtered:
            hooks[event] = filtered
        else:
            hooks.pop(event, None)
    return result


def install_claude(hook_path: Path) -> bool:
    settings = _read_json(CLAUDE_SETTINGS)
    _backup_file(CLAUDE_SETTINGS)
    # Update command paths in our config template
    config = json.loads(json.dumps(CLAUDE_HOOKS_CONFIG))
    cmd = f"{hook_path} claude"
    for event, groups in config["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                hook["command"] = hook["command"].replace(f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME}", str(hook_path))
    merged = _merge_claude_hooks(settings, config["hooks"])
    return _write_json(CLAUDE_SETTINGS, merged)


def uninstall_claude() -> bool:
    settings = _read_json(CLAUDE_SETTINGS)
    if not settings:
        return True
    _backup_file(CLAUDE_SETTINGS)
    cleaned = _remove_claude_hooks(settings)
    return _write_json(CLAUDE_SETTINGS, cleaned)


def status_claude() -> dict:
    settings = _read_json(CLAUDE_SETTINGS)
    hooks = settings.get("hooks", {})
    found = {}
    for event, handlers in hooks.items():
        for group in handlers:
            for hook in group.get("hooks", []):
                if HOOK_MARKER in hook.get("name", ""):
                    found.setdefault(event, 0)
                    found[event] += 1
    return {"installed": bool(found), "events": found}


# ──────────────────────────────────────────────────────────────────────────────
# Codex CLI Integration
# ──────────────────────────────────────────────────────────────────────────────

def install_codex(hook_path: Path) -> bool:
    config = _read_toml(CODEX_CONFIG)
    _backup_file(CODEX_CONFIG)

    # Enable hooks feature
    config.setdefault("features", {})["codex_hooks"] = True

    # Add our hooks - hooks.Stop is a list of tables
    hooks_section = config.setdefault("hooks", {})

    # Stop hook - find or create the Stop table
    stop_tables = hooks_section.setdefault("Stop", [])
    # Filter out our old hooks from existing tables
    for table in stop_tables:
        hooks_list = table.get("hooks")
        if isinstance(hooks_list, list):
            table["hooks"] = [h for h in hooks_list if HOOK_MARKER not in h.get("command", "")]
    
    # Find a table to add to, or create new one
    target_table = None
    for table in stop_tables:
        hooks_list = table.get("hooks")
        if isinstance(hooks_list, list):
            target_table = table
            break
    if target_table is None:
        target_table = {"hooks": []}
        stop_tables.append(target_table)
    
    hooks_list = target_table.setdefault("hooks", [])
    if not isinstance(hooks_list, list):
        hooks_list = []
        target_table["hooks"] = hooks_list
    hooks_list.append({
        "type": "command",
        "command": f"{hook_path} codex Stop {HOOK_MARKER} {INSTALL_TAG}",
        "timeout": "10",
        "statusMessage": "Reporting to Rooks Companion"
    })

    # PermissionRequest hook
    perm_tables = hooks_section.setdefault("PermissionRequest", [])
    # Set matcher on first table
    if perm_tables and "matcher" not in perm_tables[0]:
        perm_tables[0]["matcher"] = "Bash"
    elif not perm_tables:
        perm_tables.append({"matcher": "Bash", "hooks": []})
    
    # Filter out our old hooks
    for table in perm_tables:
        hooks_list = table.get("hooks")
        if isinstance(hooks_list, list):
            table["hooks"] = [h for h in hooks_list if HOOK_MARKER not in h.get("command", "")]
    
    # Find a table with hooks to add to
    target_table = None
    for table in perm_tables:
        hooks_list = table.get("hooks")
        if isinstance(hooks_list, list):
            target_table = table
            break
    if target_table is None:
        target_table = {"hooks": []}
        perm_tables.append(target_table)
    
    hooks_list = target_table.setdefault("hooks", [])
    if not isinstance(hooks_list, list):
        hooks_list = []
        target_table["hooks"] = hooks_list
    hooks_list.append({
        "type": "command",
        "command": f"{hook_path} codex PermissionRequest {HOOK_MARKER} {INSTALL_TAG}",
        "timeout": "10",
        "statusMessage": "Asking Rooks Companion for permission"
    })

    return _write_toml(CODEX_CONFIG, config)


def uninstall_codex() -> bool:
    config = _read_toml(CODEX_CONFIG)
    if not config:
        return True
    _backup_file(CODEX_CONFIG)

    hooks_section = config.get("hooks", {})
    for event in ("Stop", "PermissionRequest"):
        if event in hooks_section:
            tables = hooks_section[event]
            for table in tables:
                hooks_list = table.get("hooks")
                if isinstance(hooks_list, list):
                    table["hooks"] = [h for h in hooks_list if HOOK_MARKER not in h.get("command", "")]
            # Remove tables that have no hooks left
            hooks_section[event] = [t for t in tables if not (isinstance(t.get("hooks"), list) and len(t["hooks"]) == 0)]
            if not hooks_section[event]:
                hooks_section.pop(event, None)

    return _write_toml(CODEX_CONFIG, config)


def status_codex() -> dict:
    config = _read_toml(CODEX_CONFIG)
    hooks_section = config.get("hooks", {})
    found = {}
    for event in ("Stop", "PermissionRequest"):
        tables = hooks_section.get(event, [])
        count = 0
        for table in tables:
            hooks_list = table.get("hooks")
            if isinstance(hooks_list, list):
                count += sum(1 for h in hooks_list if HOOK_MARKER in h.get("command", ""))
        if count:
            found[event] = count
    return {"installed": bool(found), "events": found, "features_codex_hooks": config.get("features", {}).get("codex_hooks") == True}


# ──────────────────────────────────────────────────────────────────────────────
# Gemini CLI Integration
# ──────────────────────────────────────────────────────────────────────────────

GEMINI_HOOKS_CONFIG = {
    "hooks": {
        "SessionStart": [{"matcher": "startup", "hooks": [{"name": "rooks-companion-session-start", "type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} gemini SessionStart", "timeout": 5000}]}],
        "BeforeAgent": [{"matcher": "*", "hooks": [{"name": "rooks-companion-before-agent", "type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} gemini BeforeAgent", "timeout": 5000}]}],
        "BeforeTool": [{"matcher": "*", "hooks": [{"name": "rooks-companion-before-tool", "type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} gemini BeforeTool", "timeout": 5000}]}],
        "AfterTool": [{"matcher": "*", "hooks": [{"name": "rooks-companion-after-tool", "type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} gemini AfterTool", "timeout": 5000}]}],
        "AfterAgent": [{"matcher": "*", "hooks": [{"name": "rooks-companion-after-agent", "type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} gemini AfterAgent", "timeout": 5000}]}],
        "Notification": [{"matcher": "*", "hooks": [{"name": "rooks-companion-notification", "type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} gemini Notification", "timeout": 5000}]}],
        "SessionEnd": [{"matcher": "exit", "hooks": [{"name": "rooks-companion-session-end", "type": "command", "command": f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME} gemini SessionEnd", "timeout": 5000}]}],
    }
}


def _merge_gemini_hooks(existing: dict, new_hooks: dict) -> dict:
    result = json.loads(json.dumps(existing))
    hooks = result.setdefault("hooks", {})
    for event, handlers in new_hooks.items():
        existing_handlers = hooks.get(event, [])
        filtered = [h for h in existing_handlers if HOOK_MARKER not in json.dumps(h)]
        for group in handlers:
            for hook in group.get("hooks", []):
                hook["name"] = f"{hook['name']}-{INSTALL_TAG}"
        filtered.extend(handlers)
        hooks[event] = filtered
    return result


def _remove_gemini_hooks(settings: dict) -> dict:
    result = json.loads(json.dumps(settings))
    hooks = result.get("hooks", {})
    for event, handlers in list(hooks.items()):
        filtered = [h for h in handlers if HOOK_MARKER not in json.dumps(h)]
        if filtered:
            hooks[event] = filtered
        else:
            hooks.pop(event, None)
    return result


def install_gemini(hook_path: Path) -> bool:
    settings = _read_json(GEMINI_SETTINGS)
    _backup_file(GEMINI_SETTINGS)
    config = json.loads(json.dumps(GEMINI_HOOKS_CONFIG))
    for event, groups in config["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                hook["command"] = hook["command"].replace(f"{ROOKS_HOOKS_DIR}/{HOOK_SCRIPT_NAME}", str(hook_path))
    merged = _merge_gemini_hooks(settings, config["hooks"])
    return _write_json(GEMINI_SETTINGS, merged)


def uninstall_gemini() -> bool:
    settings = _read_json(GEMINI_SETTINGS)
    if not settings:
        return True
    _backup_file(GEMINI_SETTINGS)
    cleaned = _remove_gemini_hooks(settings)
    return _write_json(GEMINI_SETTINGS, cleaned)


def status_gemini() -> dict:
    settings = _read_json(GEMINI_SETTINGS)
    hooks = settings.get("hooks", {})
    found = {}
    for event, handlers in hooks.items():
        for group in handlers:
            for hook in group.get("hooks", []):
                if HOOK_MARKER in hook.get("name", ""):
                    found.setdefault(event, 0)
                    found[event] += 1
    return {"installed": bool(found), "events": found}


# ──────────────────────────────────────────────────────────────────────────────
# Main Commands
# ──────────────────────────────────────────────────────────────────────────────

AGENTS = {
    "claude": (install_claude, uninstall_claude, status_claude, CLAUDE_SETTINGS),
    "codex": (install_codex, uninstall_codex, status_codex, CODEX_CONFIG),
    "gemini": (install_gemini, uninstall_gemini, status_gemini, GEMINI_SETTINGS),
}


def cmd_install(args: argparse.Namespace, hook_path: Path) -> int:
    agents = [args.agent] if args.agent != "all" else list(AGENTS.keys())
    ok = True
    for agent in agents:
        install_fn, _, _, _ = AGENTS[agent]
        if not install_fn(hook_path):
            print(f"✗ Failed to install for {agent}", file=sys.stderr)
            ok = False
        else:
            print(f"✓ Installed for {agent}")
    return 0 if ok else 1


def cmd_uninstall(args: argparse.Namespace) -> int:
    agents = [args.agent] if args.agent != "all" else list(AGENTS.keys())
    ok = True
    for agent in agents:
        _, uninstall_fn, _, _ = AGENTS[agent]
        if not uninstall_fn():
            print(f"✗ Failed to uninstall for {agent}", file=sys.stderr)
            ok = False
        else:
            print(f"✓ Uninstalled for {agent}")
    # Also remove the hook script if uninstalling all
    if args.agent == "all":
        _remove_hook_script()
        print("✓ Removed hook script")
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    agents = [args.agent] if args.agent != "all" else list(AGENTS.keys())
    for agent in agents:
        _, _, status_fn, config_path = AGENTS[agent]
        st = status_fn()
        marker = "✓" if st["installed"] else "✗"
        print(f"{marker} {agent}: {'installed' if st['installed'] else 'not installed'}")
        if st.get("events"):
            for evt, count in st["events"].items():
                print(f"    {evt}: {count} hook(s)")
        if agent == "codex" and st.get("features_codex_hooks"):
            print(f"    features.codex_hooks: enabled")
        print(f"    config: {config_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rooks Companion Hook Installer")
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd in ("install", "uninstall", "status"):
        p = sub.add_parser(cmd)
        p.add_argument("--agent", choices=["claude", "codex", "gemini", "all"], default="all")
        p.add_argument("--home", type=Path, help="Override home directory (for testing)")

    args = parser.parse_args()

    # Override home for testing
    if args.home:
        os.environ["HOME"] = str(args.home)
        # Recompute paths
        global ROOKS_HOOKS_DIR, CLAUDE_SETTINGS, CODEX_CONFIG, GEMINI_SETTINGS
        ROOKS_HOOKS_DIR = args.home / ".rooks" / "hooks"
        CLAUDE_SETTINGS = args.home / ".claude" / "settings.json"
        CODEX_CONFIG = args.home / ".codex" / "config.toml"
        GEMINI_SETTINGS = args.home / ".gemini" / "settings.json"

    # Source hook script is next to this file
    source_dir = Path(__file__).parent
    hook_path = _deploy_hook_script(source_dir)

    if args.command == "install":
        if not hook_path:
            print("✗ Hook script not found", file=sys.stderr)
            return 1
        return cmd_install(args, hook_path)
    elif args.command == "uninstall":
        return cmd_uninstall(args)
    elif args.command == "status":
        return cmd_status(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())