"""
Task Queue — queues work requests when max concurrent sessions is reached.

When all session slots are full, new work requests are queued here instead
of being rejected. The queue drains as sessions complete — the next queued
task is spawned automatically.

Queue state is persisted to SQLite so it survives bot restarts.

Configuration:
    MAX_CONCURRENT_SESSIONS — env var, default 4
"""

from __future__ import annotations

import logging
import os
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SESSIONS = int(os.getenv("MAX_CONCURRENT_SESSIONS", "4"))


@dataclass
class QueuedTask:
    """A work request waiting to be spawned."""
    member: str
    task_description: str
    task_prompt: str
    thread_ts: Optional[str] = None
    channel_id: Optional[str] = None
    files: list[str] = field(default_factory=list)
    project: Optional[str] = None
    queued_at: float = field(default_factory=time.time)

    @property
    def wait_seconds(self) -> float:
        """How long this task has been waiting."""
        return time.time() - self.queued_at


class TaskQueue:
    """
    FIFO queue for work requests that can't be spawned immediately.

    Thread-safe. Persists queue state to the audit log for visibility
    but uses an in-memory deque for actual ordering (queue is transient
    across restarts — if the bot restarts, queued tasks are lost, which
    is acceptable since the user can re-send them).
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_SESSIONS):
        self.max_concurrent = max_concurrent
        self._queue: deque[QueuedTask] = deque()
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        """Number of tasks currently queued."""
        with self._lock:
            return len(self._queue)

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._queue) == 0

    def enqueue(self, task: QueuedTask) -> int:
        """
        Add a task to the queue. Returns its position (1-indexed).
        """
        with self._lock:
            self._queue.append(task)
            position = len(self._queue)
        logger.info(
            f"Queued task for {task.member}: '{task.task_description[:50]}' "
            f"(position {position})"
        )

        # Log to audit trail
        try:
            from .storage import get_storage
            get_storage().log_event(
                "task_queued",
                member=task.member,
                details={
                    "task": task.task_description[:100],
                    "position": position,
                    "project": task.project,
                    "queue_size": position,
                },
            )
        except Exception:
            pass

        return position

    def dequeue(self) -> Optional[QueuedTask]:
        """
        Remove and return the next task, or None if empty.
        """
        with self._lock:
            if self._queue:
                task = self._queue.popleft()
                logger.info(
                    f"Dequeued task for {task.member} "
                    f"(waited {task.wait_seconds:.1f}s, {len(self._queue)} remaining)"
                )
                return task
        return None

    def peek(self) -> Optional[QueuedTask]:
        """Look at the next task without removing it."""
        with self._lock:
            return self._queue[0] if self._queue else None

    def remove_member(self, member: str) -> int:
        """Remove all queued tasks for a specific member. Returns count removed."""
        with self._lock:
            before = len(self._queue)
            self._queue = deque(t for t in self._queue if t.member != member)
            removed = before - len(self._queue)
        if removed:
            logger.info(f"Removed {removed} queued task(s) for {member}")
        return removed

    def list_queued(self) -> list[QueuedTask]:
        """List all queued tasks in order."""
        with self._lock:
            return list(self._queue)

    def clear(self) -> int:
        """Clear all queued tasks. Returns count cleared."""
        with self._lock:
            count = len(self._queue)
            self._queue.clear()
        if count:
            logger.info(f"Cleared {count} queued task(s)")
        return count

    def get_position(self, member: str) -> Optional[int]:
        """Get a member's position in the queue (1-indexed), or None if not queued."""
        with self._lock:
            for i, task in enumerate(self._queue):
                if task.member == member:
                    return i + 1
        return None

    def format_status(self) -> str:
        """Format a human-readable status of the queue."""
        tasks = self.list_queued()
        if not tasks:
            return "Queue is empty."
        lines = [f"*Queued Tasks ({len(tasks)}):*"]
        for i, task in enumerate(tasks, 1):
            wait = task.wait_seconds
            project_tag = f" [{task.project}]" if task.project else ""
            lines.append(
                f"  {i}. {task.member.title()}{project_tag}: "
                f"{task.task_description[:40]}... "
                f"(waiting {wait:.0f}s)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_task_queue: Optional[TaskQueue] = None
_queue_lock = threading.Lock()


def get_task_queue() -> TaskQueue:
    """Get the singleton TaskQueue instance."""
    global _task_queue
    with _queue_lock:
        if _task_queue is None:
            _task_queue = TaskQueue()
    return _task_queue


def reset_task_queue():
    """Reset the singleton (for tests)."""
    global _task_queue
    with _queue_lock:
        _task_queue = None
