"""
Token-bucket rate limiter for incoming Slack messages.

Two independent buckets:
  - Per-user: prevents any single user from flooding the bot
  - Global:   prevents the bot from being overwhelmed in aggregate

Defaults (configurable via env):
  RATE_LIMIT_PER_USER_PER_MIN  = 10
  RATE_LIMIT_GLOBAL_PER_MIN    = 30

Usage:
    from anchovies.rate_limit import get_rate_limiter
    limiter = get_rate_limiter()
    if not limiter.allow(user_id="U123"):
        # tell the user we're at capacity
        return
    # otherwise process normally

Token-bucket semantics:
  - Each bucket has a capacity equal to its per-minute limit
  - Tokens refill at capacity / 60 per second
  - allow() consumes one token; if none available, returns False
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

# Defaults (read at import time; configurable via env vars)
PER_USER_PER_MIN = int(os.getenv("RATE_LIMIT_PER_USER_PER_MIN", "10"))
GLOBAL_PER_MIN = int(os.getenv("RATE_LIMIT_GLOBAL_PER_MIN", "30"))


@dataclass
class TokenBucket:
    """A simple thread-safe token bucket."""
    capacity: float
    refill_per_second: float
    tokens: float = field(init=False)
    last_refill: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.tokens = self.capacity

    def consume(self, amount: float = 1.0) -> bool:
        """Try to consume `amount` tokens. Returns True if available, False otherwise."""
        with self._lock:
            self._refill()
            if self.tokens >= amount:
                self.tokens -= amount
                return True
            return False

    def _refill(self):
        """Refill tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = now


class RateLimiter:
    """
    Per-user + global rate limiter using token buckets.

    A request is allowed only if BOTH the per-user bucket and the global
    bucket have a token available.
    """

    def __init__(
        self,
        per_user_per_min: int = PER_USER_PER_MIN,
        global_per_min: int = GLOBAL_PER_MIN,
    ):
        self.per_user_per_min = per_user_per_min
        self.global_per_min = global_per_min
        self._user_buckets: dict[str, TokenBucket] = {}
        self._global_bucket = TokenBucket(
            capacity=global_per_min,
            refill_per_second=global_per_min / 60.0,
        )
        self._lock = threading.Lock()

    def _user_bucket(self, user_id: str) -> TokenBucket:
        with self._lock:
            if user_id not in self._user_buckets:
                self._user_buckets[user_id] = TokenBucket(
                    capacity=self.per_user_per_min,
                    refill_per_second=self.per_user_per_min / 60.0,
                )
            return self._user_buckets[user_id]

    def allow(self, user_id: str = "anonymous") -> bool:
        """
        Check if a request from user_id should be allowed right now.

        Both per-user and global buckets must have tokens.

        Returns True if allowed (and consumes a token from each bucket),
        False if rate-limited (no tokens consumed).
        """
        user_bucket = self._user_bucket(user_id)

        # Peek the user bucket first (don't consume yet)
        # We need to consume from BOTH or NEITHER to avoid wasting global capacity
        # on a user who is already over their per-user limit.
        with user_bucket._lock:
            user_bucket._refill()
            if user_bucket.tokens < 1.0:
                return False

        # User bucket has a token; try the global bucket
        if not self._global_bucket.consume(1.0):
            return False

        # Both have tokens — actually consume from the user bucket
        with user_bucket._lock:
            user_bucket.tokens -= 1.0

        return True

    def reset(self):
        """Reset all buckets (useful for tests)."""
        with self._lock:
            self._user_buckets.clear()
        self._global_bucket = TokenBucket(
            capacity=self.global_per_min,
            refill_per_second=self.global_per_min / 60.0,
        )


# Singleton instance
_rate_limiter: RateLimiter | None = None
_singleton_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """Get the singleton RateLimiter instance."""
    global _rate_limiter
    with _singleton_lock:
        if _rate_limiter is None:
            _rate_limiter = RateLimiter()
    return _rate_limiter


def reset_rate_limiter():
    """Reset the singleton (useful for tests)."""
    global _rate_limiter
    with _singleton_lock:
        _rate_limiter = None
