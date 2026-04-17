"""Tests for smart routing (suggest + confirm)."""

import inspect
import time
from unittest.mock import AsyncMock, patch

import pytest

import anchovies.handlers as handlers_module
from anchovies.handlers import _check_pending_suggestion, SUGGESTION_TIMEOUT_SECONDS


@pytest.fixture(autouse=True)
def clear_suggestions():
    """Clear pending suggestions between tests using the module reference."""
    handlers_module._pending_suggestions.clear()
    yield
    handlers_module._pending_suggestions.clear()


@pytest.fixture
def client():
    return AsyncMock()


def _make_suggestion(member="sofia", task="fix the dbt model", project=None):
    return {
        "suggested_member": member,
        "track": "Analytics",
        "reason": "dbt expertise",
        "task_description": task,
        "task_prompt": "prompt",
        "files": [],
        "project": project,
        "created_at": time.time(),
    }


class TestCheckPendingSuggestion:
    @pytest.mark.anyio
    async def test_no_pending_returns_false(self, client):
        result = await _check_pending_suggestion(client, "C1", "T1", "hello")
        assert result is False

    @pytest.mark.anyio
    async def test_yes_confirms(self, client):
        handlers_module._pending_suggestions["T1"] = _make_suggestion()
        with patch("anchovies.handlers._spawn_from_suggestion", new_callable=AsyncMock) as mock_spawn:
            result = await _check_pending_suggestion(client, "C1", "T1", "yes")
        assert result is True
        assert "T1" not in handlers_module._pending_suggestions
        mock_spawn.assert_called_once()
        assert mock_spawn.call_args[0][4] == "sofia"

    @pytest.mark.anyio
    async def test_go_confirms(self, client):
        handlers_module._pending_suggestions["T1"] = _make_suggestion()
        with patch("anchovies.handlers._spawn_from_suggestion", new_callable=AsyncMock):
            result = await _check_pending_suggestion(client, "C1", "T1", "go")
        assert result is True

    @pytest.mark.anyio
    async def test_persona_name_redirects(self, client):
        handlers_module._pending_suggestions["T1"] = _make_suggestion()
        with patch("anchovies.handlers._spawn_from_suggestion", new_callable=AsyncMock) as mock_spawn:
            result = await _check_pending_suggestion(client, "C1", "T1", "raj")
        assert result is True
        assert mock_spawn.call_args[0][4] == "raj"

    @pytest.mark.anyio
    async def test_no_cancels(self, client):
        handlers_module._pending_suggestions["T1"] = _make_suggestion()
        result = await _check_pending_suggestion(client, "C1", "T1", "no")
        assert result is True
        assert "T1" not in handlers_module._pending_suggestions
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "cancelled" in text.lower()

    @pytest.mark.anyio
    async def test_other_message_falls_through(self, client):
        handlers_module._pending_suggestions["T1"] = _make_suggestion()
        result = await _check_pending_suggestion(client, "C1", "T1", "what about the other bug?")
        assert result is False
        assert "T1" in handlers_module._pending_suggestions

    @pytest.mark.anyio
    async def test_expired_cleaned_up(self, client):
        handlers_module._pending_suggestions["T1"] = _make_suggestion()
        handlers_module._pending_suggestions["T1"]["created_at"] = time.time() - SUGGESTION_TIMEOUT_SECONDS - 10
        result = await _check_pending_suggestion(client, "C1", "T1", "yes")
        assert "T1" not in handlers_module._pending_suggestions
        assert result is False


class TestSmartRoutingInHandler:
    def test_handler_uses_persona_explicit(self):
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        assert "persona_explicit" in source

    def test_handler_calls_get_suggested_persona(self):
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        assert "get_suggested_persona" in source

    def test_handler_stores_pending_suggestion(self):
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        assert "_pending_suggestions" in source

    def test_prompt_builder_returns_persona_explicit(self):
        from anchovies.chat_hub.prompt_builder import detect_work_request
        result = detect_work_request("@sofia fix the bug in app.py")
        assert result["persona_explicit"] is True
        result = detect_work_request("fix the bug in app.py")
        assert result["persona_explicit"] is False


class TestSmartRoutingIntegration:
    def test_check_pending_called_in_handle_team_message(self):
        source = inspect.getsource(handlers_module.handle_team_message)
        assert "_check_pending_suggestion" in source
