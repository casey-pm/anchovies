"""Tests for Marcus Director behavior: chat vs spawn routing."""

import inspect
from unittest.mock import AsyncMock, patch

import pytest

import anchovies.handlers as handlers_module
from anchovies.handlers.routing import handle_chat_hub_message, should_route_to_chat_hub
from anchovies.chat_hub.prompt_builder import detect_work_request
from anchovies import config


# ---------------------------------------------------------------------------
# Work detection classification
# ---------------------------------------------------------------------------


class TestWorkDetectionClassification:
    def test_chat_greeting(self):
        r = detect_work_request("hey marcus how are you")
        assert r["is_work_request"] is False

    def test_chat_status_question(self):
        r = detect_work_request("what's the project status?")
        assert r["is_work_request"] is False

    def test_chat_opinion_question(self):
        r = detect_work_request("what do you think about using python?")
        assert r["is_work_request"] is False

    def test_work_fix_bug(self):
        r = detect_work_request("fix the bug in app.py")
        assert r["is_work_request"] is True

    def test_work_build_app(self):
        r = detect_work_request("build a calculator application")
        assert r["is_work_request"] is True

    def test_work_lets_start(self):
        r = detect_work_request("let's start building the calculator")
        assert r["is_work_request"] is True

    def test_work_implement(self):
        r = detect_work_request("implement error handling for divide")
        assert r["is_work_request"] is True

    def test_work_explicit_persona(self):
        r = detect_work_request("@sofia fix the bug in the dbt model")
        assert r["persona_explicit"] is True
        assert r["target_persona"] == "sofia"

    def test_work_no_persona_defaults_marcus(self):
        r = detect_work_request("fix the bug in app.py")
        assert r["persona_explicit"] is False
        assert r["target_persona"] == "marcus"


# ---------------------------------------------------------------------------
# Marcus never spawns as worker
# ---------------------------------------------------------------------------


class TestMarcusNeverSpawnsAsWorker:
    @pytest.mark.anyio
    async def test_marcus_responds_as_director(self, anchovies_config):
        client = AsyncMock()
        client.chat_postMessage.return_value = {"ts": "123", "channel": "C1"}
        client.chat_update.return_value = {"ok": True}
        client.chat_delete.return_value = {"ok": True}

        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "Here's my plan. I'll have Sofia start."
            result = await handle_chat_hub_message(
                client=client, channel_id="C1", thread_ts="T1",
                user_message="build a calculator app",
                bot_user_id="U_BOT", project="calculator",
            )

        assert result is True
        # Marcus should have responded (posted messages)
        assert client.chat_postMessage.call_count >= 1


# ---------------------------------------------------------------------------
# Explicit persona spawns directly
# ---------------------------------------------------------------------------


class TestExplicitPersonaSpawns:
    def test_explicit_persona_detected(self):
        r = detect_work_request("@sofia build the calculator module")
        assert r["persona_explicit"] is True
        assert r["target_persona"] == "sofia"


# ---------------------------------------------------------------------------
# Routing decision
# ---------------------------------------------------------------------------


class TestRoutingDecision:
    def test_work_request_goes_to_chat_hub(self):
        from anchovies.router import RoutingResult
        routing = RoutingResult(members=["marcus"], cleaned_message="fix bug", is_broadcast=False)
        assert should_route_to_chat_hub("fix the bug in app.py", routing, is_crosstalk=False) is True

    def test_crosstalk_skips_chat_hub(self):
        from anchovies.router import RoutingResult
        routing = RoutingResult(members=["sofia"], cleaned_message="response", is_broadcast=False)
        assert should_route_to_chat_hub("some response", routing, is_crosstalk=True) is False

    def test_no_members_goes_to_chat_hub(self):
        from anchovies.router import RoutingResult
        routing = RoutingResult(members=[], cleaned_message="hello", is_broadcast=False)
        assert should_route_to_chat_hub("hello there", routing, is_crosstalk=False) is True

    def test_specific_persona_skips_chat_hub(self):
        from anchovies.router import RoutingResult
        routing = RoutingResult(members=["sofia"], cleaned_message="hello", is_broadcast=False)
        assert should_route_to_chat_hub("hello there", routing, is_crosstalk=False) is False


# ---------------------------------------------------------------------------
# Assign command
# ---------------------------------------------------------------------------


class TestAssignCommand:
    def test_assign_pattern(self):
        import re
        msg = "assign sofia build the calculator core module"
        match = re.match(r"assign\s+(\w+)\s+(.+)", msg, re.IGNORECASE)
        assert match is not None
        assert match.group(1) == "sofia"
        assert "calculator" in match.group(2)


# ---------------------------------------------------------------------------
# Unified spawner exists
# ---------------------------------------------------------------------------


class TestUnifiedSpawner:
    def test_spawn_session_for_task_exists(self):
        from anchovies.handlers.spawner import spawn_session_for_task
        assert inspect.iscoroutinefunction(spawn_session_for_task)

    def test_auto_spawn_from_director_exists(self):
        from anchovies.handlers.spawner import auto_spawn_from_director
        assert inspect.iscoroutinefunction(auto_spawn_from_director)
