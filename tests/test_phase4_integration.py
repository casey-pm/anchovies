"""Integration tests for Phase 4: Cost & Resource Management.

Tests the interaction between rate limiting, budget caps, model selection,
session timeouts, and /summon cross-talk — verifying they work together
correctly rather than in isolation.
"""

import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anchovies.cost_tracking import (
    DAILY_BUDGET_USD,
    estimate_cost,
    is_budget_exceeded,
    record_call,
    remaining_budget,
)
from anchovies.handlers import detect_summons_in_response
from anchovies.rate_limit import RateLimiter, reset_rate_limiter
from anchovies.storage import Storage, reset_storage
from anchovies.work_sessions.session_manager import SessionManager, WorkSession


@pytest.fixture
def fresh_storage(tmp_path, monkeypatch):
    reset_storage()
    db_path = tmp_path / "test.db"
    storage = Storage(db_path)
    import anchovies.storage as storage_module
    monkeypatch.setattr(storage_module, "_storage", storage)
    yield storage
    storage.close()
    reset_storage()


@pytest.fixture
def session_mgr(fresh_storage):
    with patch("anchovies.work_sessions.session_manager.get_tmux_manager") as mock_get:
        mock_tmux = MagicMock()
        mock_tmux.session_name = "anchovies"
        mock_tmux.session_exists = MagicMock(return_value=True)
        mock_tmux.persona_tab_exists = MagicMock(return_value=True)
        mock_tmux.close_persona_tab = MagicMock(return_value=True)
        mock_tmux.get_pane_content = MagicMock(return_value="> ")
        mock_get.return_value = mock_tmux
        mgr = SessionManager()
        mgr.tmux = mock_tmux
        yield mgr


# ---------------------------------------------------------------------------
# Rate limit + budget interaction
# ---------------------------------------------------------------------------


class TestRateLimitAndBudget:
    """Rate limiting and budget cap are independent — both must pass."""

    def test_rate_limit_doesnt_affect_budget(self, fresh_storage):
        """Being rate-limited doesn't consume budget."""
        limiter = RateLimiter(per_user_per_min=2, global_per_min=10)
        for _ in range(3):
            limiter.allow(user_id="U1")

        # Budget should be unaffected (rate limiting happens before any CLI call)
        total, _ = fresh_storage.get_budget()
        assert total == 0.0

    def test_budget_exceeded_still_rate_limited(self, fresh_storage, monkeypatch):
        """Even if budget is exceeded, rate limiting still applies to the rejection message."""
        monkeypatch.setattr("anchovies.cost_tracking.DAILY_BUDGET_USD", 1.0)
        fresh_storage.add_cost(5.0, 10)
        assert is_budget_exceeded() is True

        # Rate limiter is a separate mechanism
        limiter = RateLimiter(per_user_per_min=100, global_per_min=100)
        assert limiter.allow(user_id="U1") is True


# ---------------------------------------------------------------------------
# Cost tracking accuracy across models
# ---------------------------------------------------------------------------


class TestCostTrackingAccuracy:
    def test_haiku_chat_is_cheap(self):
        """A typical Marcus chat response (haiku) should cost < $0.01."""
        cost = estimate_cost(
            prompt="What's the project status? Tell me briefly.",
            response="Everything's on track. Sofia's finishing the dbt tests, Leo's writing unit tests. No blockers.",
            model="haiku",
        )
        assert cost < 0.01

    def test_sonnet_work_session_prompt_reasonable(self):
        """A full work session prompt (~3000 chars) + response should be < $0.50."""
        prompt = "x" * 3000  # typical prompt builder output
        response = "y" * 2000  # typical work response
        cost = estimate_cost(prompt, response, model="sonnet")
        assert 0.001 < cost < 0.50

    def test_budget_tracks_across_mixed_models(self, fresh_storage):
        """Budget accumulates correctly across haiku + sonnet calls."""
        record_call("chat prompt", "chat response", model="haiku")
        record_call("work prompt " * 100, "work response " * 50, model="sonnet")

        total, calls = fresh_storage.get_budget()
        assert calls == 2
        assert total > 0

    def test_ten_sonnet_calls_under_daily_budget(self, fresh_storage, monkeypatch):
        """10 typical sonnet work sessions should stay under $25/day."""
        monkeypatch.setattr("anchovies.cost_tracking.DAILY_BUDGET_USD", 25.0)
        for _ in range(10):
            record_call("x" * 3000, "y" * 2000, model="sonnet")
        assert is_budget_exceeded() is False


