"""Tests for kill switch, pause/resume, and daily summary commands."""

import inspect
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anchovies.handlers import _handle_control_command, _paused
from anchovies.storage import Storage, reset_storage
from anchovies.work_sessions.session_manager import SessionManager, WorkSession
import anchovies.handlers as handlers_module


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
def client():
    return AsyncMock()


@pytest.fixture(autouse=True)
def reset_paused():
    """Reset pause state between tests."""
    handlers_module._paused = False
    yield
    handlers_module._paused = False


# ---------------------------------------------------------------------------
# stop all
# ---------------------------------------------------------------------------


class TestStopAll:
    @pytest.mark.anyio
    async def test_stop_all_kills_sessions(self, client, fresh_storage):
        with patch("anchovies.handlers.get_session_manager") as mock_mgr, \
             patch("anchovies.handlers.get_tmux_manager") as mock_tmux:
            mgr = MagicMock()
            mgr.active_sessions = {
                "sofia": WorkSession(member="sofia", task_description="t"),
                "leo": WorkSession(member="leo", task_description="t"),
            }
            mgr.storage = fresh_storage
            mock_mgr.return_value = mgr
            mock_tmux.return_value = MagicMock()

            result = await _handle_control_command(client, "C1", "T1", "stop all")

        assert result is True
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "stopped" in text.lower()

    @pytest.mark.anyio
    async def test_stop_all_clears_queue(self, client, fresh_storage):
        from anchovies.task_queue import get_task_queue, QueuedTask, reset_task_queue
        reset_task_queue()
        queue = get_task_queue()
        queue.enqueue(QueuedTask(member="james", task_description="t", task_prompt="p"))

        with patch("anchovies.handlers.get_session_manager") as mock_mgr, \
             patch("anchovies.handlers.get_tmux_manager"):
            mgr = MagicMock()
            mgr.active_sessions = {}
            mgr.storage = fresh_storage
            mock_mgr.return_value = mgr

            await _handle_control_command(client, "C1", "T1", "stop all")

        assert queue.is_empty
        reset_task_queue()


# ---------------------------------------------------------------------------
# stop <name>
# ---------------------------------------------------------------------------


class TestStopSpecific:
    @pytest.mark.anyio
    async def test_stop_active_session(self, client, fresh_storage):
        with patch("anchovies.handlers.get_session_manager") as mock_mgr, \
             patch("anchovies.handlers.get_tmux_manager"):
            mgr = MagicMock()
            mgr.active_sessions = {
                "sofia": WorkSession(member="sofia", task_description="t"),
            }
            mgr.storage = fresh_storage
            mock_mgr.return_value = mgr

            result = await _handle_control_command(client, "C1", "T1", "stop sofia")

        assert result is True
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "Sofia" in text

    @pytest.mark.anyio
    async def test_stop_nonexistent_session(self, client, fresh_storage):
        with patch("anchovies.handlers.get_session_manager") as mock_mgr:
            mgr = MagicMock()
            mgr.active_sessions = {}
            mgr.storage = fresh_storage
            mock_mgr.return_value = mgr

            from anchovies.task_queue import reset_task_queue
            reset_task_queue()

            result = await _handle_control_command(client, "C1", "T1", "stop sofia")

        assert result is True
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "no active" in text.lower()
        reset_task_queue()

    @pytest.mark.anyio
    async def test_stop_unknown_name_falls_through(self, client):
        """If the name isn't a team member, don't handle it (could be normal chat)."""
        result = await _handle_control_command(client, "C1", "T1", "stop worrying")
        assert result is False


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    @pytest.mark.anyio
    async def test_pause(self, client):
        result = await _handle_control_command(client, "C1", "T1", "pause")
        assert result is True
        assert handlers_module._paused is True
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "paused" in text.lower()

    @pytest.mark.anyio
    async def test_resume(self, client):
        handlers_module._paused = True
        result = await _handle_control_command(client, "C1", "T1", "resume")
        assert result is True
        assert handlers_module._paused is False
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "resumed" in text.lower()


# ---------------------------------------------------------------------------
# daily summary
# ---------------------------------------------------------------------------


class TestDailySummary:
    @pytest.mark.anyio
    async def test_daily_summary_basic(self, client, fresh_storage):
        with patch("anchovies.handlers.get_session_manager") as mock_mgr, \
             patch("anchovies.task_queue.get_task_queue") as mock_queue:
            mgr = MagicMock()
            mgr.active_sessions = {}
            mgr.storage = fresh_storage
            mgr.list_sessions.return_value = []
            mock_mgr.return_value = mgr
            q = MagicMock()
            q.size = 0
            mock_queue.return_value = q

            result = await _handle_control_command(client, "C1", "T1", "daily summary")

        assert result is True
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "Summary" in text
        assert "$" in text  # cost line

    @pytest.mark.anyio
    async def test_summary_alias(self, client, fresh_storage):
        with patch("anchovies.handlers.get_session_manager") as mock_mgr, \
             patch("anchovies.task_queue.get_task_queue") as mock_queue:
            mgr = MagicMock()
            mgr.active_sessions = {}
            mgr.storage = fresh_storage
            mgr.list_sessions.return_value = []
            mock_mgr.return_value = mgr
            q = MagicMock()
            q.size = 0
            mock_queue.return_value = q

            result = await _handle_control_command(client, "C1", "T1", "summary")

        assert result is True


# ---------------------------------------------------------------------------
# Not a control command
# ---------------------------------------------------------------------------


class TestNotControlCommand:
    @pytest.mark.anyio
    async def test_normal_message_falls_through(self, client):
        result = await _handle_control_command(client, "C1", "T1", "hey marcus how are you")
        assert result is False

    @pytest.mark.anyio
    async def test_fix_bug_falls_through(self, client):
        result = await _handle_control_command(client, "C1", "T1", "fix the bug in app.py")
        assert result is False


# ---------------------------------------------------------------------------
# Integration check
# ---------------------------------------------------------------------------


class TestControlIntegration:
    def test_handler_calls_control_command(self):
        source = inspect.getsource(handlers_module.handle_team_message)
        assert "_handle_control_command" in source

    def test_handler_checks_paused(self):
        source = inspect.getsource(handlers_module.handle_team_message)
        assert "_paused" in source
