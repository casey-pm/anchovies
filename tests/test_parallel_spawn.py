"""Tests for parallel session spawning and queue draining."""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anchovies.task_queue import QueuedTask, TaskQueue, reset_task_queue
from anchovies.storage import Storage, reset_storage
from anchovies.work_sessions.session_manager import SessionManager


@pytest.fixture
def fresh_storage(tmp_path, monkeypatch):
    reset_storage()
    storage = Storage(tmp_path / "test.db")
    import anchovies.storage as sm
    monkeypatch.setattr(sm, "_storage", storage)
    yield storage
    storage.close()
    reset_storage()


@pytest.fixture
def session_mgr(fresh_storage):
    with patch("anchovies.work_sessions.session_manager.get_tmux_manager") as mock_get:
        mock_tmux = MagicMock()
        mock_tmux.session_name = "anchovies"
        mock_tmux.session_exists = MagicMock(return_value=True)
        mock_tmux.persona_tab_exists = MagicMock(return_value=False)
        mock_tmux.close_persona_tab = MagicMock(return_value=True)
        mock_tmux._get_pane_command = MagicMock(return_value="claude")
        mock_tmux.get_pane_content = MagicMock(return_value="> ")

        async def fake_spawn(member, prompt, working_dir=None):
            return True
        mock_tmux.spawn_persona_tab = fake_spawn

        mock_get.return_value = mock_tmux
        mgr = SessionManager()
        mgr.tmux = mock_tmux
        yield mgr


@pytest.fixture(autouse=True)
def clean_queue():
    reset_task_queue()
    yield
    reset_task_queue()


def _task(member="sofia", desc="fix bug"):
    return QueuedTask(member=member, task_description=desc, task_prompt="prompt")


class TestDrainQueue:
    @pytest.mark.anyio
    async def test_drain_empty_queue(self, session_mgr):
        spawned = await session_mgr.drain_queue()
        assert spawned == []

    @pytest.mark.anyio
    async def test_drain_one_task(self, session_mgr):
        from anchovies.task_queue import get_task_queue
        queue = get_task_queue()
        queue.enqueue(_task("sofia", "fix dbt model"))

        spawned = await session_mgr.drain_queue()
        assert spawned == ["sofia"]
        assert "sofia" in session_mgr.active_sessions
        assert queue.is_empty

    @pytest.mark.anyio
    async def test_drain_multiple_tasks(self, session_mgr):
        from anchovies.task_queue import get_task_queue
        queue = get_task_queue()
        queue.enqueue(_task("sofia"))
        queue.enqueue(_task("leo"))
        queue.enqueue(_task("james"))

        spawned = await session_mgr.drain_queue()
        assert len(spawned) == 3
        assert queue.is_empty

    @pytest.mark.anyio
    async def test_drain_respects_max_concurrent(self, session_mgr):
        """Only spawns up to available slots."""
        from anchovies.task_queue import get_task_queue
        queue = get_task_queue()

        # Fill 3 of 4 slots manually
        from anchovies.work_sessions.session_manager import WorkSession
        for name in ["existing1", "existing2", "existing3"]:
            session_mgr.active_sessions[name] = WorkSession(member=name, task_description="t")

        # Queue 3 tasks
        queue.enqueue(_task("sofia"))
        queue.enqueue(_task("leo"))
        queue.enqueue(_task("james"))

        # Only 1 slot available (4 max - 3 active)
        spawned = await session_mgr.drain_queue()
        assert len(spawned) == 1
        assert queue.size == 2  # 2 still queued

    @pytest.mark.anyio
    async def test_drain_at_full_capacity(self, session_mgr):
        """No spawning when all slots are full."""
        from anchovies.task_queue import get_task_queue
        from anchovies.work_sessions.session_manager import WorkSession
        queue = get_task_queue()

        # Fill all 4 slots
        for i in range(4):
            session_mgr.active_sessions[f"member{i}"] = WorkSession(member=f"member{i}", task_description="t")

        queue.enqueue(_task("sofia"))
        spawned = await session_mgr.drain_queue()
        assert spawned == []
        assert queue.size == 1  # still queued


class TestWatchdogDrain:
    def test_watchdog_calls_drain_queue(self):
        from anchovies import watchdog
        source = inspect.getsource(watchdog._run_checks)
        assert "drain_queue" in source


class TestDrainQueueMethod:
    def test_method_exists(self):
        assert hasattr(SessionManager, "drain_queue")

    def test_method_is_async(self):
        assert inspect.iscoroutinefunction(SessionManager.drain_queue)
