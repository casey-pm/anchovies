"""Tests for rate limiting (token bucket per-user + global)."""

import inspect
import time
from unittest.mock import patch

import pytest

from anchovies.rate_limit import (
    PER_USER_PER_MIN,
    GLOBAL_PER_MIN,
    RateLimiter,
    TokenBucket,
    get_rate_limiter,
    reset_rate_limiter,
)


# ---------------------------------------------------------------------------
# TokenBucket primitives
# ---------------------------------------------------------------------------


class TestTokenBucket:
    def test_starts_full(self):
        b = TokenBucket(capacity=10, refill_per_second=1)
        assert b.tokens == 10

    def test_consume_decrements(self):
        b = TokenBucket(capacity=10, refill_per_second=1)
        assert b.consume(1) is True
        assert b.tokens == 9

    def test_consume_returns_false_when_empty(self):
        b = TokenBucket(capacity=2, refill_per_second=0.001)
        assert b.consume(1) is True
        assert b.consume(1) is True
        assert b.consume(1) is False

    def test_failed_consume_does_not_decrement(self):
        b = TokenBucket(capacity=1, refill_per_second=0.001)
        b.consume(1)  # empty it
        # Refill is so slow we can't gain a full token in microseconds
        result = b.consume(1)
        assert result is False
        # After the failed consume, tokens should still be < 1 (no full token gained)
        assert b.tokens < 1.0

    def test_refill_over_time(self):
        b = TokenBucket(capacity=10, refill_per_second=10)  # 1 per 100ms
        b.tokens = 0
        b.last_refill = time.monotonic() - 0.5  # simulate 500ms ago
        assert b.consume(1) is True  # should have refilled ~5 tokens

    def test_refill_capped_at_capacity(self):
        b = TokenBucket(capacity=5, refill_per_second=10)
        b.tokens = 5
        b.last_refill = time.monotonic() - 100  # ages ago
        b._refill()
        assert b.tokens == 5  # capped


# ---------------------------------------------------------------------------
# RateLimiter — per-user
# ---------------------------------------------------------------------------


class TestPerUserLimit:
    def setup_method(self):
        reset_rate_limiter()

    def test_first_n_requests_allowed(self):
        limiter = RateLimiter(per_user_per_min=10, global_per_min=100)
        for i in range(10):
            assert limiter.allow(user_id="U_TEST") is True, f"request {i} should pass"

    def test_eleventh_request_blocked(self):
        limiter = RateLimiter(per_user_per_min=10, global_per_min=100)
        for _ in range(10):
            limiter.allow(user_id="U_TEST")
        assert limiter.allow(user_id="U_TEST") is False

    def test_users_have_independent_buckets(self):
        limiter = RateLimiter(per_user_per_min=2, global_per_min=100)
        # User A exhausts their bucket
        assert limiter.allow(user_id="A") is True
        assert limiter.allow(user_id="A") is True
        assert limiter.allow(user_id="A") is False
        # User B is unaffected
        assert limiter.allow(user_id="B") is True
        assert limiter.allow(user_id="B") is True


# ---------------------------------------------------------------------------
# RateLimiter — global
# ---------------------------------------------------------------------------


class TestGlobalLimit:
    def setup_method(self):
        reset_rate_limiter()

    def test_global_limit_enforced_across_users(self):
        limiter = RateLimiter(per_user_per_min=100, global_per_min=3)
        assert limiter.allow(user_id="A") is True
        assert limiter.allow(user_id="B") is True
        assert limiter.allow(user_id="C") is True
        # Global bucket exhausted
        assert limiter.allow(user_id="D") is False

    def test_global_blocks_high_traffic_user(self):
        """If global limit hits before user limit, request is blocked."""
        limiter = RateLimiter(per_user_per_min=100, global_per_min=2)
        assert limiter.allow(user_id="A") is True
        assert limiter.allow(user_id="A") is True
        assert limiter.allow(user_id="A") is False  # global cap

    def test_global_token_not_wasted_when_user_limited(self):
        """If user is already over limit, global token must NOT be consumed."""
        limiter = RateLimiter(per_user_per_min=2, global_per_min=10)
        # Exhaust user A
        limiter.allow(user_id="A")
        limiter.allow(user_id="A")
        assert limiter.allow(user_id="A") is False
        # Verify global bucket still has 8 tokens (only 2 consumed by A's allowed reqs)
        assert limiter._global_bucket.tokens == pytest.approx(8.0, abs=0.5)


# ---------------------------------------------------------------------------
# Refill behaviour
# ---------------------------------------------------------------------------


class TestRefill:
    def test_tokens_replenish_after_wait(self):
        # 60 per minute = 1 per second
        limiter = RateLimiter(per_user_per_min=60, global_per_min=600)
        for _ in range(60):
            limiter.allow(user_id="U")
        assert limiter.allow(user_id="U") is False

        # Wait a bit and try again — at 1 token/sec we should have a new one
        time.sleep(1.1)
        assert limiter.allow(user_id="U") is True


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_rate_limiter_returns_same_instance(self):
        reset_rate_limiter()
        l1 = get_rate_limiter()
        l2 = get_rate_limiter()
        assert l1 is l2

    def test_reset_creates_new_instance(self):
        reset_rate_limiter()
        l1 = get_rate_limiter()
        reset_rate_limiter()
        l2 = get_rate_limiter()
        assert l1 is not l2


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_per_user_default(self):
        import os
        prev = os.environ.pop("RATE_LIMIT_PER_USER_PER_MIN", None)
        try:
            import importlib
            from anchovies import rate_limit as rl
            importlib.reload(rl)
            assert rl.PER_USER_PER_MIN == 10
        finally:
            if prev is not None:
                os.environ["RATE_LIMIT_PER_USER_PER_MIN"] = prev
            import importlib
            from anchovies import rate_limit as rl
            importlib.reload(rl)

    def test_global_default(self):
        import os
        prev = os.environ.pop("RATE_LIMIT_GLOBAL_PER_MIN", None)
        try:
            import importlib
            from anchovies import rate_limit as rl
            importlib.reload(rl)
            assert rl.GLOBAL_PER_MIN == 30
        finally:
            if prev is not None:
                os.environ["RATE_LIMIT_GLOBAL_PER_MIN"] = prev
            import importlib
            from anchovies import rate_limit as rl
            importlib.reload(rl)


# ---------------------------------------------------------------------------
# handlers.py integration
# ---------------------------------------------------------------------------


class TestHandlerIntegration:
    def test_handle_team_message_calls_rate_limiter(self):
        """Verify handlers.handle_team_message integrates the rate limiter."""
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_team_message)
        assert "get_rate_limiter" in source
        assert "limiter.allow" in source

    def test_handler_signature_includes_user_id(self):
        from anchovies import handlers
        sig = inspect.signature(handlers.handle_team_message)
        assert "user_id" in sig.parameters

    def test_app_passes_user_id(self):
        """app.py should pass event.get('user') to handle_team_message."""
        from anchovies import app
        source = inspect.getsource(app.create_app)
        assert "user_id=event.get" in source

    def test_crosstalk_skips_rate_limit(self):
        """is_crosstalk=True should bypass the rate limiter."""
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_team_message)
        # Look for the conditional that skips the limiter when crosstalk
        assert "if not is_crosstalk" in source
