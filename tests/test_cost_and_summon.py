"""Tests for cross-talk opt-in (/summon) and cost tracking with budget cap."""

import inspect
from unittest.mock import patch

import pytest

from anchovies.cost_tracking import (
    DAILY_BUDGET_USD,
    estimate_cost,
    get_today_spend,
    is_budget_exceeded,
    is_budget_warning,
    record_call,
    remaining_budget,
)
from anchovies.handlers import detect_summons_in_response, MAX_CHAIN_DEPTH
from anchovies.storage import Storage, reset_storage


# ---------------------------------------------------------------------------
# /summon detection (replaces automatic @mention cross-talk)
# ---------------------------------------------------------------------------


class TestSummonDetection:
    def test_summon_detected(self):
        result = detect_summons_in_response("Let me check with /summon sofia about this")
        assert result == ["sofia"]

    def test_summon_multiple(self):
        result = detect_summons_in_response("/summon sofia and /summon raj for opinions")
        assert "sofia" in result
        assert "raj" in result

    def test_summon_case_insensitive(self):
        result = detect_summons_in_response("/summon Sofia")
        assert result == ["sofia"]

    def test_summon_alias(self):
        """Aliases like 'boss' should resolve to the canonical member name."""
        result = detect_summons_in_response("/summon boss")
        assert result == ["marcus"]

    def test_summon_deduplicated(self):
        result = detect_summons_in_response("/summon sofia /summon sofia again")
        assert result == ["sofia"]

    def test_plain_mention_does_NOT_trigger(self):
        """Plain @sofia or just mentioning Sofia should NOT trigger cross-talk."""
        result = detect_summons_in_response("@sofia worked on this already")
        assert result == []

    def test_conversational_mention_does_NOT_trigger(self):
        result = detect_summons_in_response("Sofia and I discussed this last week")
        assert result == []

    def test_no_summon_returns_empty(self):
        result = detect_summons_in_response("This is a normal response without summoning anyone.")
        assert result == []

    def test_unknown_name_ignored(self):
        result = detect_summons_in_response("/summon unknownperson")
        assert result == []

    def test_summon_with_punctuation(self):
        result = detect_summons_in_response("/summon sofia, can you review this?")
        assert result == ["sofia"]


class TestMaxChainDepth:
    def test_chain_depth_default(self):
        """MAX_CHAIN_DEPTH should default to 3 (Casey's preference)."""
        import os
        prev = os.environ.pop("MAX_SUMMON_DEPTH", None)
        try:
            import importlib
            from anchovies import handlers
            importlib.reload(handlers)
            assert handlers.MAX_CHAIN_DEPTH == 3
        finally:
            if prev is not None:
                os.environ["MAX_SUMMON_DEPTH"] = prev
            import importlib
            from anchovies import handlers
            importlib.reload(handlers)


class TestCrossTalkInHandlerSource:
    def test_uses_summon_not_mention(self):
        """Handler should call detect_summons_in_response, not detect_mentions."""
        from anchovies import handlers
        source = inspect.getsource(handlers.process_member_response)
        assert "detect_summons_in_response" in source

    def test_summon_mention_in_log(self):
        from anchovies import handlers
        source = inspect.getsource(handlers.process_member_response)
        assert "summoned" in source.lower()


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


class TestCostEstimation:
    def test_haiku_cheaper_than_sonnet(self):
        prompt = "x" * 4000  # ~1000 tokens
        response = "y" * 2000  # ~500 tokens
        haiku = estimate_cost(prompt, response, model="haiku")
        sonnet = estimate_cost(prompt, response, model="sonnet")
        assert haiku < sonnet

    def test_sonnet_cheaper_than_opus(self):
        prompt = "x" * 4000
        response = "y" * 2000
        sonnet = estimate_cost(prompt, response, model="sonnet")
        opus = estimate_cost(prompt, response, model="opus")
        assert sonnet < opus

    def test_empty_prompt_minimal_cost(self):
        cost = estimate_cost("", "ok", model="haiku")
        assert cost > 0  # at least 1 token
        assert cost < 0.001

    def test_large_prompt_higher_cost(self):
        small = estimate_cost("hello", "hi", model="sonnet")
        large = estimate_cost("x" * 40000, "y" * 40000, model="sonnet")
        assert large > small

    def test_unknown_model_falls_back_to_sonnet(self):
        """Unknown model names should use sonnet pricing as default."""
        cost_unknown = estimate_cost("hello world", "ok", model="future-model-v99")
        cost_sonnet = estimate_cost("hello world", "ok", model="sonnet")
        assert cost_unknown == cost_sonnet

    def test_none_model_falls_back(self):
        cost = estimate_cost("hello", "world", model=None)
        assert cost > 0


# ---------------------------------------------------------------------------
# Budget tracking via storage
# ---------------------------------------------------------------------------


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


class TestRecordCall:
    def test_records_to_budget(self, fresh_storage):
        record_call(prompt="x" * 4000, response="y" * 2000, model="haiku")
        total, calls = fresh_storage.get_budget()
        assert total > 0
        assert calls == 1

    def test_multiple_calls_accumulate(self, fresh_storage):
        record_call(prompt="a" * 4000, response="b" * 2000, model="haiku")
        record_call(prompt="c" * 4000, response="d" * 2000, model="haiku")
        total, calls = fresh_storage.get_budget()
        assert calls == 2


class TestBudgetExceeded:
    def test_not_exceeded_when_empty(self, fresh_storage, monkeypatch):
        monkeypatch.setattr("anchovies.cost_tracking.DAILY_BUDGET_USD", 25.0)
        assert is_budget_exceeded() is False

    def test_exceeded_when_over_cap(self, fresh_storage, monkeypatch):
        monkeypatch.setattr("anchovies.cost_tracking.DAILY_BUDGET_USD", 1.0)
        fresh_storage.add_cost(1.50, 10)
        assert is_budget_exceeded() is True

    def test_remaining_budget(self, fresh_storage, monkeypatch):
        monkeypatch.setattr("anchovies.cost_tracking.DAILY_BUDGET_USD", 25.0)
        fresh_storage.add_cost(10.0, 5)
        assert remaining_budget() == pytest.approx(15.0)

    def test_remaining_never_negative(self, fresh_storage, monkeypatch):
        monkeypatch.setattr("anchovies.cost_tracking.DAILY_BUDGET_USD", 5.0)
        fresh_storage.add_cost(10.0, 5)
        assert remaining_budget() == 0.0

    def test_warning_at_80_percent(self, fresh_storage, monkeypatch):
        monkeypatch.setattr("anchovies.cost_tracking.DAILY_BUDGET_USD", 25.0)
        monkeypatch.setattr("anchovies.cost_tracking.BUDGET_WARN_FRACTION", 0.8)
        fresh_storage.add_cost(20.0, 10)  # 80%
        assert is_budget_warning() is True
        assert is_budget_exceeded() is False

    def test_no_warning_below_threshold(self, fresh_storage, monkeypatch):
        monkeypatch.setattr("anchovies.cost_tracking.DAILY_BUDGET_USD", 25.0)
        monkeypatch.setattr("anchovies.cost_tracking.BUDGET_WARN_FRACTION", 0.8)
        fresh_storage.add_cost(10.0, 5)  # 40%
        assert is_budget_warning() is False


class TestBudgetGateInHandler:
    def test_handler_checks_budget(self):
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_chat_hub_message)
        assert "is_budget_exceeded" in source

    def test_handler_posts_rejection_message(self):
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_chat_hub_message)
        assert "budget" in source.lower()
        assert "midnight" in source.lower()