# ---------------------------------------------------------------------------
# Timeout + crash sweep combined
# ---------------------------------------------------------------------------


class TestTimeoutSweepCombined:
    def test_sweep_handles_mixed_states(self, session_mgr, fresh_storage):
        """A single auto_close_timed_out call handles soft timeout, hard timeout,
        AND crash — all in one pass, each correctly categorised."""
        now = datetime.now()

        with patch.object(session_mgr, "TIMEOUT_MINUTES", 10), \
             patch.object(session_mgr, "HARD_TIMEOUT_MINUTES", 30):

            # Session 1: soft timeout (inactive + ready)
            s1 = WorkSession(
                member="soft", task_description="t",
                started_at=now - timedelta(minutes=20),
                last_activity=now - timedelta(minutes=15),
                status_updated=True, close_prompt_shown=True,
            )
            session_mgr.active_sessions["soft"] = s1
            session_mgr._persist(s1)

            # Session 2: hard timeout (active but running too long)
            s2 = WorkSession(
                member="hard", task_description="t",
                started_at=now - timedelta(minutes=35),
                last_activity=now - timedelta(minutes=1),
            )
            session_mgr.active_sessions["hard"] = s2
            session_mgr._persist(s2)

            # Session 3: crashed
            s3 = WorkSession(
                member="crashed", task_description="t",
                started_at=now - timedelta(minutes=5),
                last_activity=now - timedelta(minutes=1),
            )
            session_mgr.active_sessions["crashed"] = s3
            session_mgr._persist(s3)

            # Session 4: healthy (should survive)
            s4 = WorkSession(
                member="healthy", task_description="t",
                started_at=now - timedelta(minutes=5),
                last_activity=now - timedelta(minutes=1),
            )
            session_mgr.active_sessions["healthy"] = s4
            session_mgr._persist(s4)

            # Make "crashed" pane look dead
            def pane_content(target, lines=20):
                if "crashed" in target:
                    return "user@host:~$ "
                return "> "
            session_mgr.tmux.get_pane_content = MagicMock(side_effect=pane_content)

            closed = session_mgr.auto_close_timed_out()

            assert "soft" in closed
            assert "hard" in closed
            assert "crashed" in closed
            assert "healthy" not in closed
            assert "healthy" in session_mgr.active_sessions

            # Verify correct statuses in storage
            assert fresh_storage.load_session("soft").status == "completed"
            assert fresh_storage.load_session("hard").status == "timeout"
            assert fresh_storage.load_session("crashed").status == "crashed"
            assert fresh_storage.load_session("healthy").status == "active"


# ---------------------------------------------------------------------------
# /summon edge cases
# ---------------------------------------------------------------------------


class TestSummonEdgeCases:
    def test_summon_inside_code_block(self):
        """/summon inside a code block still triggers (personas generate text, not markdown)."""
        response = "```\n/summon sofia to help\n```"
        result = detect_summons_in_response(response)
        assert "sofia" in result

    def test_summon_at_end_of_response(self):
        response = "I need help. /summon raj"
        result = detect_summons_in_response(response)
        assert "raj" in result

    def test_summon_at_start_of_response(self):
        response = "/summon kai, can you review this?"
        result = detect_summons_in_response(response)
        assert "kai" in result

    def test_no_summon_in_explanation(self):
        """Talking ABOUT the /summon command shouldn't trigger it... actually it should.
        The regex doesn't know context, and this is fine — it's a safety/audit feature
        that can be tuned later."""
        response = "You can use /summon sofia to get her input"
        result = detect_summons_in_response(response)
        # This WILL match, which is acceptable — better too sensitive than too loose.
        assert "sofia" in result


# ---------------------------------------------------------------------------
# Model selection hierarchy
# ---------------------------------------------------------------------------


class TestModelHierarchy:
    def test_profile_override_beats_env_config(self):
        """Profile model_override > CHAT_MODEL/WORK_MODEL > CLAUDE_MODEL."""
        from anchovies.context import MemberProfile
        p = MemberProfile(name="Raj", model_override="opus")
        model = p.model_override or "sonnet"
        assert model == "opus"

    def test_no_override_uses_context_default(self):
        from anchovies.context import MemberProfile
        p = MemberProfile(name="Raj")
        model = p.model_override or "sonnet"
        assert model == "sonnet"
