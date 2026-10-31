"""Turn recovery: auto-retry failed model calls with intelligent backoff.

Inspired by Hermes Agent's turn_recovery.py, simplified for ai-workspace.
Classifies errors and applies specific recovery strategies.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from . import db, llm

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """Classification of API errors."""
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    CONTENT_POLICY = "content_policy"
    CONTEXT_LENGTH = "context_length"
    SERVER_ERROR = "server_error"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass
class RecoveryAction:
    """Action to take for recovery."""
    retry: bool
    delay_seconds: float
    modify_messages: Optional[Callable] = None
    switch_provider: bool = False
    reduce_context: bool = False
    strip_images: bool = False


class RecoveryStrategy:
    """Base class for recovery strategies."""
    
    def can_handle(self, error: Exception) -> bool:
        raise NotImplementedError
    
    def recover(self, error: Exception, context: Dict[str, Any]) -> RecoveryAction:
        raise NotImplementedError


class RateLimitRecovery(RecoveryStrategy):
    """Handle rate limit errors with exponential backoff."""
    
    def can_handle(self, error: Exception) -> bool:
        msg = str(error).lower()
        return any(k in msg for k in ["rate limit", "429", "too many requests", "quota"])
    
    def recover(self, error: Exception, context: Dict[str, Any]) -> RecoveryAction:
        attempt = context.get("attempt", 0)
        delay = min(2 ** attempt * 5, 300)  # 5s, 10s, 20s... max 5 min
        
        return RecoveryAction(
            retry=True,
            delay_seconds=delay,
            switch_provider=attempt > 2,  # Switch after 3 attempts
        )


class AuthRecovery(RecoveryStrategy):
    """Handle authentication errors."""
    
    def can_handle(self, error: Exception) -> bool:
        msg = str(error).lower()
        return any(k in msg for k in ["401", "unauthorized", "invalid api key", "authentication"])
    
    def recover(self, error: Exception, context: Dict[str, Any]) -> RecoveryAction:
        # Auth errors usually need user intervention, but try rotating key if available
        return RecoveryAction(
            retry=False,  # Don't retry without new credentials
            delay_seconds=0,
            switch_provider=True,  # Try another provider
        )


class ContentPolicyRecovery(RecoveryStrategy):
    """Handle content policy violations."""
    
    def can_handle(self, error: Exception) -> bool:
        msg = str(error).lower()
        return any(k in msg for k in ["content policy", "safety", "moderation", "blocked"])
    
    def recover(self, error: Exception, context: Dict[str, Any]) -> RecoveryAction:
        # Strip potentially problematic content and retry
        return RecoveryAction(
            retry=True,
            delay_seconds=0,
            strip_images=True,
            modify_messages=lambda msgs: self._sanitize_messages(msgs),
        )
    
    def _sanitize_messages(self, messages: List[Dict]) -> List[Dict]:
        """Remove or modify potentially problematic content."""
        # Simple implementation: truncate long messages, remove images
        sanitized = []
        for msg in messages:
            new_msg = msg.copy()
            content = new_msg.get("content", "")
            if isinstance(content, str) and len(content) > 4000:
                new_msg["content"] = content[:4000] + "... [truncated]"
            elif isinstance(content, list):
                # Remove image content
                new_msg["content"] = [
                    p for p in content 
                    if p.get("type") != "image_url"
                ]
            sanitized.append(new_msg)
        return sanitized


class ContextLengthRecovery(RecoveryStrategy):
    """Handle context length exceeded errors."""
    
    def can_handle(self, error: Exception) -> bool:
        msg = str(error).lower()
        return any(k in msg for k in ["context length", "maximum context", "token limit", "too long"])
    
    def recover(self, error: Exception, context: Dict[str, Any]) -> RecoveryAction:
        return RecoveryAction(
            retry=True,
            delay_seconds=0,
            reduce_context=True,
        )


class ServerErrorRecovery(RecoveryStrategy):
    """Handle 5xx server errors."""
    
    def can_handle(self, error: Exception) -> bool:
        msg = str(error).lower()
        return any(k in msg for k in ["500", "502", "503", "504", "server error", "internal error"])
    
    def recover(self, error: Exception, context: Dict[str, Any]) -> RecoveryAction:
        attempt = context.get("attempt", 0)
        delay = min(2 ** attempt * 2, 60)  # 2s, 4s, 8s... max 1 min
        
        return RecoveryAction(
            retry=attempt < 5,  # Max 5 retries
            delay_seconds=delay,
            switch_provider=attempt > 3,
        )


# Registry of recovery strategies
RECOVERY_STRATEGIES: List[RecoveryStrategy] = [
    RateLimitRecovery(),
    AuthRecovery(),
    ContentPolicyRecovery(),
    ContextLengthRecovery(),
    ServerErrorRecovery(),
]


def classify_error(error: Exception) -> ErrorType:
    """Classify an error into a type."""
    msg = str(error).lower()
    
    if any(k in msg for k in ["rate limit", "429", "too many requests", "quota"]):
        return ErrorType.RATE_LIMIT
    if any(k in msg for k in ["401", "unauthorized", "invalid api key"]):
        return ErrorType.AUTH
    if any(k in msg for k in ["content policy", "safety", "moderation"]):
        return ErrorType.CONTENT_POLICY
    if any(k in msg for k in ["context length", "token limit", "too long"]):
        return ErrorType.CONTEXT_LENGTH
    if any(k in msg for k in ["500", "502", "503", "504", "server error"]):
        return ErrorType.SERVER_ERROR
    if any(k in msg for k in ["network", "timeout", "connection"]):
        return ErrorType.NETWORK
    
    return ErrorType.UNKNOWN


def recover_from_error(
    error: Exception,
    messages: List[Dict[str, Any]],
    attempt: int = 0,
) -> tuple[List[Dict[str, Any]], float, bool]:
    """
    Attempt to recover from an error.
    
    Returns (modified_messages, delay_seconds, should_retry).
    """
    error_type = classify_error(error)
    logger.info("Attempting recovery from %s error (attempt %d)", error_type.value, attempt)
    
    for strategy in RECOVERY_STRATEGIES:
        if strategy.can_handle(error):
            context = {"attempt": attempt, "error_type": error_type}
            action = strategy.recover(error, context)
            
            if not action.retry:
                return messages, 0, False
            
            # Apply modifications
            new_messages = messages
            if action.modify_messages:
                new_messages = action.modify_messages(messages)
            if action.strip_images:
                new_messages = [
                    {**m, "content": [p for p in m.get("content", []) if p.get("type") != "image_url"]}
                    if isinstance(m.get("content"), list) else m
                    for m in new_messages
                ]
            if action.reduce_context:
                # Import here to avoid circular import
                from .compression import compress_conversation
                new_messages, _ = compress_conversation(new_messages, target_tokens=4000)
            
            # Record recovery attempt
            db.run(
                "INSERT INTO recovery_attempts(error_type, attempt, delay_seconds, created_at) VALUES (?, ?, ?, ?)",
                error_type.value, attempt, action.delay_seconds, db.now(),
            )
            
            return new_messages, action.delay_seconds, True
    
    # No strategy found
    logger.warning("No recovery strategy for error: %s", error)
    return messages, 0, False


def init_recovery_table():
    """Create recovery tracking table."""
    db.run("""
        CREATE TABLE IF NOT EXISTS recovery_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            error_type TEXT,
            attempt INTEGER,
            delay_seconds REAL,
            created_at TEXT
        )
    """)
