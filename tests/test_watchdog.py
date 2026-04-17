"""Tests for the watchdog background task."""

import asyncio
import inspect
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anchovies.watchdog import _run_checks, watchdog_loop
from anchovies.storage import Storage, reset_storage
from anchovies.work_sessions.session_manager import WorkSession


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


class TestRunChecks:
    @pytest.mark.anyio
    async def test_no_alerts_when_clean(self, fresh_storage):
        """No active sessions, no budget issues = no alerts."""
        with patch("anchovies.work_sessions.get_session_manager") as mock_mgr, \
             patch("anchovies.task_queue.get_task_queue") as mock_queue:
            mgr = MagicMock()
            mgr.active_sessions = {}
            mgr.auto_close_timed_out.return_value = []
            mock_mgr.return_value = mgr
            q = MagicMock()
            q.size = 0
            mock_queue.return_value = q

            alerts = await _run_checks()
        assert alerts == []

    @pytest.mark.anyio
    async def test_alert_on_timeout_close(self, fresh_storage):
        """Closed sessions generate alerts."""
        with patch("anchovies.work_sessions.get_session_manager") as mock_mgr, \
             patch("anchovies.task_queue.get_task_queue") as mock_queue:
            mgr = MagicMock()
            mgr.active_sessions = {}
            mgr.auto_close_timed_out.return_value = ["sofia"]
            mock_mgr.return_value = mgr
            q = MagicMock()
            q.size = 0
            mock_queue.return_value = q

            alerts = await _run_checks()

        assert len(alerts) >= 1
        assert "Sofia" in alerts[0]

    @pytest.mark.anyio
    async def test_alert_on_budget_warning(self, fresh_storage, monkeypatch):
        """Budget at 80%+ triggers an alert."""
        with patch("anchovies.work_sessions.get_session_manager") as mock_mgr, \
             patch("anchovies.task_queue.get_task_queue") as mock_queue:
            mgr = MagicMock()
            mgr.active_sessions = {}
            mgr.auto_close_timed_out.return_value = []
            mock_mgr.return_value = mgr
            q = MagicMock()
            q.size = 0
            mock_queue.return_value = q

            monkeypatch.setattr("anchovies.cost_tracking.is_budget_warning", lambda: True)
            monkeypatch.setattr("anchovies.cost_tracking.get_today_spend", lambda: (20.0, 50))
            monkeypatch.setattr("anchovies.cost_tracking.DAILY_BUDGET_USD", 25.0)

            alerts = await _run_checks()

        assert any("budget" in a.lower() for a in alerts)

    @pytest.mark.anyio
    async def test_alert_on_long_queued_task(self, fresh_storage):
        """Tasks waiting > 5 min trigger an alert."""
        with patch("anchovies.work_sessions.get_session_manager") as mock_mgr, \
             patch("anchovies.task_queue.get_task_queue") as mock_queue:
            mgr = MagicMock()
            mgr.active_sessions = {}
            mgr.auto_close_timed_out.return_value = []
            mock_mgr.return_value = mgr

            from anchovies.task_queue import QueuedTask
            import time
            old_task = QueuedTask(member="james", task_description="t", task_prompt="p",
                                  queued_at=time.time() - 400)  # 6+ min ago
            q = MagicMock()
            q.size = 1
            q.peek.return_value = old_task
            mock_queue.return_value = q

            alerts = await _run_checks()

        assert any("queued" in a.lower() for a in alerts)


class TestWatchdogLoop:
    @pytest.mark.anyio
    async def test_loop_stops_on_shutdown(self):
        """The loop should exit when shutdown_event is set."""
        shutdown = asyncio.Event()
        client = AsyncMock()

        # Set shutdown immediately so the loop exits after one sleep
        async def set_soon():
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(set_soon())

        # Should not hang — loop exits within a few seconds
        await asyncio.wait_for(
            watchdog_loop(client, shutdown, interval=1),
            timeout=5,
        )

    @pytest.mark.anyio
    async def test_loop_handles_cancellation(self):
        """The loop should handle CancelledError gracefully."""
        shutdown = asyncio.Event()
        client = AsyncMock()

        task = asyncio.create_task(watchdog_loop(client, shutdown, interval=1))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected


class TestAppIntegration:
    def test_watchdog_started_in_async_main(self):
        from anchovies import app
        source = inspect.getsource(app.async_main)
        assert "watchdog_loop" in source

    def test_watchdog_cancelled_on_shutdown(self):
        from anchovies import app
        source = inspect.getsource(app.async_main)
        assert "watchdog_task.cancel" in source
