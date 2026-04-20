"""Tests for ChatHub message processing.

Verifies chat vs work request routing and that the hub uses
the async CLI runner (not sync subprocess).
"""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from anchovies.chat_hub.hub import ChatHub, create_chat_hub


@pytest.fixture
def hub(anchovies_config):
    """Create a ChatHub instance with test config."""
    return create_chat_hub()


class TestProcessMessage:
    """Test ChatHub.process_message routing."""

    @pytest.mark.anyio
    async def test_work_request_with_explicit_persona(self, hub):
        """A work request with explicit persona returns task_prompt."""
        result = await hub.process_message("@sofia fix the bug in app.py")
        assert result["type"] == "work_request"
        assert result["task_prompt"] is not None
        assert result["target_persona"] == "sofia"
        assert result["persona_explicit"] is True

    @pytest.mark.anyio
    async def test_work_request_no_persona_marcus_thinks(self, hub):
        """A work request with no persona — Marcus thinks as Director."""
        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "Here's my plan. I'll have Sofia start."
            result = await hub.process_message("fix the bug in app.py")
        assert result["type"] == "work_request"
        assert result["persona_explicit"] is False
        # task_prompt is None because persona hasn't been confirmed yet
        assert result["task_prompt"] is None
        # But Marcus's response should be his actual Director thinking
        assert "plan" in result["response"].lower() or "Sofia" in result["response"]

    @pytest.mark.anyio
    async def test_chat_message_uses_async_cli(self, hub):
        """Chat messages call run_claude_cli (async), not subprocess.run."""
        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "I'm Marcus, what's the priority?"

            result = await hub.process_message("what's the project status?")

            assert result["type"] == "chat"
            assert result["response"] == "I'm Marcus, what's the priority?"
            mock_cli.assert_called_once()

    @pytest.mark.anyio
    async def test_chat_error_returns_fallback(self, hub):
        """If CLI fails, a fallback response is returned (not a crash)."""
        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            from anchovies.cli_runner import ClaudeCliError
            mock_cli.side_effect = ClaudeCliError("timeout")

            result = await hub.process_message("hello")

            assert result["type"] == "chat"
            assert "snag" in result["response"].lower() or "priority" in result["response"].lower()

    @pytest.mark.anyio
    async def test_conversation_history_passed_to_cli(self, hub):
        """Conversation history is included in the CLI prompt."""
        history = [
            {"role": "user", "content": "previous question", "member": ""},
            {"role": "assistant", "content": "previous answer", "member": "marcus"},
        ]

        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "Following up on that..."

            await hub.process_message(
                "what about now?",
                conversation_history=history,
            )

            # The prompt sent to CLI should contain the history
            prompt_arg = mock_cli.call_args[0][0]
            assert "previous question" in prompt_arg
            assert "previous answer" in prompt_arg


class TestNoSyncSubprocess:
    """Verify that hub.py no longer uses subprocess.run."""

    def test_no_subprocess_import(self):
        """hub.py should not import subprocess at all."""
        import anchovies.chat_hub.hub as hub_module
        import inspect
        source = inspect.getsource(hub_module)
        # Should not have 'import subprocess' (we removed it)
        assert "import subprocess" not in source

    def test_no_subprocess_run_call(self):
        """hub.py should not call subprocess.run anywhere."""
        import anchovies.chat_hub.hub as hub_module
        import inspect
        source = inspect.getsource(hub_module)
        assert "subprocess.run" not in source
