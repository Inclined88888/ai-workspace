"""Credential pool: multiple API keys with rotation and health tracking.

Inspired by Hermes Agent's credential_pool.py, simplified for ai-workspace.
Supports multiple keys per provider with automatic rotation on failure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from . import db, redact
from .security import decrypt, encrypt

logger = logging.getLogger(__name__)


class KeyHealth(Enum):
    """Health status of an API key."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # Rate limited or slow
    FAILED = "failed"      # Auth error or quota exceeded
    UNKNOWN = "unknown"


@dataclass
class Credential:
    """A single API key with metadata."""
    id: int
    provider: str
    key: str
    name: str = ""
    health: KeyHealth = KeyHealth.UNKNOWN
    last_used: float = 0
    last_error: str = ""
    error_count: int = 0
    success_count: int = 0
    rate_limit_until: float = 0  # Timestamp when rate limit expires
    priority: int = 0  # Higher = preferred


class CredentialPool:
    """Manages multiple API keys for a provider."""
    
    def __init__(self, provider: str):
        self.provider = provider
        self._credentials: List[Credential] = []
        self._load()
    
    def _load(self):
        """Load credentials from database."""
        rows = db.all(
            "SELECT * FROM credentials WHERE provider = ? ORDER BY priority DESC, id",
            self.provider
        )
        self._credentials = [
            Credential(
                id=r["id"],
                provider=r["provider"],
                key=decrypt(r["key_enc"]),  # stored Fernet-encrypted, like provider keys in connections
                name=r["name"],
                health=KeyHealth(r["health"]),
                last_used=r["last_used"] or 0,
                last_error=r["last_error"] or "",
                error_count=r["error_count"] or 0,
                success_count=r["success_count"] or 0,
                rate_limit_until=r["rate_limit_until"] or 0,
                priority=r["priority"] or 0,
            )
            for r in rows
        ]
    
    def add(self, key: str, name: str = "", priority: int = 0) -> Credential:
        """Add a new credential to the pool. The key is stored encrypted, never in plaintext."""
        for existing in self._credentials:
            if existing.key == key:
                return existing
        cid = db.run(
            "INSERT INTO credentials(provider, key_enc, name, priority, health, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            self.provider, encrypt(key), name, priority, KeyHealth.UNKNOWN.value, db.now(),
        )
        cred = Credential(id=cid, provider=self.provider, key=key, name=name, priority=priority)
        self._credentials.append(cred)
        self._credentials.sort(key=lambda c: (-c.priority, c.id))
        return cred
    
    def get_next(self) -> Optional[Credential]:
        """Get the next available credential."""
        now = time.time()
        
        for cred in self._credentials:
            # Skip if rate limited
            if cred.rate_limit_until > now:
                continue
            # Skip if failed too many times recently
            if cred.health == KeyHealth.FAILED and cred.error_count > 5:
                continue
            return cred
        
        # All credentials are rate limited or failed
        # Return the one with the earliest rate limit expiry
        available = [c for c in self._credentials if c.rate_limit_until > 0]
        if available:
            return min(available, key=lambda c: c.rate_limit_until)
        
        return self._credentials[0] if self._credentials else None
    
    def mark_success(self, cred: Credential):
        """Mark a credential as successfully used."""
        cred.last_used = time.time()
        cred.success_count += 1
        cred.health = KeyHealth.HEALTHY
        cred.error_count = 0  # Reset on success
        
        db.run(
            "UPDATE credentials SET last_used = ?, success_count = ?, health = ?, error_count = 0 WHERE id = ?",
            cred.last_used, cred.success_count, cred.health.value, cred.id,
        )
    
    def mark_failure(self, cred: Credential, error: str, rate_limit_seconds: int = 0):
        """Mark a credential as failed."""
        cred.last_used = time.time()
        cred.last_error = redact.redact(str(error))[:500]  # provider errors can echo the key back
        cred.error_count += 1
        
        if rate_limit_seconds > 0:
            cred.rate_limit_until = time.time() + rate_limit_seconds
            cred.health = KeyHealth.DEGRADED
        elif "401" in error or "unauthorized" in error.lower():
            cred.health = KeyHealth.FAILED
        else:
            cred.health = KeyHealth.DEGRADED
        
        db.run(
            "UPDATE credentials SET last_used = ?, last_error = ?, error_count = ?, health = ?, rate_limit_until = ? WHERE id = ?",
            cred.last_used, cred.last_error, cred.error_count, cred.health.value, cred.rate_limit_until, cred.id,
        )
        
        logger.warning(
            "Credential %s (%s) failed: %s",
            cred.id, cred.name or "unnamed", cred.last_error[:100]
        )
    
    def remove(self, cred_id: int):
        """Remove a credential from the pool."""
        db.run("DELETE FROM credentials WHERE id = ?", cred_id)
        self._credentials = [c for c in self._credentials if c.id != cred_id]
    
    def list(self) -> List[Dict[str, Any]]:
        """List all credentials (without keys)."""
        return [
            {
                "id": c.id,
                "provider": c.provider,
                "name": c.name,
                "health": c.health.value,
                "last_used": c.last_used,
                "last_error": c.last_error,
                "error_count": c.error_count,
                "success_count": c.success_count,
                "rate_limit_until": c.rate_limit_until,
                "priority": c.priority,
            }
            for c in self._credentials
        ]


# Global pool registry
_pools: Dict[str, CredentialPool] = {}


def get_pool(provider: str) -> CredentialPool:
    """Get or create a credential pool for a provider."""
    if provider not in _pools:
        _pools[provider] = CredentialPool(provider)
    return _pools[provider]


def init_credentials_table():
    """Create credentials table."""
    db.run("""
        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            key_enc TEXT NOT NULL,
            name TEXT DEFAULT '',
            health TEXT DEFAULT 'unknown',
            last_used REAL,
            last_error TEXT,
            error_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            rate_limit_until REAL DEFAULT 0,
            priority INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(provider, key_enc)
        )
    """)
