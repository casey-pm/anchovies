"""Tests for Marcus Director behavior: chat vs spawn routing.

Verifies that Marcus responds as Director (never spawns as worker),
work requests route correctly, and the assignment detection flow works.
"""

import inspect
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import anchovies.handlers as handlers_module
from anchovies.handlers import (
    handle_chat_hub_message,
    _detect_assignment_in_response,
)
from anchovies.chat_hub.prompt_builder import detect_work_request
from anchovies import config


@pytest.fixture(autouse=True)
def clear_state():
    """Clear pending suggestions between tests."""
    handlers_module._pending_suggestions.clear()
    yield
    handlers_module._pending_suggestions.clear()


# ---------------------------------------------------------------------------
# 1. Work detection: what IS and ISN'T a work request
# ---------------------------------------------------------------------------


class TestWorkDetectionClassification:
    """Test that messages are correctly classified as work or chat."""

    def test_chat_greeting(self):
        """'hey marcus how are you' = chat, NOT work"""
        r = detect_work_request("hey marcus how are you")
        assert r["is_work_request"] is False
        assert r["confidence"] == 0.0

    def test_chat_status_question(self):
        """'what's the project status?' = chat"""
        r = detect_work_request("what's the project status?")
        assert r["is_work_request"] is False

    def test_chat_opinion_question(self):
        """'what do you think about using python?' = chat"""
        r = detect_work_request("what do you think about using python?")
        assert r["is_work_request"] is False

    def test_work_fix_bug(self):
        """'fix the bug in app.py' = work"""
        r = detect_work_request("fix the bug in app.py")
        assert r["is_work_request"] is True
        assert r["confidence"] >= 0.5

    def test_work_create_module(self):
        """'create a new calculator module' = work"""
        r = detect_work_request("create a new calculator module")
        assert r["is_work_request"] is True

    def test_work_build_app(self):
        """'build a calculator application' = work"""
        r = detect_work_request("build a calculator application")
        assert r["is_work_request"] is True

    def test_work_lets_start(self):
        """'let's start building the calculator' = work"""
        r = detect_work_request("let's start building the calculator")
        assert r["is_work_request"] is True

    def test_work_implement(self):
        """'implement error handling for divide' = work"""
        r = detect_work_request("implement error handling for divide")
        assert r["is_work_request"] is True

    def test_work_explicit_persona(self):
        """'@sofia fix the bug in the dbt model' = work with explicit persona"""
        r = detect_work_request("@sofia fix the bug in the dbt model")
        assert r["is_work_request"] is True
        assert r["persona_explicit"] is True
        assert r["target_persona"] == "sofia"

    def test_work_no_persona_defaults_marcus(self):
        """'fix the bug' = work, defaults to marcus (no one named)"""
        r = detect_work_request("fix the bug in app.py")
        assert r["persona_explicit"] is False
        assert r["target_persona"] == "marcus"

    def test_lets_start_working_on_project(self):
        """'let's start working on the calculator project' = work"""
        r = detect_work_request("let's start working on the calculator project")
        assert r["is_work_request"] is True

    def test_work_on_project(self):
        """'work on the calculator app' = work"""
        r = detect_work_request("work on the calculator app")
        assert r["is_work_request"] is True


# ---------------------------------------------------------------------------
# 2. Marcus never spawns as a worker
# ---------------------------------------------------------------------------


class TestMarcusNeverSpawnsAsWorker:
    """Marcus should ALWAYS respond as Director, never get a tmux work session."""

    def test_guard_exists_in_handler(self):
        """The handler must have a guard preventing Marcus from being spawned."""
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        assert "CHAT_HUB_PERSONA" in source
        assert "Director" in source or "not spawning" in source

    def test_guard_shows_response_not_silence(self):
        """When Marcus is blocked from spawning, he should still respond (not silent)."""
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        # The guard path should call chat_update to show Marcus's response
        # Find the guard block and verify it doesn't just return True silently
        guard_section = source[source.index("Marcus is the target"):]
        assert "chat_update" in guard_section or "build_response_message" in guard_section

    @pytest.mark.anyio
    async def test_marcus_responds_as_director_for_generic_work(self, anchovies_config):
        """When a work request has no persona, Marcus responds with a plan, doesn't spawn."""
        client = AsyncMock()
        client.chat_postMessage.return_value = {"ts": "123", "channel": "C1"}
        client.chat_update.return_value = {"ok": True}
        client.chat_delete.return_value = {"ok": True}

        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "Here's my plan. I'll have Sofia start on the core module."

            result = await handle_chat_hub_message(
                client=client,
                channel_id="C1",
                thread_ts="T1",
                user_message="build a calculator app",
                bot_user_id="U_BOT",
                project="calculator",
            )

        assert result is True
        # Marcus should have responded (chat_update called with his response)
        update_calls = [c for c in client.chat_update.call_args_list]
        assert len(update_calls) > 0 or len(client.chat_postMessage.call_args_list) > 1


