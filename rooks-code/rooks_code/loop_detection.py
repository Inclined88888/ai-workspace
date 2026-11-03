"""Loop/Doom Detection for Rooks Code.

Detots three classes of unproductive loops:
1. Consecutive identical tool calls (same tool, same args) — gemini-cli style
2. Content chanting (repeating text chunks) — gemini-cli style
3. Many reads without progress (opencode doom loop style)

When a loop is detected, the agent stops and asks the user for guidance.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Tuple


# Configuration constants (tunable)
TOOL_CALL_LOOP_THRESHOLD = 5          # Repeated identical calls before flagging
TOOL_CALL_MAX_HISTORY = 50            # Max tool call keys to remember
TOOL_CALL_CYCLE_MAX_LEN = 5           # Max cycle length to detect (1-5)

CONTENT_CHUNK_SIZE = 50               # Chunk size for content loop detection
CONTENT_LOOP_THRESHOLD = 10           # Repeated chunks before flagging
CONTENT_MAX_HISTORY = 5000            # Max chars of streaming content to track
CONTENT_MAX_DISTANCE_FACTOR = 5       # Max average distance between repetitions (× chunk size)

READ_HEAVY_THRESHOLD = 15             # Read tools in a row without write/command
READ_HEAVY_WINDOW = 20                # Window to count reads in


@dataclass
class LoopDetectionResult:
    """Result of a loop detection check."""
    detected: bool = False
    loop_type: str = ""               # "tool_call", "content", "read_heavy"
    detail: str = ""
    count: int = 0                    # How many times this loop was detected


@dataclass
class LoopDetector:
    """Tracks tool calls and streaming content to detect unproductive loops."""

    # Tool call tracking
    _tool_call_history: Deque[str] = field(default_factory=deque)
    _tool_call_counts: Dict[str, int] = field(default_factory=dict)

    # Content streaming tracking
    _content_history: str = ""
    _content_chunk_hashes: Dict[str, List[int]] = field(default_factory=dict)
    _content_last_index: int = 0
    _in_code_block: bool = False

    # Read-heavy tracking
    _recent_tools: Deque[str] = field(default_factory=deque)

    # State
    _loop_detected: bool = False
    _detected_count: int = 0
    _last_loop_type: str = ""
    _last_loop_detail: str = ""

    # Session control
    _disabled: bool = False

    def disable(self) -> None:
        """Disable loop detection for this session."""
        self._disabled = True

    def reset(self) -> None:
        """Reset all tracking state (new prompt)."""
        self._tool_call_history.clear()
        self._tool_call_counts.clear()
        self._content_history = ""
        self._content_chunk_hashes.clear()
        self._content_last_index = 0
        self._in_code_block = False
        self._recent_tools.clear()
        self._loop_detected = False
        self._detected_count = 0
        self._last_loop_type = ""
        self._last_loop_detail = ""

    def _tool_call_key(self, tool_name: str, args: dict) -> str:
        """Stable hash of tool name + arguments for comparison."""
        args_json = json.dumps(args, sort_keys=True, separators=(",", ":"))
        key_string = f"{tool_name}:{args_json}"
        return hashlib.sha256(key_string.encode()).hexdigest()[:16]

    def _is_read_tool(self, tool_name: str) -> bool:
        """Check if a tool is a read-only operation."""
        return tool_name in ("read_file", "list_files", "search", "web_search", "web_fetch")

    def _is_write_tool(self, tool_name: str) -> bool:
        """Check if a tool modifies state."""
        return tool_name in ("edit_file", "write_file", "run_command", "start_background", "stop_background")

    # ──────────────────────────────────────────────────────────────
    # Tool call loop detection (gemini-cli style)
    # ──────────────────────────────────────────────────────────────

    def on_tool_call(self, tool_name: str, args: dict) -> LoopDetectionResult:
        """Process a tool call and check for loops."""
        if self._disabled or self._loop_detected:
            return LoopDetectionResult()

        key = self._tool_call_key(tool_name, args)
        self._tool_call_history.append(key)
        self._tool_call_counts[key] = self._tool_call_counts.get(key, 0) + 1

        # Trim history
        max_len = TOOL_CALL_MAX_HISTORY
        if len(self._tool_call_history) > max_len:
            old = self._tool_call_history.popleft()
            self._tool_call_counts[old] -= 1
            if self._tool_call_counts[old] == 0:
                del self._tool_call_counts[old]

        # Track recent tools for read-heavy detection
        self._recent_tools.append(tool_name)
        if len(self._recent_tools) > READ_HEAVY_WINDOW:
            self._recent_tools.popleft()

        # Check for consecutive identical tool calls (cycles of length 1-5)
        is_loop, detail = self._check_tool_call_cycles()
        if is_loop:
            self._loop_detected = True
            self._detected_count += 1
            self._last_loop_type = "tool_call"
            self._last_loop_detail = detail
            return LoopDetectionResult(
                detected=True,
                loop_type="tool_call",
                detail=detail,
                count=self._detected_count,
            )

        # Check for read-heavy pattern (many reads without progress)
        is_loop, detail = self._check_read_heavy()
        if is_loop:
            # Exploring a codebase legitimately reads a lot, so this only nudges the model (once per window); a stop
            # is kept for true repetition (identical calls in a cycle) above.
            self._recent_tools.clear()
            return LoopDetectionResult(detected=True, loop_type="read_heavy", detail=detail, count=self._detected_count)
        if False:
            self._loop_detected = True
            self._detected_count += 1
            self._last_loop_type = "read_heavy"
            self._last_loop_detail = detail
            return LoopDetectionResult(
                detected=True,
                loop_type="read_heavy",
                detail=detail,
                count=self._detected_count,
            )

        return LoopDetectionResult()

    def _check_tool_call_cycles(self) -> Tuple[bool, str]:
        """Detect repeating cycles of tool calls (length 1-5, repeated 5×)."""
        n = len(self._tool_call_history)
        if n < TOOL_CALL_LOOP_THRESHOLD:
            return False, ""

        # Check cycles of length k=1 to 5
        for k in range(1, TOOL_CALL_CYCLE_MAX_LEN + 1):
            required = k * TOOL_CALL_LOOP_THRESHOLD
            if n < required:
                continue

            cycle = list(self._tool_call_history)[-k:]
            is_pattern = True

            for i in range(required):
                idx = n - required + i
                if self._tool_call_history[idx] != cycle[i % k]:
                    is_pattern = False
                    break

            if is_pattern:
                # Format the cycle for user-friendly message
                cycle_strs = []
                for ck in cycle:
                    # Find a sample call with this key
                    for name, args in self._recent_tool_calls_with_key(ck):
                        args_str = json.dumps(args)[:100]
                        cycle_strs.append(f"{name}({args_str})")
                        break
                return True, f"Repeating cycle of {k} tool call(s): {' → '.join(cycle_strs)}"

        return False, ""

    def _recent_tool_calls_with_key(self, key: str) -> List[Tuple[str, dict]]:
        """Find recent tool calls matching a key (for display)."""
        # This is a best-effort reverse lookup; in practice we'd store the mapping
        # For now return empty — the key itself is shown
        return []

    def _check_read_heavy(self) -> Tuple[bool, str]:
        """Detect many read tools without write/command progress (opencode doom loop)."""
        if len(self._recent_tools) < READ_HEAVY_WINDOW:
            return False, ""

        reads = sum(1 for t in self._recent_tools if self._is_read_tool(t))
        writes = sum(1 for t in self._recent_tools if self._is_write_tool(t))

        if reads >= READ_HEAVY_THRESHOLD and writes == 0:
            return True, f"{reads} read tool(s) in last {READ_HEAVY_WINDOW} calls with no writes or commands"

        return False, ""

    # ──────────────────────────────────────────────────────────────
    # Content chanting detection (gemini-cli style)
    # ──────────────────────────────────────────────────────────────

    def on_content_chunk(self, chunk: str) -> LoopDetectionResult:
        """Process streaming content chunk and check for chanting loops."""
        if self._disabled or self._loop_detected:
            return LoopDetectionResult()

        # Detect code block boundaries (``` fences) — disable inside code blocks
        fence_count = chunk.count("```")
        if fence_count > 0:
            self._in_code_block = not self._in_code_block

        # Reset on structural content elements (tables, lists, headings)
        if self._has_structural_elements(chunk):
            self._reset_content_tracking(reset_history=False)

        if self._in_code_block:
            return LoopDetectionResult()

        self._content_history += chunk
        self._truncate_content_history()
        return self._analyze_content_chunks()

    def _has_structural_elements(self, text: str) -> bool:
        """Check for markdown structures that reset content tracking."""
        return bool(re.search(r"(^|\n)\s*(\|.*\||\|[-|+]{3,})", text)) or \
               bool(re.search(r"(^|\n)\s*[-*+]\s", text)) or \
               bool(re.search(r"(^|\n)\s*\d+\.\s", text)) or \
               bool(re.search(r"(^|\n)#+\\s", text)) or \
               bool(re.search(r"(^|\n)>\s", text)) or \
               bool(re.match(r"^[\+\-_=\*\u2500-\u257F]+$", text.strip()))

    def _reset_content_tracking(self, reset_history: bool = True) -> None:
        if reset_history:
            self._content_history = ""
        self._content_chunk_hashes.clear()
        self._content_last_index = 0

    def _truncate_content_history(self) -> None:
        if len(self._content_history) <= CONTENT_MAX_HISTORY:
            return

        truncate_amount = len(self._content_history) - CONTENT_MAX_HISTORY
        self._content_history = self._content_history[truncate_amount:]
        self._content_last_index = max(0, self._content_last_index - truncate_amount)

        # Adjust stored indices
        for hash_key in list(self._content_chunk_hashes.keys()):
            adjusted = [idx - truncate_amount for idx in self._content_chunk_hashes[hash_key] if idx >= truncate_amount]
            if adjusted:
                self._content_chunk_hashes[hash_key] = adjusted
            else:
                del self._content_chunk_hashes[hash_key]

    def _analyze_content_chunks(self) -> LoopDetectionResult:
        """Sliding window chunk analysis for repetitive content."""
        while self._content_last_index + CONTENT_CHUNK_SIZE <= len(self._content_history):
            chunk = self._content_history[
                self._content_last_index:self._content_last_index + CONTENT_CHUNK_SIZE
            ]
            chunk_hash = hashlib.sha256(chunk.encode()).hexdigest()

            if self._is_loop_for_chunk(chunk, chunk_hash):
                self._loop_detected = True
                self._detected_count += 1
                self._last_loop_type = "content"
                self._last_loop_detail = f"Repeating content chunk: {chunk[:50]!r}..."
                return LoopDetectionResult(
                    detected=True,
                    loop_type="content",
                    detail=self._last_loop_detail,
                    count=self._detected_count,
                )

            self._content_last_index += 1

        return LoopDetectionResult()

    def _is_loop_for_chunk(self, chunk: str, chunk_hash: str) -> bool:
        """Check if a chunk indicates a chanting loop."""
        existing = self._content_chunk_hashes.get(chunk_hash)
        if not existing:
            self._content_chunk_hashes[chunk_hash] = [self._content_last_index]
            return False

        # Verify actual content match (hash collision safety)
        original_idx = existing[0]
        original_chunk = self._content_history[
            original_idx:original_idx + CONTENT_CHUNK_SIZE
        ]
        if original_chunk != chunk:
            return False

        existing.append(self._content_last_index)

        if len(existing) < CONTENT_LOOP_THRESHOLD:
            return False

        # Check recent occurrences are tightly clustered
        recent = existing[-CONTENT_LOOP_THRESHOLD:]
        total_dist = recent[-1] - recent[0]
        avg_dist = total_dist / (CONTENT_LOOP_THRESHOLD - 1)
        max_allowed = CONTENT_CHUNK_SIZE * CONTENT_MAX_DISTANCE_FACTOR

        if avg_dist > max_allowed:
            return False

        # Verify periods between repetitions are mostly identical
        periods = set()
        for i in range(len(recent) - 1):
            period = self._content_history[recent[i]:recent[i + 1]]
            periods.add(period)

        # True loop: few unique periods (1-2). List with shared prefix: many unique periods.
        if len(periods) > CONTENT_LOOP_THRESHOLD // 2:
            return False

        return True

    # ──────────────────────────────────────────────────────────────
    # LLM-based semantic loop check (placeholder for future)
    # ──────────────────────────────────────────────────────────────

    async def check_with_llm(
        self,
        recent_messages: List[dict],
        user_prompt: str,
        model_client,
    ) -> LoopDetectionResult:
        """
        Ask an LLM to classify if the conversation is stuck.
        Placeholder — integrates with a fast/cheap model when available.
        """
        # TODO: Implement LLM-based semantic loop detection
        # Similar to gemini-cli's checkForLoopWithLLM
        return LoopDetectionResult()

    # ──────────────────────────────────────────────────────────────
    # State access
    # ──────────────────────────────────────────────────────────────

    def is_loop_detected(self) -> bool:
        return self._loop_detected

    def clear_detection(self) -> None:
        """Allow one recovery turn after a loop was detected."""
        self._loop_detected = False

    def get_status(self) -> dict:
        return {
            "loop_detected": self._loop_detected,
            "detected_count": self._detected_count,
            "last_loop_type": self._last_loop_type,
            "last_loop_detail": self._last_loop_detail,
            "tool_call_history_len": len(self._tool_call_history),
            "content_history_len": len(self._content_history),
        }