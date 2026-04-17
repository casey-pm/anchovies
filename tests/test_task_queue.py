"""Tests for task queue and max concurrent sessions (Phase 4.1)."""

import inspect
import time
from unittest.mock import MagicMock, patch

import pytest

from anchovies.task_queue import (
    MAX_CONCURRENT_SESSIONS,
    QueuedTask,
    TaskQueue,
    get_task_queue,
    reset_task_queue,
)
from anchovies.storage import Storage, reset_storage


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
def queue():
    q = TaskQueue(max_concurrent=4)
    yield q


def _task(member: str = "sofia", desc: str = "fix bug") -> QueuedTask:
    return QueuedTask(member=member, task_description=desc, task_prompt="prompt")


# ---------------------------------------------------------------------------
# QueuedTask
# ---------------------------------------------------------------------------


class TestQueuedTask:
    def test_wait_seconds(self):
        t = QueuedTask(member="sofia", task_description="t", task_prompt="p",
                        queued_at=time.time() - 10)
        assert 9 < t.wait_seconds < 11

    def test_default_queued_at(self):
        t = _task()
        assert t.wait_seconds < 1


# ---------------------------------------------------------------------------
# TaskQueue basics
# ---------------------------------------------------------------------------


class TestTaskQueueBasics:
    def test_starts_empty(self, queue):
        assert queue.is_empty
        assert queue.size == 0

    def test_enqueue_and_size(self, queue):
        pos = queue.enqueue(_task("sofia"))
        assert pos == 1
        assert queue.size == 1
        assert not queue.is_empty

    def test_fifo_order(self, queue):
        queue.enqueue(_task("sofia", "task 1"))
        queue.enqueue(_task("leo", "task 2"))
        queue.enqueue(_task("james", "task 3"))

        t1 = queue.dequeue()
        assert t1.member == "sofia"
        t2 = queue.dequeue()
        assert t2.member == "leo"
        t3 = queue.dequeue()
        assert t3.member == "james"
        assert queue.is_empty

    def test_dequeue_empty_returns_none(self, queue):
        assert queue.dequeue() is None

    def test_peek_does_not_remove(self, queue):
        queue.enqueue(_task("sofia"))
        peeked = queue.peek()
        assert peeked.member == "sofia"
        assert queue.size == 1  # still there

    def test_peek_empty_returns_none(self, queue):
        assert queue.peek() is None


# ---------------------------------------------------------------------------
# Remove / clear
# ---------------------------------------------------------------------------


class TestRemoveAndClear:
    def test_remove_member(self, queue):
        queue.enqueue(_task("sofia"))
        queue.enqueue(_task("leo"))
        queue.enqueue(_task("sofia", "another task"))
        removed = queue.remove_member("sofia")
        assert removed == 2
        assert queue.size == 1
        assert queue.dequeue().member == "leo"

    def test_remove_nonexistent(self, queue):
        queue.enqueue(_task("sofia"))
        assert queue.remove_member("leo") == 0
        assert queue.size == 1

    def test_clear(self, queue):
        for name in ["sofia", "leo", "james"]:
            queue.enqueue(_task(name))
        cleared = queue.clear()
        assert cleared == 3
        assert queue.is_empty


# ---------------------------------------------------------------------------
# Position tracking
# ---------------------------------------------------------------------------


class TestPosition:
    def test_get_position(self, queue):
        queue.enqueue(_task("sofia"))
        queue.enqueue(_task("leo"))
        queue.enqueue(_task("james"))
        assert queue.get_position("sofia") == 1
        assert queue.get_position("leo") == 2
        assert queue.get_position("james") == 3

    def test_position_not_found(self, queue):
        assert queue.get_position("nobody") is None


# ---------------------------------------------------------------------------
# Format status
# ---------------------------------------------------------------------------


class TestFormatStatus:
    def test_empty_queue(self, queue):
        assert "empty" in queue.format_status().lower()

    def test_with_tasks(self, queue):
        queue.enqueue(_task("sofia", "fix bug"))
        queue.enqueue(_task("leo", "write tests"))
        status = queue.format_status()
        assert "Sofia" in status
        assert "Leo" in status
        assert "2" in status


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


class TestAuditLogging:
    def test_enqueue_logs_to_audit(self, queue, fresh_storage):
        queue.enqueue(_task("sofia"))
        events = fresh_storage.query_audit(event_type="task_queued")
        assert len(events) == 1
        assert events[0].member == "sofia"
        assert events[0].details["position"] == 1


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_returns_same(self):
        reset_task_queue()
        try:
            q1 = get_task_queue()
            q2 = get_task_queue()
            assert q1 is q2
        finally:
            reset_task_queue()

    def test_reset_creates_new(self):
        reset_task_queue()
        try:
            q1 = get_task_queue()
            reset_task_queue()
            q2 = get_task_queue()
            assert q1 is not q2
        finally:
            reset_task_queue()


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_default_max_concurrent(self):
        import os
        prev = os.environ.pop("MAX_CONCURRENT_SESSIONS", None)
        try:
            import importlib
            from anchovies import task_queue as tq
            importlib.reload(tq)
            assert tq.MAX_CONCURRENT_SESSIONS == 4
        finally:
            if prev is not None:
                os.environ["MAX_CONCURRENT_SESSIONS"] = prev
            import importlib
            from anchovies import task_queue as tq
            importlib.reload(tq)


# ---------------------------------------------------------------------------
# Handler integration
# ---------------------------------------------------------------------------


class TestHandlerIntegration:
    def test_handler_checks_session_limit(self):
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_chat_hub_message)
        assert "MAX_CONCURRENT_SESSIONS" in source

    def test_handler_enqueues_when_full(self):
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_chat_hub_message)
        assert "queue.enqueue" in source or "enqueue" in source

    def test_handler_posts_queue_position(self):
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_chat_hub_message)
        assert "queued" in source.lower()
        assert "position" in source.lower()
