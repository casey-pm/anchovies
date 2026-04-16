"""Tests for Claude CLI ready-detection in TmuxManager.

Verifies that spawn_persona_tab uses process-based detection
(pane_current_command) instead of text-based pane capture, since
Claude Code uses a TUI that doesn't render as plain text.
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
    manager._run_tmux = MagicMock(return_value=MagicMock(returncode=0, stdout=""))
    manager.session_exists = MagicMock(return_value=True)
    manager.persona_tab_exists = MagicMock(return_value=False)
    manager.close_persona_tab = MagicMock(return_value=True)
    # Default: _get_pane_command returns "claude" (process is running)
    manager._get_pane_command = MagicMock(return_value="claude")
    return manager


class TestReadyDetection:
    """Test _wait_for_claude_ready process-based polling."""

    @pytest.mark.anyio
    async def test_detects_claude_process(self, tmux_manager):
        """Ready when pane_current_command shows 'claude'."""
        tmux_manager._get_pane_command = MagicMock(return_value="claude")
        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=10, poll_interval=0.1
        )
        assert result is True

    @pytest.mark.anyio
    async def test_detects_claude_with_path(self, tmux_manager):
        """Ready when command is a full path like '/usr/bin/claude'."""
        tmux_manager._get_pane_command = MagicMock(return_value="/usr/bin/claude")
        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=10, poll_interval=0.1
        )
        assert result is True

    @pytest.mark.anyio
    async def test_timeout_when_bash_only(self, tmux_manager):
        """Times out when the pane stays on bash (claude never starts)."""
        tmux_manager._get_pane_command = MagicMock(return_value="bash")
        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=0.5, poll_interval=0.1
        )
        assert result is False

    @pytest.mark.anyio
    async def test_polls_until_process_appears(self, tmux_manager):
        """Polls multiple times before claude process appears."""
        call_count = 0

        def delayed_start(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return "bash"
            return "claude"

        tmux_manager._get_pane_command = MagicMock(side_effect=delayed_start)

        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=10, poll_interval=0.1
        )
        assert result is True
        assert call_count >= 3

    @pytest.mark.anyio
    async def test_detects_crash_during_settle(self, tmux_manager):
        """If claude exits during the settle period, returns False."""
        call_count = 0

        def crash_during_settle(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return "claude"  # First check: process exists
            return "bash"  # After settle: process crashed

        tmux_manager._get_pane_command = MagicMock(side_effect=crash_during_settle)

        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=10, poll_interval=0.1
        )
        assert result is False

    @pytest.mark.anyio
    async def test_empty_command_not_ready(self, tmux_manager):
        """Empty pane_current_command means pane isn't functional."""
        tmux_manager._get_pane_command = MagicMock(return_value="")
        result = await tmux_manager._wait_for_claude_ready(
            "test_session:sofia", timeout=0.5, poll_interval=0.1
        )
        assert result is False


class TestSpawnWithReadyDetection:
    """Test that spawn_persona_tab uses process-based ready-detection."""

    @pytest.mark.anyio
    async def test_spawn_succeeds_when_claude_running(self, tmux_manager):
        """Spawn returns True when claude process is detected."""
        tmux_manager._get_pane_command = MagicMock(return_value="claude")
        tmux_manager._write_prompt_file = MagicMock(return_value="/tmp/test_prompt.txt")

        result = await tmux_manager.spawn_persona_tab("sofia", "test task prompt")
        assert result is True

    @pytest.mark.anyio
    async def test_spawn_fails_on_timeout(self, tmux_manager):
        """Spawn returns False and cleans up when Claude doesn't start."""
        tmux_manager._write_prompt_file = MagicMock(return_value="/tmp/test_prompt.txt")

        with patch.object(tmux_manager, '_wait_for_claude_ready') as mock_wait:
            mock_wait.return_value = False

            result = await tmux_manager.spawn_persona_tab("sofia", "test task prompt")

            assert result is False
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

    @pytest.mark.anyio
    async def test_uses_process_detection_not_text(self):
        """The ready-detection should use _get_pane_command, not pane text patterns."""
        import inspect
        source = inspect.getsource(TmuxManager._wait_for_claude_ready)
        assert "_get_pane_command" in source
        # Should NOT rely on text patterns for primary detection
        assert "How can I help" not in source