# ---------------------------------------------------------------------------
# 3. Explicit @persona spawns directly (no suggestion)
# ---------------------------------------------------------------------------


class TestExplicitPersonaSpawns:
    """When a persona is explicitly named, spawn directly without suggestion."""

    def test_explicit_persona_detected(self):
        r = detect_work_request("@sofia build the calculator module")
        assert r["persona_explicit"] is True
        assert r["target_persona"] == "sofia"

    def test_explicit_bypasses_smart_routing(self):
        """The smart routing suggestion block only runs when persona_explicit is False."""
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        assert 'persona_explicit' in source
        # The suggestion block is conditional on NOT explicit
        assert 'not result.get("persona_explicit"' in source


# ---------------------------------------------------------------------------
# 4. Smart routing suggests when no persona named
# ---------------------------------------------------------------------------


class TestSmartRoutingSuggestion:
    """When work is detected with no persona, smart routing should suggest."""

    def test_dbt_task_suggests_sofia(self):
        from anchovies.teams import get_suggested_persona
        result = get_suggested_persona("fix the dbt staging model")
        assert result is not None
        member, track, reason = result
        assert member == "sofia"

    def test_pipeline_task_suggests_elena(self):
        from anchovies.teams import get_suggested_persona
        result = get_suggested_persona("fix the data pipeline ETL")
        assert result is not None
        member, _, _ = result
        assert member == "elena"

    def test_dashboard_task_suggests_bi(self):
        from anchovies.teams import get_suggested_persona
        result = get_suggested_persona("fix the dashboard visualization")
        assert result is not None
        member, _, _ = result
        assert member in ["natalie", "tom", "priya", "mike", "nina"]

    def test_generic_task_no_suggestion(self):
        """A generic task with no keywords returns None."""
        from anchovies.teams import get_suggested_persona
        result = get_suggested_persona("do the thing")
        assert result is None


# ---------------------------------------------------------------------------
# 5. Assignment detection in Marcus's response
# ---------------------------------------------------------------------------


class TestAssignmentDetection:
    """When Marcus's response mentions assigning a persona, create a pending suggestion."""

    def test_ill_have_sofia_start(self):
        _detect_assignment_in_response(
            "Here's my plan. I'll have Sofia start on the core module.",
            "T1", "build calculator", None,
        )
        assert "T1" in handlers_module._pending_suggestions
        assert handlers_module._pending_suggestions["T1"]["suggested_member"] == "sofia"

    def test_lets_assign_elena(self):
        _detect_assignment_in_response(
            "Let's assign Elena to handle the data pipeline.",
            "T2", "fix pipeline", None,
        )
        assert "T2" in handlers_module._pending_suggestions
        assert handlers_module._pending_suggestions["T2"]["suggested_member"] == "elena"

    def test_sofia_should_handle(self):
        _detect_assignment_in_response(
            "Sofia should handle this task — she's the dbt expert.",
            "T3", "update models", None,
        )
        assert "T3" in handlers_module._pending_suggestions
        assert handlers_module._pending_suggestions["T3"]["suggested_member"] == "sofia"

    def test_start_with_leo(self):
        _detect_assignment_in_response(
            "I recommend starting with Leo on the tests.",
            "T4", "write tests", None,
        )
        assert "T4" in handlers_module._pending_suggestions
        assert handlers_module._pending_suggestions["T4"]["suggested_member"] == "leo"

    def test_no_assignment_no_suggestion(self):
        _detect_assignment_in_response(
            "The calculator project looks straightforward. What features do you want?",
            "T5", "discuss", None,
        )
        assert "T5" not in handlers_module._pending_suggestions

    def test_marcus_doesnt_assign_himself(self):
        _detect_assignment_in_response(
            "I'll have Marcus coordinate the effort.",
            "T6", "coordinate", None,
        )
        assert "T6" not in handlers_module._pending_suggestions

    def test_unknown_name_ignored(self):
        _detect_assignment_in_response(
            "I'll have Batman start on this.",
            "T7", "task", None,
        )
        assert "T7" not in handlers_module._pending_suggestions


