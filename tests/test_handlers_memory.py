"""Tests for conversation store memory management in handlers.py.

Verifies LRU eviction, per-thread caps, and 24-hour cleanup.
"""

import time
from collections import OrderedDict
from unittest.mock import patch

from anchovies.handlers import (
    add_to_conversation,
    get_conversation_history,
    _cleanup_old_threads,
    MAX_THREADS,
    MAX_MESSAGES_PER_THREAD,
    THREAD_TTL_SECONDS,
)
import anchovies.handlers.memory as handlers_module


class TestConversationLRU:
    """Test LRU eviction when MAX_THREADS is exceeded."""

    def setup_method(self):
        """Reset conversation store before each test."""
        handlers_module.conversation_store = OrderedDict()
        handlers_module._thread_last_accessed = {}
        handlers_module._message_counter = 0

    def test_lru_eviction_at_capacity(self):
        """When MAX_THREADS is reached, the oldest thread is evicted."""
        # Fill to capacity with MAX_THREADS threads
        with patch.object(handlers_module, "MAX_THREADS", 5):
            for i in range(5):
                add_to_conversation(f"thread_{i}", "user", f"msg {i}")

            assert len(handlers_module.conversation_store) == 5

            # Adding a 6th should evict thread_0
            add_to_conversation("thread_new", "user", "new msg")
            assert len(handlers_module.conversation_store) == 5
            assert "thread_0" not in handlers_module.conversation_store
            assert "thread_new" in handlers_module.conversation_store

    def test_lru_access_moves_to_end(self):
        """Accessing a thread moves it to end, protecting from eviction."""
        with patch.object(handlers_module, "MAX_THREADS", 3):
            add_to_conversation("thread_a", "user", "msg a")
            add_to_conversation("thread_b", "user", "msg b")
            add_to_conversation("thread_c", "user", "msg c")

            # Access thread_a (moves it to end)
            get_conversation_history("thread_a")

            # Adding thread_d should evict thread_b (now oldest), not thread_a
            add_to_conversation("thread_d", "user", "msg d")
            assert "thread_a" in handlers_module.conversation_store
            assert "thread_b" not in handlers_module.conversation_store
            assert "thread_d" in handlers_module.conversation_store

    def test_eviction_removes_timestamp_tracking(self):
        """Evicted threads are also removed from _thread_last_accessed."""
        with patch.object(handlers_module, "MAX_THREADS", 2):
            add_to_conversation("thread_old", "user", "old")
            add_to_conversation("thread_new", "user", "new")
            assert "thread_old" in handlers_module._thread_last_accessed

            add_to_conversation("thread_newest", "user", "newest")
            assert "thread_old" not in handlers_module._thread_last_accessed


class TestPerThreadCap:
    """Test per-thread message limit."""

    def setup_method(self):
        handlers_module.conversation_store = OrderedDict()
        handlers_module._thread_last_accessed = {}
        handlers_module._message_counter = 0

    def test_messages_capped_at_limit(self):
        """A thread cannot exceed MAX_MESSAGES_PER_THREAD."""
        for i in range(MAX_MESSAGES_PER_THREAD + 50):
            add_to_conversation("thread_1", "user", f"msg {i}")

        messages = get_conversation_history("thread_1")
        assert len(messages) == MAX_MESSAGES_PER_THREAD

    def test_oldest_messages_trimmed(self):
        """When cap is hit, oldest messages are removed, newest kept."""
        for i in range(MAX_MESSAGES_PER_THREAD + 10):
            add_to_conversation("thread_1", "user", f"msg {i}")

        messages = get_conversation_history("thread_1")
        # The first message should be msg 10 (0-9 trimmed)
        assert messages[0]["content"] == "msg 10"
        assert messages[-1]["content"] == f"msg {MAX_MESSAGES_PER_THREAD + 9}"


class TestThreadCleanup:
    """Test 24-hour expiry cleanup."""

    def setup_method(self):
        handlers_module.conversation_store = OrderedDict()
        handlers_module._thread_last_accessed = {}
        handlers_module._message_counter = 0

    def test_cleanup_removes_expired_threads(self):
        """Threads older than THREAD_TTL_SECONDS are removed."""
        add_to_conversation("thread_old", "user", "old")
        add_to_conversation("thread_new", "user", "new")

        # Backdate thread_old to 25 hours ago
        handlers_module._thread_last_accessed["thread_old"] = time.time() - (25 * 3600)

        _cleanup_old_threads()

        assert "thread_old" not in handlers_module.conversation_store
        assert "thread_new" in handlers_module.conversation_store

    def test_cleanup_keeps_recent_threads(self):
        """Recent threads are not removed."""
        add_to_conversation("thread_recent", "user", "recent")
        _cleanup_old_threads()
        assert "thread_recent" in handlers_module.conversation_store

    def test_periodic_cleanup_triggered(self):
        """Cleanup runs automatically every 100 messages."""
        # Add an old thread
        add_to_conversation("thread_old", "user", "old")
        handlers_module._thread_last_accessed["thread_old"] = time.time() - (25 * 3600)

        # Force counter to 99 so the next add triggers cleanup
        handlers_module._message_counter = 99

        add_to_conversation("thread_trigger", "user", "trigger")

        # thread_old should have been cleaned up
        assert "thread_old" not in handlers_module.conversation_store
        assert "thread_trigger" in handlers_module.conversation_store


class TestGetConversationHistory:
    """Test history retrieval."""

    def setup_method(self):
        handlers_module.conversation_store = OrderedDict()
        handlers_module._thread_last_accessed = {}
        handlers_module._message_counter = 0

    def test_returns_empty_for_unknown_thread(self):
        """Unknown threads return empty list, not KeyError."""
        assert get_conversation_history("nonexistent") == []

    def test_returns_messages_in_order(self):
        """Messages are returned in insertion order."""
        add_to_conversation("t1", "user", "first")
        add_to_conversation("t1", "assistant", "second", "marcus")
        add_to_conversation("t1", "user", "third")

        history = get_conversation_history("t1")
        assert len(history) == 3
        assert history[0]["content"] == "first"
        assert history[1]["content"] == "second"
        assert history[1]["member"] == "marcus"
        assert history[2]["content"] == "third"
