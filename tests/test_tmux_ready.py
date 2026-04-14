"""Tests for Claude CLI ready-detection in TmuxManager.

Verifies that spawn_persona_tab polls for Claude's ready indicator
instead of using a fixed sleep, and handles timeouts correctly.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from anchovies.work_sessions.tmux_manager import TmuxManager


@pytest.fixture
def tmux_manager():
    """Create a TmuxManager with mocked tmux commands."""
    manager = TmuxManager(session_name="test_session")
    # Mock _run_tmux so we don't need actual tmux
    manager._run_tmux = MagicMock(return_value=MagicMock(returncode=0, stdout=""))
    # Mock session_exists to return True
    manager.session_exists = MagicMock(return_value=True)
    # Mock persona_tab_exists to return False (no existing tab)
    manager.persona_tab_exists = MagicMock(return_value=False)
    # Mock close_persona_tab
    manager.close_persona_tab = MagicMock(return_value=True)
    return manager


class TestReadyDetection:
    """Test _wait_for_claude_ready polling logic."""

    @pytest.mark.anyio
    async def test_detects_prompt_character(self, tmux_manager):
        """Ready when '> ' prompt is found in pane content."""
        tmux_manager.get_pane_content = MagicMock(return_value="\n> ")

        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=10, poll_interval=0.1
        )
        assert result is True

    @pytest.mark.anyio
    async def test_detects_how_can_i_help(self, tmux_manager):
        """Ready when 'How can I help' text is found."""
        tmux_manager.get_pane_content = MagicMock(
            return_value="Welcome to Claude!\nHow can I help you today?"
        )

        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=10, poll_interval=0.1
        )
        assert result is True

    @pytest.mark.anyio
    async def test_detects_what_can_i_help(self, tmux_manager):
        """Ready when 'What can I help' text is found."""
        tmux_manager.get_pane_content = MagicMock(
            return_value="What can I help you with?"
        )

        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=10, poll_interval=0.1
        )
        assert result is True

    @pytest.mark.anyio
    async def test_timeout_returns_false(self, tmux_manager):
        """Returns False when Claude doesn't become ready within timeout."""
        # Pane always shows loading, never ready
        tmux_manager.get_pane_content = MagicMock(return_value="Loading...")

        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=0.5, poll_interval=0.1
        )
        assert result is False

    @pytest.mark.anyio
    async def test_polls_until_ready(self, tmux_manager):
        """Polls multiple times before detecting ready state."""
        call_count = 0

        def delayed_ready(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return "Loading Claude..."
            return "> "

        tmux_manager.get_pane_content = MagicMock(side_effect=delayed_ready)

        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=10, poll_interval=0.1
        )
        assert result is True
        assert call_count >= 3

    @pytest.mark.anyio
    async def test_empty_pane_not_ready(self, tmux_manager):
        """Empty pane content is not treated as ready."""
        tmux_manager.get_pane_content = MagicMock(return_value="")

        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=0.5, poll_interval=0.1
        )
        assert result is False


class TestSpawnWithReadyDetection:
    """Test that spawn_persona_tab uses ready-detection."""

    @pytest.mark.anyio
    async def test_spawn_succeeds_when_claude_ready(self, tmux_manager):
        """Spawn returns True when Claude becomes ready."""
        tmux_manager.get_pane_content = MagicMock(return_value="> ")
        tmux_manager._write_prompt_file = MagicMock(return_value="/tmp/test_prompt.txt")

        result = await tmux_manager.spawn_persona_tab("sofia", "test task prompt")
        assert result is True

    @pytest.mark.anyio
    async def test_spawn_fails_on_timeout(self, tmux_manager):
        """Spawn returns False and cleans up when Claude doesn't start."""
        tmux_manager.get_pane_content = MagicMock(return_value="Loading...")
        tmux_manager._write_prompt_file = MagicMock(return_value="/tmp/test_prompt.txt")

        # Use a short timeout so the test doesn't take long
        with patch.object(tmux_manager, '_wait_for_claude_ready') as mock_wait:
            mock_wait.return_value = False

            result = await tmux_manager.spawn_persona_tab("sofia", "test task prompt")

            assert result is False
            # Should have attempted cleanup
            tmux_manager.close_persona_tab.assert_called_once_with("sofia")

    @pytest.mark.anyio
    async def test_no_fixed_sleep_in_spawn(self):
        """spawn_persona_tab source should not contain time.sleep(18)."""
        import inspect
        source = inspect.getsource(TmuxManager.spawn_persona_tab)
        assert "time.sleep(18)" not in source
        assert "sleep(18)" not in source

    @pytest.mark.anyio
    async def test_spawn_is_async(self):
        """spawn_persona_tab should be an async method."""
        import inspect
        assert inspect.iscoroutinefunction(TmuxManager.spawn_persona_tab)