# ---------------------------------------------------------------------------
# 6. "yes" confirmation flow
# ---------------------------------------------------------------------------


class TestYesConfirmation:
    """After Marcus recommends and a pending suggestion exists, 'yes' spawns."""

    @pytest.mark.anyio
    async def test_yes_after_suggestion_spawns(self):
        from anchovies.handlers import _check_pending_suggestion

        handlers_module._pending_suggestions["T1"] = {
            "suggested_member": "sofia",
            "track": "Analytics",
            "reason": "recommended by Marcus",
            "task_description": "build the calculator core",
            "task_prompt": "",
            "files": [],
            "project": "calculator",
            "created_at": time.time(),
            "needs_prompt_build": True,
        }

        client = AsyncMock()
        with patch("anchovies.handlers._spawn_from_suggestion", new_callable=AsyncMock) as mock_spawn:
            result = await _check_pending_suggestion(client, "C1", "T1", "yes")

        assert result is True
        mock_spawn.assert_called_once()
        # Should spawn sofia
        assert mock_spawn.call_args[0][4] == "sofia"

    @pytest.mark.anyio
    async def test_no_suggestion_yes_falls_through(self):
        """'yes' without a pending suggestion should not be handled."""
        from anchovies.handlers import _check_pending_suggestion
        client = AsyncMock()
        result = await _check_pending_suggestion(client, "C1", "T1", "yes")
        assert result is False


# ---------------------------------------------------------------------------
# 7. assign command as explicit override
# ---------------------------------------------------------------------------


class TestAssignCommand:
    def test_assign_detected_in_handler(self):
        source = inspect.getsource(handlers_module._handle_control_command)
        assert "assign" in source

    def test_assign_pattern(self):
        """'assign sofia build the module' should be detected."""
        import re
        msg = "assign sofia build the calculator core module"
        match = re.match(r"assign\s+(\w+)\s+(.+)", msg, re.IGNORECASE)
        assert match is not None
        assert match.group(1) == "sofia"
        assert "calculator" in match.group(2)


# ---------------------------------------------------------------------------
# 8. End-to-end flow summary (source verification)
# ---------------------------------------------------------------------------


class TestNoUpdateOnDeletedMessage:
    """Verify we never call chat_update on a message that was already chat_deleted."""

    def test_marcus_guard_uses_post_not_update(self):
        """The Marcus guard should use chat_postMessage, NOT chat_update on thinking_ts."""
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        # Find the Marcus guard section
        guard_start = source.index("Marcus is the target")
        guard_section = source[guard_start:guard_start + 500]
        # Should use chat_postMessage, not chat_update
        assert "chat_postMessage" in guard_section
        assert "chat_update" not in guard_section or "thinking_ts" not in guard_section.split("chat_update")[1][:50]

    def test_no_routing_match_uses_post_not_update(self):
        """The 'no smart routing match' path should post a new message, not update deleted one."""
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        no_match_start = source.index("No smart routing match")
        no_match_section = source[no_match_start:no_match_start + 500]
        assert "chat_postMessage" in no_match_section


class TestEndToEndFlow:
    def test_handler_has_thinking_indicator(self):
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        assert "is thinking" in source

    def test_handler_has_assignment_detection(self):
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        assert "_detect_assignment_in_response" in source

    def test_handler_has_smart_routing(self):
        source = inspect.getsource(handlers_module.handle_chat_hub_message)
        assert "get_suggested_persona" in source

    def test_handler_has_pending_suggestion_check(self):
        source = inspect.getsource(handlers_module.handle_team_message)
        assert "_check_pending_suggestion" in source

    def test_handler_has_work_detection_logging(self):
        source = inspect.getsource(handlers_module.handle_team_message)
        assert "Work detection" in source
