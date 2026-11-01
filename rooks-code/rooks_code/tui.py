"""The terminal UI: status bar, activity line, streaming markdown, input with history and completion.

Pure standard library (the project's rule: rooks runs with nothing to install). When the optional [tui] extra is
present (rich, prompt_toolkit) it is used for markdown rendering and input editing; plain mode is the fallback and
what pipes and --no-color get. The pieces with logic (status bar text, activity text, paste collapsing, completion
candidates) are plain functions so they are unit tested without a terminal.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

# --- status bar ---------------------------------------------------------------------------------------------------


def status_bar(provider: str, model: str, tokens_in: int, tokens_out: int, cost: str, approval: str,
               git_branch: str, elapsed_s: float, context_used_pct: float | None = None, width: int = 100) -> str:
    """One line: provider/model · context % · tokens · cost · approval mode · git branch · elapsed."""
    left = f" {provider}/{model}"
    if context_used_pct is not None:
        left += f" · {context_used_pct:.0f}% ctx"
    mid = f" {tokens_in + tokens_out:,} tok"
    if cost:
        mid += f" · {cost}"
    right = ""
    if approval:
        right += f" {approval}"
    if git_branch:
        right += f" · {git_branch}"
    right += f" · {_elapsed(elapsed_s)} "
    gap = max(1, width - len(left) - len(mid) - len(right))
    return (left + mid + " " * gap + right)[:width]


def _elapsed(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s}s"


def git_branch(root: Path) -> str:
    try:
        head = (root / ".git" / "HEAD").read_text().strip()
        return head.removeprefix("ref: refs/heads/") if head.startswith("ref:") else head[:8]
    except OSError:
        return ""


# --- activity line ------------------------------------------------------------------------------------------------

_TOOLS = {
    "read_file": ("reading", lambda a: str(a.get("path", ""))),
    "list_files": ("listing", lambda a: str(a.get("path", "."))),
    "search": ("searching", lambda a: repr(a.get("pattern", ""))),
    "edit_file": ("editing", lambda a: str(a.get("path", ""))),
    "write_file": ("writing", lambda a: str(a.get("path", ""))),
    "run_command": ("running", lambda a: str(a.get("command", "")).splitlines()[0][:40]),
}


def activity(tool: str, args: dict, elapsed_s: float = 0.0, detail: str = "") -> str:
    """The line under the spinner: 'reading tools.py', \"searching 'foo' (1,240 files)\", 'running pytest 12s'."""
    verb, pick = _TOOLS.get(tool, (tool, lambda a: ""))
    target = pick(args)
    out = f"{verb} {target}".rstrip()
    if detail:
        out += f" ({detail})"
    if elapsed_s >= 1.0:
        out += f" {_elapsed(elapsed_s)}"
    return out


# --- paste collapsing ----------------------------------------------------------------------------------------------


def collapse_paste(text: str, threshold: int = 3) -> str:
    """A pasted block of more than `threshold` lines shows as [pasted N lines] in the input, not the whole block."""
    n = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    if n > threshold or len(text) > 200:
        return f"[pasted {n} lines]"
    return text


# --- completion ----------------------------------------------------------------------------------------------------

SLASH_COMMANDS = ("/undo", "/cost", "/dry-run", "/yes", "/model", "/receipt", "/clear", "/exit", "/help")


def completions(prefix: str, root: Path, limit: int = 20) -> list[str]:
    """Candidates for the text at the cursor: slash commands, then @file paths, then files at the root."""
    if prefix.startswith("/"):
        return [c for c in SLASH_COMMANDS if c.startswith(prefix)][:limit]
    if prefix.startswith("@"):
        return ["@" + p for p in _files(root, prefix[1:], limit)]
    return []


def _files(root: Path, prefix: str, limit: int) -> list[str]:
    from .tools import SKIP_DIRS
    out = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                rel = (Path(dirpath) / f).relative_to(root).as_posix()
                if rel.startswith(prefix):
                    out.append(rel)
                    if len(out) >= limit:
                        return out
    except OSError:
        pass
    return out


# --- streaming markdown --------------------------------------------------------------------------------------------

_CODE_FENCE = re.compile(r"^```(\w*)\s*$")


def render_markdown_line(line: str, in_code: bool, color) -> tuple[str, bool]:
    """One line of streamed markdown -> (terminal text, still in a code block). Inline, no renderer: headers go bold,
    code fences toggle a dim block, everything else passes through. `color` is UI.c or a no-op."""
    if _CODE_FENCE.match(line.strip()):
        return color("2", line), not in_code
    if in_code:
        return color("2", "  " + line), True
    if line.startswith("#"):
        return color("1", line.lstrip("# ")), False
    return line, False
