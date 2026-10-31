"""Conversation compression: auto-summarize old context to fit token limits.

Inspired by Hermes Agent's approach, simplified for ai-workspace's architecture.
Runs as a background task when conversation exceeds threshold.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import db, llm

logger = logging.getLogger(__name__)

# Compression thresholds
DEFAULT_MAX_TOKENS = 8000  # Trigger compression when conversation exceeds this
DEFAULT_TARGET_TOKENS = 4000  # Compress down to this
DEFAULT_KEEP_RECENT = 10  # Always keep this many recent messages

# Compression markers
COMPRESSION_MARKER = "[Earlier conversation summarized]"
COMPRESSION_STATUS = "🗜️ Compacting context..."


def _text(content: Any) -> str:
    """Message content as plain text: strings pass through, multimodal parts keep only their text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return "" if content is None else str(content)


@dataclass
class CompressionResult:
    """Result of a compression pass."""
    original_tokens: int
    compressed_tokens: int
    messages_removed: int
    summary: str
    timestamp: float


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """Rough token estimate: 4 chars ≈ 1 token."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text", "")) // 4
    return total


def should_compress(messages: List[Dict[str, Any]], max_tokens: int = DEFAULT_MAX_TOKENS) -> bool:
    """Check if conversation needs compression."""
    return estimate_tokens(messages) > max_tokens


def compress_conversation(
    messages: List[Dict[str, Any]],
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    keep_recent: int = DEFAULT_KEEP_RECENT,
    summary_fn: Optional[Callable] = None,
) -> tuple[List[Dict[str, Any]], Optional[CompressionResult]]:
    """
    Compress conversation by summarizing old messages.
    
    Strategy:
    1. Keep system message + recent N messages
    2. Summarize everything in between
    3. Return new message list with summary injected
    
    Returns (new_messages, compression_result) or (original_messages, None) if no compression needed.
    """
    if not should_compress(messages):
        return messages, None
    
    original_tokens = estimate_tokens(messages)
    
    # Split: system + old + recent
    system_msgs = [m for m in messages if m.get("role") == "system"]
    other_msgs = [m for m in messages if m.get("role") != "system"]
    
    if len(other_msgs) <= keep_recent:
        return messages, None  # Not enough to compress
    
    old_msgs = other_msgs[:-keep_recent]
    recent_msgs = other_msgs[-keep_recent:]
    
    # Generate summary
    if summary_fn is None:
        summary_fn = _default_summarize
    
    summary = summary_fn(old_msgs)
    
    # Build new message list
    summary_msg = {
        "role": "system",
        "content": f"{COMPRESSION_MARKER}\n\n{summary}",
        "_compressed": True,
        "_original_count": len(old_msgs),
    }
    
    new_messages = system_msgs + [summary_msg] + recent_msgs
    new_tokens = estimate_tokens(new_messages)
    
    result = CompressionResult(
        original_tokens=original_tokens,
        compressed_tokens=new_tokens,
        messages_removed=len(old_msgs),
        summary=summary,
        timestamp=time.time(),
    )
    
    logger.info(
        "Compressed conversation: %d -> %d tokens (%d messages removed)",
        original_tokens, new_tokens, len(old_msgs)
    )
    
    return new_messages, result


def _default_summarize(messages: List[Dict[str, Any]]) -> str:
    """Default summarization using the configured model."""
    # Build a prompt for summarization
    text = "\n\n".join(
        f"{m.get('role', 'unknown')}: {_text(m.get('content'))[:500]}"
        for m in messages[-20:]  # Last 20 old messages
    )
    
    prompt = f"""Summarize this conversation history concisely. Focus on:
1. Key facts and decisions
2. User preferences mentioned
3. Tasks completed or in progress
4. Important context for future turns

Conversation:
{text}

Summary (be concise, 2-3 paragraphs max):"""

    try:
        # Use the default model for summarization
        # llm.complete is async; this path is sync (callers run it from worker threads), so drive it here.
        # Inside a running event loop asyncio.run raises, and we fall back to truncation below.
        summary = asyncio.run(llm.complete(llm.default_model_ref(), [{"role": "user", "content": prompt}], {"channel": "compression"}))
        return (summary or "").strip() or "Summary unavailable"
    except Exception as e:
        logger.warning("Summarization failed: %s", e)
        # Fallback: simple truncation
        return f"[{len(messages)} messages removed. Last topic: {_text(messages[-1].get('content'))[:100] if messages else 'unknown'}...]"


def compress_if_needed(
    conversation_id: int,
    messages: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], bool]:
    """
    Check and compress a conversation if needed.
    Returns (messages, was_compressed).
    """
    if not should_compress(messages):
        return messages, False
    
    new_msgs, result = compress_conversation(messages)
    if result:
        # Store compression record
        db.run(
            "INSERT INTO compressions(conversation_id, original_tokens, compressed_tokens, messages_removed, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            conversation_id, result.original_tokens, result.compressed_tokens,
            result.messages_removed, db.now(),
        )
        return new_msgs, True
    
    return messages, False


# Database migration for compression tracking
def init_compression_table():
    """Create compression tracking table if not exists."""
    db.run("""
        CREATE TABLE IF NOT EXISTS compressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            original_tokens INTEGER,
            compressed_tokens INTEGER,
            messages_removed INTEGER,
            created_at TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
    """)
