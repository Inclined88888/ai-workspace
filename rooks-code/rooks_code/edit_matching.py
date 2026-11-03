"""Layered fuzzy matching for edit_file tool.

Implements a multi-tier fallback matching strategy:
1. Exact match (current behavior)
2. Whitespace-insensitive (normalize spaces, tabs, unicode quotes/dashes)
3. Indentation-flexible (match lines ignoring leading whitespace differences)
4. Line-trimmed (match stripped lines)
5. Fuzzy/Levenshtein (allow small edit distance, up to 10% of content)

On failure, generates a helpful hint showing the closest matching lines.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


# Unicode normalization maps
UNICODE_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
}
UNICODE_DASHES = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
}
UNICODE_SPACES = {
    "\u00A0": " ", "\u2002": " ", "\u2003": " ", "\u2004": " ",
    "\u2005": " ", "\u2006": " ", "\u2007": " ", "\u2008": " ",
    "\u2009": " ", "\u200A": " ", "\u202F": " ", "\u205F": " ", "\u3000": " ",
}

CRLF = "\r\n"


def normalize_for_match(text: str) -> str:
    """Normalize unicode quotes, dashes, and spaces for matching."""
    for k, v in UNICODE_QUOTES.items():
        text = text.replace(k, v)
    for k, v in UNICODE_DASHES.items():
        text = text.replace(k, v)
    for k, v in UNICODE_SPACES.items():
        text = text.replace(k, v)
    return text


def line_ending(text: str, fallback: str = "\n") -> str:
    """Detect dominant line ending in text."""
    crlf_count = text.count(CRLF)
    lf_count = text.count("\n") - crlf_count
    if crlf_count == lf_count:
        return fallback
    return CRLF if crlf_count > lf_count else "\n"


@dataclass
class Match:
    """A match region in the source text."""
    start: int
    end: int


@dataclass
class EditMatchResult:
    """Result of an edit match attempt."""
    matched: bool
    matches: List[Match]
    strategy: str  # "exact", "whitespace", "indentation", "line_trimmed", "fuzzy"
    new_content: str = ""
    error_hint: str = ""


def find_exact_occurrences(source: str, search: str) -> List[Match]:
    """Find all exact occurrences of search in source."""
    result = []
    offset = 0
    while True:
        idx = source.find(search, offset)
        if idx == -1:
            break
        result.append(Match(idx, idx + len(search)))
        offset = idx + len(search)
    return result


def find_whitespace_insensitive_occurrences(source: str, search: str) -> List[Match]:
    """Find occurrences with unicode normalization (quotes, dashes, spaces)."""
    norm_source = normalize_for_match(source)
    norm_search = normalize_for_match(search)
    matches = []
    offset = 0
    while True:
        idx = norm_source.find(norm_search, offset)
        if idx == -1:
            break
        # Map back to original source indices (approximate since normalization may change length)
        # For simplicity, we use the normalized indices directly
        matches.append(Match(idx, idx + len(norm_search)))
        offset = idx + len(norm_search)
    return matches


def split_lines_keep_ending(text: str) -> List[str]:
    """Split text into lines, keeping line endings."""
    return text.splitlines(keepends=True)


def lines_match_flexible(source_lines: List[str], search_lines: List[str], start_idx: int) -> bool:
    """Check if source_lines[start_idx:start_idx+len(search_lines)] matches search_lines
    ignoring trailing whitespace on each line."""
    if start_idx + len(search_lines) > len(source_lines):
        return False
    for i, search_line in enumerate(search_lines):
        src_line = source_lines[start_idx + i]
        if src_line.rstrip() != search_line.rstrip():
            return False
    return True


def find_line_trimmed_occurrences(source: str, search: str) -> List[Match]:
    """Find occurrences matching line-by-line with trailing whitespace ignored."""
    source_lines = split_lines_keep_ending(source)
    search_lines = split_lines_keep_ending(search)
    # Remove trailing empty line from splitlines(keepends=True) artifact
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if source_lines and source_lines[-1] == "":
        source_lines.pop()

    matches = []
    for i in range(len(source_lines) - len(search_lines) + 1):
        if lines_match_flexible(source_lines, search_lines, i):
            # Calculate byte offsets
            start = sum(len(l) for l in source_lines[:i])
            end = sum(len(l) for l in source_lines[:i + len(search_lines)])
            matches.append(Match(start, end))
    return matches


def find_indentation_flexible_occurrences(source: str, search: str) -> List[Match]:
    """Find occurrences matching line-by-line with leading whitespace normalized."""
    source_lines = split_lines_keep_ending(source)
    search_lines = split_lines_keep_ending(search)
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if source_lines and source_lines[-1] == "":
        source_lines.pop()

    matches = []
    for i in range(len(source_lines) - len(search_lines) + 1):
        match = True
        for j, search_line in enumerate(search_lines):
            src_line = source_lines[i + j]
            # Compare with leading whitespace stripped
            if src_line.lstrip() != search_line.lstrip():
                match = False
                break
        if match:
            start = sum(len(l) for l in source_lines[:i])
            end = sum(len(l) for l in source_lines[:i + len(search_lines)])
            matches.append(Match(start, end))
    return matches


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def find_fuzzy_occurrences(source: str, search: str, threshold: float = 0.1) -> List[Match]:
    """Find occurrences using fuzzy matching (Levenshtein distance <= threshold * len(search))."""
    max_dist = int(len(search) * threshold)
    if max_dist == 0:
        return []

    source_lines = split_lines_keep_ending(source)
    search_lines = split_lines_keep_ending(search)
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if source_lines and source_lines[-1] == "":
        source_lines.pop()

    search_text = "".join(search_lines)
    matches = []

    # Sliding window over source lines
    window_size = len(search_lines)
    for i in range(len(source_lines) - window_size + 1):
        window_text = "".join(source_lines[i:i + window_size])
        dist = levenshtein_distance(window_text, search_text)
        if dist <= max_dist:
            start = sum(len(l) for l in source_lines[:i])
            end = sum(len(l) for l in source_lines[:i + window_size])
            matches.append(Match(start, end))

    return matches


def apply_replacement(source: str, matches: List[Match], replacement: str, replace_all: bool) -> str:
    """Apply replacement to matched regions (in reverse order to preserve indices)."""
    if not matches:
        return source

    to_replace = matches if replace_all else [matches[0]]
    result = source
    for match in reversed(to_replace):
        result = result[:match.start] + replacement + result[match.end:]
    return result


def generate_miss_hint(source: str, search: str, replacement: str, max_hint_lines: int = 12) -> str:
    """Generate a helpful hint when old_string is not found.

    Shows the closest matching lines from the file, or notes if new_string is already present.
    """
    source_view = source.replace(CRLF, "\n")
    replacement_view = replacement.replace(CRLF, "\n")

    # Check if replacement is already in the file
    if len(replacement_view.strip()) >= 20:
        norm_source = normalize_for_match(source_view)
        norm_replacement = normalize_for_match(replacement_view)
        if norm_replacement in norm_source:
            return "newString is already in the file, so this edit may already be applied."

    # Whitespace-insensitive line matching to find closest location
    def line_key(line: str) -> str:
        return normalize_for_match(line).strip()

    def is_distinct(line: str) -> bool:
        # Check if line contains at least one letter or digit (Unicode-aware alternative)
        return bool(re.search(r"[A-Za-z0-9]", line))

    source_lines = source_view.split("\n")
    search_lines = search.replace(CRLF, "\n").split("\n")

    # Build index of distinct normalized lines
    positions = {}
    for idx, line in enumerate(source_lines):
        key = line_key(line)
        if not is_distinct(key):
            continue
        positions.setdefault(key, []).append(idx)

    # Vote for start position based on matching lines
    votes = {}
    wanted = 0
    for offset, search_line in enumerate(search_lines):
        key = line_key(search_line)
        if not is_distinct(key):
            continue
        wanted += 1
        for idx in positions.get(key, []):
            start_pos = idx - offset
            votes[start_pos] = votes.get(start_pos, 0) + 1

    if not votes or wanted == 0:
        return ""

    # Find best voted position
    best_start, best_count = max(votes.items(), key=lambda x: x[1])
    if best_count * 2 < wanted:
        return ""

    # Verify matched lines
    matched_indices = []
    for offset, search_line in enumerate(search_lines):
        idx = best_start + offset
        key = line_key(search_line)
        if key and 0 <= idx < len(source_lines) and line_key(source_lines[idx]) == key:
            matched_indices.append(idx)

    if not matched_indices:
        return ""

    from_idx = matched_indices[0]
    to_idx = min(matched_indices[-1] + 1, from_idx + max_hint_lines)

    hint_lines = ["The closest lines in the file:"]
    for i in range(from_idx, to_idx):
        line = source_lines[i]
        if len(line) > 200:
            line = line[:200] + "…"
        hint_lines.append(f"{i + 1}: {line}")

    return "\n".join(hint_lines)


def calculate_edit_match(
    source: str,
    search: str,
    replacement: str,
    replace_all: bool = False,
    fuzzy_threshold: float = 0.1,
) -> EditMatchResult:
    """Main entry point: try all matching strategies in order.

    Returns EditMatchResult with the matched content and strategy used.
    """
    if not search:
        return EditMatchResult(
            matched=False,
            matches=[],
            strategy="empty_search",
            error_hint="old_text must not be empty",
        )

    # Normalize line endings for matching
    file_ending = line_ending(source)
    search_norm = search.replace(CRLF, "\n")
    replacement_norm = replacement.replace(CRLF, "\n")

    # Strategy 1: Exact match
    exact_matches = find_exact_occurrences(source, search_norm)
    if exact_matches:
        new_content = apply_replacement(source, exact_matches, replacement_norm, replace_all)
        return EditMatchResult(
            matched=True,
            matches=exact_matches,
            strategy="exact",
            new_content=new_content,
        )

    # Strategy 2: Whitespace/Unicode normalization
    ws_matches = find_whitespace_insensitive_occurrences(source, search_norm)
    if ws_matches:
        new_content = apply_replacement(source, ws_matches, replacement_norm, replace_all)
        return EditMatchResult(
            matched=True,
            matches=ws_matches,
            strategy="whitespace",
            new_content=new_content,
        )

    # Strategy 3: Indentation flexible
    indent_matches = find_indentation_flexible_occurrences(source, search_norm)
    if indent_matches:
        new_content = apply_replacement(source, indent_matches, replacement_norm, replace_all)
        return EditMatchResult(
            matched=True,
            matches=indent_matches,
            strategy="indentation",
            new_content=new_content,
        )

    # Strategy 4: Line-trimmed (trailing whitespace ignored)
    trimmed_matches = find_line_trimmed_occurrences(source, search_norm)
    if trimmed_matches:
        new_content = apply_replacement(source, trimmed_matches, replacement_norm, replace_all)
        return EditMatchResult(
            matched=True,
            matches=trimmed_matches,
            strategy="line_trimmed",
            new_content=new_content,
        )

    # Strategy 5: Fuzzy (Levenshtein)
    fuzzy_matches = find_fuzzy_occurrences(source, search_norm, fuzzy_threshold)
    if fuzzy_matches:
        new_content = apply_replacement(source, fuzzy_matches, replacement_norm, replace_all)
        return EditMatchResult(
            matched=True,
            matches=fuzzy_matches,
            strategy="fuzzy",
            new_content=new_content,
        )

    # No match found - generate hint
    hint = generate_miss_hint(source, search_norm, replacement_norm)
    error_msg = f"old_text was not found in the file"
    if hint:
        error_msg += f";\n\n{hint}"
    error_msg += ". Read the file and copy the text exactly, or use more surrounding context."

    return EditMatchResult(
        matched=False,
        matches=[],
        strategy="none",
        error_hint=error_msg,
    )