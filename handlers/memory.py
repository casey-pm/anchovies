"""
Conversation memory management.

Handles in-memory conversation store with LRU eviction and 24-hour cleanup.
Thread-safe OrderedDict keyed by Slack thread_ts.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Limits
MAX_THREADS = 500
MAX_MESSAGES_PER_THREAD = 200
THREAD_TTL_SECONDS = 86400  # 24 hours

# In-memory conversation store (thread_ts -> list of messages)
conversation_store: OrderedDict[str, list[dict]] = OrderedDict()
_thread_last_accessed: dict[str, float] = {}
_message_counter: int = 0


def get_conversation_history(thread_ts: str) -> list[dict]:
    """Get conversation history for a thread, updating LRU position."""
    if thread_ts in conversation_store:
        conversation_store.move_to_end(thread_ts)
        _thread_last_accessed[thread_ts] = time.time()
    return conversation_store.get(thread_ts, [])


def add_to_conversation(thread_ts: str, role: str, content: str, member: str = ""):
    """Add a message to conversation history with LRU eviction."""
    global _message_counter

    if thread_ts not in conversation_store:
        while len(conversation_store) >= MAX_THREADS:
            evicted_ts, _ = conversation_store.popitem(last=False)
            _thread_last_accessed.pop(evicted_ts, None)
            logger.debug(f"LRU evicted thread {evicted_ts}")
        conversation_store[thread_ts] = []

    conversation_store[thread_ts].append({
        "role": role,
        "content": content,
        "member": member,
    })

    conversation_store.move_to_end(thread_ts)
    _thread_last_accessed[thread_ts] = time.time()

    if len(conversation_store[thread_ts]) > MAX_MESSAGES_PER_THREAD:
        conversation_store[thread_ts] = conversation_store[thread_ts][-MAX_MESSAGES_PER_THREAD:]

    _message_counter += 1
    if _message_counter % 100 == 0:
        _cleanup_old_threads()


def _cleanup_old_threads():
    """Remove threads not accessed in the last 24 hours."""
    now = time.time()
    expired = [
        ts for ts, last in _thread_last_accessed.items()
        if now - last > THREAD_TTL_SECONDS
    ]
    for ts in expired:
        conversation_store.pop(ts, None)
        _thread_last_accessed.pop(ts, None)
    if expired:
        logger.info(f"Cleaned up {len(expired)} expired threads")
