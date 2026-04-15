"""Tests for the SQLite storage layer."""

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from anchovies.storage import (
    AuditEntry,
    Storage,
    StoredSession,
    get_storage,
    reset_storage,
)


@pytest.fixture
def storage(tmp_path):
    """Create a fresh Storage instance in a temp directory."""
    db_path = tmp_path / "test.db"
    store = Storage(db_path)
    yield store
    store.close()


# ---------------------------------------------------------------------------
# Schema / initialization
# ---------------------------------------------------------------------------


class TestInitialization:
    def test_db_file_created(self, tmp_path):
        db_path = tmp_path / "new.db"
        assert not db_path.exists()
        Storage(db_path).close()
        assert db_path.exists()

    def test_parent_dir_created(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "test.db"
        assert not nested.parent.exists()
        Storage(nested).close()
        assert nested.parent.exists()
        assert nested.exists()

    def test_tables_created(self, storage):
        """All four tables should exist after initialization."""
        with storage._lock:
            rows = storage._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        names = {r["name"] for r in rows}
        assert "conversations" in names
        assert "sessions" in names
        assert "audit_log" in names
        assert "budget" in names

    def test_wal_mode_enabled(self, storage):
        """SQLite should be using WAL mode for better concurrency."""
        with storage._lock:
            mode = storage._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_init_idempotent(self, tmp_path):
        """Running Storage twice on the same DB should not error."""
        db_path = tmp_path / "test.db"
        Storage(db_path).close()
        # Second instance should open the existing DB cleanly
        Storage(db_path).close()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class TestConversations:
    def test_save_and_load_roundtrip(self, storage):
        messages = [
            {"role": "user", "content": "hello", "member": ""},
            {"role": "assistant", "content": "hi there", "member": "marcus"},
        ]
        storage.save_conversation("1234.5678", messages)

        loaded = storage.load_conversation("1234.5678")
        assert loaded == messages

    def test_load_missing_returns_empty(self, storage):
        assert storage.load_conversation("nonexistent") == []

    def test_save_overwrites_existing(self, storage):
        storage.save_conversation("t1", [{"role": "user", "content": "old", "member": ""}])
        new_messages = [{"role": "user", "content": "new", "member": ""}]
        storage.save_conversation("t1", new_messages)
        assert storage.load_conversation("t1") == new_messages

    def test_delete_conversation(self, storage):
        storage.save_conversation("t1", [{"role": "user", "content": "hello", "member": ""}])
        storage.delete_conversation("t1")
        assert storage.load_conversation("t1") == []

    def test_list_conversations_ordered_by_recency(self, storage):
        storage.save_conversation("old", [{"role": "user", "content": "old", "member": ""}])
        time.sleep(0.01)
        storage.save_conversation("middle", [{"role": "user", "content": "middle", "member": ""}])
        time.sleep(0.01)
        storage.save_conversation("new", [{"role": "user", "content": "new", "member": ""}])

        convs = storage.list_conversations()
        assert [c[0] for c in convs] == ["new", "middle", "old"]

    def test_list_with_limit(self, storage):
        for i in range(5):
            storage.save_conversation(f"t{i}", [{"role": "user", "content": f"m{i}", "member": ""}])

        convs = storage.list_conversations(limit=3)
        assert len(convs) == 3

    def test_cleanup_old_conversations(self, storage):
        # Save a conversation then backdate its last_accessed
        storage.save_conversation("old", [{"role": "user", "content": "old", "member": ""}])
        storage.save_conversation("recent", [{"role": "user", "content": "recent", "member": ""}])

        # Backdate "old" to 25 hours ago
        old_time = time.time() - (25 * 3600)
        with storage._lock:
            storage._conn.execute(
                "UPDATE conversations SET last_accessed = ? WHERE thread_ts = ?",
                (old_time, "old"),
            )
            storage._conn.commit()

        deleted = storage.cleanup_old_conversations(ttl_seconds=86400)
        assert deleted == 1
        assert storage.load_conversation("old") == []
        assert storage.load_conversation("recent") != []

    def test_special_characters_in_messages(self, storage):
        """Messages with quotes, newlines, and special chars round-trip correctly."""
        messages = [
            {"role": "user", "content": 'He said "hello"\nthen left', "member": ""},
            {"role": "assistant", "content": "Backslash: \\ and unicode: \u2714", "member": "marcus"},
        ]
        storage.save_conversation("t1", messages)
        assert storage.load_conversation("t1") == messages


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class TestSessions:
    def test_save_and_load_session(self, storage):
        sess = StoredSession(
            member="sofia",
            task_description="fix the null bug",
            status="active",
            started_at=time.time(),
            last_activity=time.time(),
            thread_ts="1234.5678",
            channel_id="C_TEST",
        )
        storage.save_session(sess)

        loaded = storage.load_session("sofia")
        assert loaded is not None
        assert loaded.member == "sofia"
        assert loaded.task_description == "fix the null bug"
        assert loaded.status == "active"
        assert loaded.thread_ts == "1234.5678"

    def test_load_missing_returns_none(self, storage):
        assert storage.load_session("nobody") is None

    def test_save_updates_existing(self, storage):
        sess = StoredSession(
            member="sofia", task_description="task 1", status="active",
            started_at=time.time(), last_activity=time.time(),
        )
        storage.save_session(sess)

        # Update with new status
        sess.status = "completed"
        sess.status_updated = True
        storage.save_session(sess)

        loaded = storage.load_session("sofia")
        assert loaded.status == "completed"
        assert loaded.status_updated is True

    def test_list_sessions_all(self, storage):
        for name in ["sofia", "leo", "james"]:
            storage.save_session(StoredSession(
                member=name, task_description=f"task {name}", status="active",
                started_at=time.time(), last_activity=time.time(),
            ))

        sessions = storage.list_sessions()
        assert len(sessions) == 3
        assert {s.member for s in sessions} == {"sofia", "leo", "james"}

    def test_list_sessions_by_status(self, storage):
        storage.save_session(StoredSession(
            member="active1", task_description="t", status="active",
            started_at=time.time(), last_activity=time.time(),
        ))
        storage.save_session(StoredSession(
            member="done1", task_description="t", status="completed",
            started_at=time.time(), last_activity=time.time(),
        ))
        storage.save_session(StoredSession(
            member="crashed1", task_description="t", status="crashed",
            started_at=time.time(), last_activity=time.time(),
        ))

        active = storage.list_sessions(status="active")
        assert len(active) == 1
        assert active[0].member == "active1"

    def test_delete_session(self, storage):
        storage.save_session(StoredSession(
            member="sofia", task_description="t", status="active",
            started_at=time.time(), last_activity=time.time(),
        ))
        storage.delete_session("sofia")
        assert storage.load_session("sofia") is None

    def test_mark_session_status(self, storage):
        storage.save_session(StoredSession(
            member="sofia", task_description="t", status="active",
            started_at=time.time(), last_activity=time.time(),
        ))
        storage.mark_session_status("sofia", "crashed")
        loaded = storage.load_session("sofia")
        assert loaded.status == "crashed"

    def test_boolean_fields_preserved(self, storage):
        sess = StoredSession(
            member="sofia", task_description="t", status="active",
            started_at=time.time(), last_activity=time.time(),
            status_updated=True, slack_posted=True, close_prompt_shown=False,
        )
        storage.save_session(sess)
        loaded = storage.load_session("sofia")
        assert loaded.status_updated is True
        assert loaded.slack_posted is True
        assert loaded.close_prompt_shown is False


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_log_event_basic(self, storage):
        eid = storage.log_event("session_started", "sofia", {"task": "fix bug"})
        assert eid > 0

    def test_log_without_member(self, storage):
        """Events like 'bot_started' don't have a member."""
        eid = storage.log_event("bot_started", details={"version": "0.1"})
        assert eid > 0

        entries = storage.query_audit(event_type="bot_started")
        assert entries[0].member is None
        assert entries[0].details == {"version": "0.1"}

    def test_query_all(self, storage):
        storage.log_event("session_started", "sofia")
        storage.log_event("session_completed", "sofia")
        storage.log_event("session_started", "leo")

        entries = storage.query_audit()
        assert len(entries) == 3

    def test_query_newest_first(self, storage):
        storage.log_event("first", "sofia")
        time.sleep(0.01)
        storage.log_event("second", "sofia")
        time.sleep(0.01)
        storage.log_event("third", "sofia")

        entries = storage.query_audit()
        assert entries[0].event_type == "third"
        assert entries[-1].event_type == "first"

    def test_query_by_member(self, storage):
        storage.log_event("started", "sofia")
        storage.log_event("started", "leo")
        storage.log_event("completed", "sofia")

        sofia_entries = storage.query_audit(member="sofia")
        assert len(sofia_entries) == 2
        assert all(e.member == "sofia" for e in sofia_entries)

    def test_query_by_event_type(self, storage):
        storage.log_event("started", "sofia")
        storage.log_event("started", "leo")
        storage.log_event("crashed", "sofia")

        started = storage.query_audit(event_type="started")
        assert len(started) == 2

    def test_query_since(self, storage):
        storage.log_event("old", "sofia")
        time.sleep(0.05)
        cutoff = time.time()
        time.sleep(0.05)
        storage.log_event("new", "sofia")

        recent = storage.query_audit(since=cutoff)
        assert len(recent) == 1
        assert recent[0].event_type == "new"

    def test_query_limit(self, storage):
        for i in range(20):
            storage.log_event(f"event_{i}", "sofia")

        entries = storage.query_audit(limit=5)
        assert len(entries) == 5

    def test_details_json_roundtrip(self, storage):
        complex_details = {
            "files": ["app.py", "handler.py"],
            "nested": {"key": "value", "count": 42},
            "unicode": "\u2714",
        }
        storage.log_event("complex", "sofia", complex_details)
        entry = storage.query_audit(event_type="complex")[0]
        assert entry.details == complex_details


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class TestBudget:
    def test_starts_at_zero(self, storage):
        total, calls = storage.get_budget()
        assert total == 0.0
        assert calls == 0

    def test_add_cost_accumulates(self, storage):
        storage.add_cost(0.50, 1)
        storage.add_cost(0.25, 1)
        storage.add_cost(1.00, 2)

        total, calls = storage.get_budget()
        assert total == pytest.approx(1.75)
        assert calls == 4

    def test_budget_exceeded(self, storage):
        storage.add_cost(24.00, 1)
        assert storage.budget_exceeded(daily_cap=25.00) is False

        storage.add_cost(2.00, 1)
        assert storage.budget_exceeded(daily_cap=25.00) is True

    def test_budget_per_day_isolated(self, storage):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        storage.add_cost(5.00, 1, day=today)
        storage.add_cost(10.00, 2, day=yesterday)

        today_total, _ = storage.get_budget(day=today)
        yesterday_total, _ = storage.get_budget(day=yesterday)

        assert today_total == pytest.approx(5.00)
        assert yesterday_total == pytest.approx(10.00)

    def test_exceeded_only_counts_today(self, storage):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        storage.add_cost(100.00, 1, day=yesterday)

        # Today's budget is still 0
        assert storage.budget_exceeded(daily_cap=25.00) is False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_storage_returns_same_instance(self, tmp_path):
        reset_storage()
        try:
            db1 = tmp_path / "singleton.db"
            s1 = get_storage(db1)
            s2 = get_storage(db1)
            assert s1 is s2
        finally:
            reset_storage()

    def test_reset_creates_new_instance(self, tmp_path):
        reset_storage()
        try:
            db1 = tmp_path / "reset_test.db"
            s1 = get_storage(db1)
            reset_storage()
            s2 = get_storage(db1)
            assert s1 is not s2
        finally:
            reset_storage()


# ---------------------------------------------------------------------------
# Persistence across reconnects
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_data_survives_reconnect(self, tmp_path):
        db_path = tmp_path / "persist.db"

        # Write data with one connection
        s1 = Storage(db_path)
        s1.save_conversation("t1", [{"role": "user", "content": "hello", "member": ""}])
        s1.save_session(StoredSession(
            member="sofia", task_description="task", status="active",
            started_at=time.time(), last_activity=time.time(),
        ))
        s1.log_event("test_event", "sofia", {"k": "v"})
        s1.add_cost(5.50, 3)
        s1.close()

        # Reopen with fresh connection
        s2 = Storage(db_path)
        assert s2.load_conversation("t1") == [{"role": "user", "content": "hello", "member": ""}]
        assert s2.load_session("sofia").task_description == "task"
        audit = s2.query_audit(event_type="test_event")
        assert len(audit) == 1
        assert audit[0].details == {"k": "v"}
        total, calls = s2.get_budget()
        assert total == pytest.approx(5.50)
        assert calls == 3
        s2.close()
