"""Rate limit tracker: provider-specific backoff and quota management.

Inspired by Hermes Agent's rate_limit_tracker.py, simplified for ai-workspace.
Tracks requests per provider and enforces cooldowns.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

from . import db

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limit configuration for a provider."""
    requests_per_minute: int = 60
    requests_per_day: int = 10000
    cooldown_seconds: int = 60
    backoff_multiplier: float = 2.0


# Default configurations per provider
DEFAULT_CONFIGS: Dict[str, RateLimitConfig] = {
    "openai": RateLimitConfig(requests_per_minute=60, requests_per_day=10000),
    "anthropic": RateLimitConfig(requests_per_minute=50, requests_per_day=5000),
    "openrouter": RateLimitConfig(requests_per_minute=100, requests_per_day=10000),
    "gemini": RateLimitConfig(requests_per_minute=60, requests_per_day=1500),
    "groq": RateLimitConfig(requests_per_minute=30, requests_per_day=1000),
    "local_ollama": RateLimitConfig(requests_per_minute=1000, requests_per_day=100000),
    "default": RateLimitConfig(),
}


@dataclass
class RateLimitState:
    """Current rate limit state for a provider."""
    provider: str
    requests_this_minute: int = 0
    requests_today: int = 0
    minute_window_start: float = 0
    day_window_start: float = 0
    cooldown_until: float = 0
    consecutive_failures: int = 0


class RateLimitTracker:
    """Tracks rate limits for a provider."""
    
    def __init__(self, provider: str, config: Optional[RateLimitConfig] = None):
        self.provider = provider
        self.config = config or DEFAULT_CONFIGS.get(provider, DEFAULT_CONFIGS["default"])
        self._state = self._load_state()
    
    def _load_state(self) -> RateLimitState:
        """Load state from database."""
        row = db.one("SELECT * FROM rate_limits WHERE provider = ?", self.provider)
        if row:
            return RateLimitState(
                provider=self.provider,
                requests_this_minute=row["requests_this_minute"],
                requests_today=row["requests_today"],
                minute_window_start=row["minute_window_start"],
                day_window_start=row["day_window_start"],
                cooldown_until=row["cooldown_until"],
                consecutive_failures=row["consecutive_failures"],
            )
        return RateLimitState(provider=self.provider)
    
    def _save_state(self):
        """Save state to database."""
        db.run("""
            INSERT OR REPLACE INTO rate_limits
            (provider, requests_this_minute, requests_today, minute_window_start, day_window_start, cooldown_until, consecutive_failures, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            self.provider,
            self._state.requests_this_minute,
            self._state.requests_today,
            self._state.minute_window_start,
            self._state.day_window_start,
            self._state.cooldown_until,
            self._state.consecutive_failures,
            db.now(),
        )
    
    def _reset_windows(self):
        """Reset expired time windows."""
        now = time.time()
        
        # Reset minute window
        if now - self._state.minute_window_start > 60:
            self._state.requests_this_minute = 0
            self._state.minute_window_start = now
        
        # Reset day window
        if now - self._state.day_window_start > 86400:
            self._state.requests_today = 0
            self._state.day_window_start = now
    
    def can_request(self) -> tuple[bool, Optional[float]]:
        """
        Check if a request can be made.
        Returns (allowed, wait_seconds).
        """
        self._reset_windows()
        now = time.time()
        
        # Check cooldown
        if self._state.cooldown_until > now:
            wait = self._state.cooldown_until - now
            return False, wait
        
        # Check minute limit
        if self._state.requests_this_minute >= self.config.requests_per_minute:
            wait = 60 - (now - self._state.minute_window_start)
            return False, max(wait, 1)
        
        # Check day limit
        if self._state.requests_today >= self.config.requests_per_day:
            wait = 86400 - (now - self._state.day_window_start)
            return False, max(wait, 60)
        
        return True, None
    
    def record_request(self, success: bool = True):
        """Record a request."""
        self._reset_windows()
        
        self._state.requests_this_minute += 1
        self._state.requests_today += 1
        
        if success:
            self._state.consecutive_failures = 0
        else:
            self._state.consecutive_failures += 1
            # Apply exponential backoff cooldown
            cooldown = self.config.cooldown_seconds * (
                self.config.backoff_multiplier ** min(self._state.consecutive_failures, 5)
            )
            self._state.cooldown_until = time.time() + cooldown
            logger.warning(
                "Rate limit cooldown for %s: %.0f seconds (failure %d)",
                self.provider, cooldown, self._state.consecutive_failures
            )
        
        self._save_state()
    
    def get_status(self) -> Dict[str, Any]:
        """Get current rate limit status."""
        self._reset_windows()
        now = time.time()
        
        return {
            "provider": self.provider,
            "requests_this_minute": self._state.requests_this_minute,
            "requests_today": self._state.requests_today,
            "minute_limit": self.config.requests_per_minute,
            "day_limit": self.config.requests_per_day,
            "cooldown_until": self._state.cooldown_until,
            "cooldown_remaining": max(0, self._state.cooldown_until - now),
            "consecutive_failures": self._state.consecutive_failures,
            "can_request": self.can_request()[0],
        }


# Global tracker registry
_trackers: Dict[str, RateLimitTracker] = {}


def get_tracker(provider: str) -> RateLimitTracker:
    """Get or create a rate limit tracker for a provider."""
    if provider not in _trackers:
        _trackers[provider] = RateLimitTracker(provider)
    return _trackers[provider]


def check_rate_limit(provider: str) -> tuple[bool, Optional[float]]:
    """Check if a request to provider is allowed."""
    return get_tracker(provider).can_request()


def record_request(provider: str, success: bool = True):
    """Record a request to a provider."""
    get_tracker(provider).record_request(success)


def init_rate_limit_table():
    """Create rate limit tracking table."""
    db.run("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            provider TEXT PRIMARY KEY,
            requests_this_minute INTEGER DEFAULT 0,
            requests_today INTEGER DEFAULT 0,
            minute_window_start REAL DEFAULT 0,
            day_window_start REAL DEFAULT 0,
            cooldown_until REAL DEFAULT 0,
            consecutive_failures INTEGER DEFAULT 0,
            updated_at TEXT
        )
    """)
