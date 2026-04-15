"""Tests for session crash recovery and pane liveness detection.

Verifies that bot restarts can recover active sessions from SQLite,
detect crashed Claude processes, and clean up orphaned tabs.
"""

import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from anchovies.storage import Storage, StoredSession, reset_storage
from anchovies.work_sessions.session_manager import SessionManager, WorkSession


@pytest.fixture
def fresh_storage(tmp_path, monkeypatch):
    """Create a fresh storage instance for each test."""
    reset_storage()
    db_path = tmp_path / "test.db"
    storage = Storage(db_path)

    # Patch the singleton so get_storage() returns our test instance
    import anchovies.storage as storage_module
    monkeypatch.setattr(storage_module, "_storage", storage)

    yield storage

    storage.close()
    reset_storage()


@pytest.fixture
def session_mgr(fresh_storage):
    """Create a SessionManager with mocked tmux and fresh storage."""
    with patch("anchovies.work_sessions.session_manager.get_tmux_manager") as mock_get_tmux:
        mock_tmux = MagicMock()
        mock_tmux.session_name = "anchovies"
        mock_tmux.session_exists = MagicMock(return_value=True)
        mock_tmux.list_active_tabs = MagicMock(return_value=[])
        mock_tmux.persona_tab_exists = MagicMock(return_value=False)
        mock_tmux.close_persona_tab = MagicMock(return_value=True)
        mock_tmux.get_pane_content = MagicMock(return_value="> ")
        mock_get_tmux.return_value = mock_tmux

        mgr = SessionManager()
        mgr.tmux = mock_tmux
        yield mgr


# ---------------------------------------------------------------------------
# WorkSession <-> StoredSession conversion
# ---------------------------------------------------------------------------


class TestSessionConversion:
    def test_work_session_to_stored(self):
        started = datetime(2026, 4, 15, 10, 0, 0)
        last = datetime(2026, 4, 15, 10, 5, 0)
        ws = WorkSession(
            member="sofia",
            task_description="fix bug",
            started_at=started,
            last_activity=last,
            status_updated=True,
            slack_posted=False,
            close_prompt_shown=True,
            thread_ts="1234.5",
            channel_id="C_TEST",
        )

        stored = ws.to_stored(status="active")

        assert stored.member == "sofia"
        assert stored.task_description == "fix bug"
        assert stored.status == "active"
        assert stored.started_at == started.timestamp()
        assert stored.last_activity == last.timestamp()
        assert stored.status_updated is True
        assert stored.slack_posted is False
        assert stored.close_prompt_shown is True
        assert stored.thread_ts == "1234.5"
        assert stored.channel_id == "C_TEST"

    def test_stored_to_work_session(self):
        now = time.time()
        stored = StoredSession(
            member="leo",
            task_description="write tests",
            status="active",
            started_at=now - 300,
            last_activity=now - 60,
            thread_ts="T1",
            channel_id="C1",
            status_updated=True,
            slack_posted=True,
            close_prompt_shown=False,
        )

        ws = WorkSession.from_stored(stored)

        assert ws.member == "leo"
        assert ws.task_description == "write tests"
        assert ws.started_at.timestamp() == pytest.approx(now - 300)
        assert ws.last_activity.timestamp() == pytest.approx(now - 60)
        assert ws.status_updated is True
        assert ws.slack_posted is True
        assert ws.close_prompt_shown is False
        assert ws.thread_ts == "T1"
        assert ws.channel_id == "C1"

    def test_roundtrip_preserves_fields(self):
        original = WorkSession(
            member="james",
            task_description="update pipeline",
            thread_ts="T2",
            channel_id="C2",
        )
        stored = original.to_stored(status="active")
        restored = WorkSession.from_stored(stored)

        assert restored.member == original.member
        assert restored.task_description == original.task_description
        assert restored.thread_ts == original.thread_ts
        assert restored.channel_id == original.channel_id


# ---------------------------------------------------------------------------
# Persistence during normal session lifecycle
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    @pytest.mark.anyio
    async def test_start_session_persists(self, session_mgr, fresh_storage):
        session_mgr.tmux.spawn_persona_tab = MagicMock(return_value=True)

        # Make spawn_persona_tab awaitable
        async def async_spawn(*args, **kwargs):
            return True

        session_mgr.tmux.spawn_persona_tab = async_spawn

        success = await session_mgr.start_session(
            member="sofia",
            task_description="fix bug",
            task_prompt="prompt text",
            thread_ts="T1",
            channel_id="C1",
        )
        assert success is True

        # Verify session persisted
        stored = fresh_storage.load_session("sofia")
        assert stored is not None
        assert stored.member == "sofia"
        assert stored.status == "active"
        assert stored.thread_ts == "T1"

        # Verify audit log
        events = fresh_storage.query_audit(member="sofia", event_type="session_started")
        assert len(events) == 1

    def test_mark_status_updated_persists(self, session_mgr, fresh_storage):
        session_mgr.active_sessions["sofia"] = WorkSession(
            member="sofia", task_description="fix bug",
        )
        session_mgr._persist(session_mgr.active_sessions["sofia"])

        session_mgr.mark_status_updated("sofia")

        stored = fresh_storage.load_session("sofia")
        assert stored.status_updated is True

    def test_end_session_marks_completed(self, session_mgr, fresh_storage):
        session_mgr.active_sessions["sofia"] = WorkSession(
            member="sofia", task_description="fix bug",
            status_updated=True, close_prompt_shown=True,
        )
        session_mgr._persist(session_mgr.active_sessions["sofia"])

        session_mgr.end_session("sofia")

        # Session should still exist in storage but with completed status
        stored = fresh_storage.load_session("sofia")
        assert stored is not None
        assert stored.status == "completed"

        # In-memory tracking should be gone
        assert "sofia" not in session_mgr.active_sessions

        # Audit events should be logged
        events = fresh_storage.query_audit(member="sofia", event_type="session_completed")
        assert len(events) == 1


# ---------------------------------------------------------------------------
# check_pane_alive
# ---------------------------------------------------------------------------


class TestCheckPaneAlive:
    def test_claude_prompt_means_alive(self, session_mgr):
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=True)
        session_mgr.tmux.get_pane_content = MagicMock(
            return_value="Welcome\nHow can I help you today?"
        )
        assert session_mgr.check_pane_alive("sofia") is True

    def test_shell_prompt_means_dead(self, session_mgr):
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=True)
        session_mgr.tmux.get_pane_content = MagicMock(
            return_value="Process exited with code 1\nuser@host:~/anchovies$ "
        )
        assert session_mgr.check_pane_alive("sofia") is False

    def test_no_tab_means_dead(self, session_mgr):
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=False)
        assert session_mgr.check_pane_alive("sofia") is False

    def test_empty_pane_means_dead(self, session_mgr):
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=True)
        session_mgr.tmux.get_pane_content = MagicMock(return_value="")
        assert session_mgr.check_pane_alive("sofia") is False

    def test_thinking_means_alive(self, session_mgr):
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=True)
        session_mgr.tmux.get_pane_content = MagicMock(return_value="thinking...")
        assert session_mgr.check_pane_alive("sofia") is True


# ---------------------------------------------------------------------------
# Recovery scenarios
# ---------------------------------------------------------------------------


class TestRecovery:
    def test_recover_live_session(self, session_mgr, fresh_storage):
        """A session whose tab exists and Claude is alive should be restored."""
        fresh_storage.save_session(StoredSession(
            member="sofia", task_description="fix bug", status="active",
            started_at=time.time(), last_activity=time.time(),
        ))

        session_mgr.tmux.list_active_tabs = MagicMock(return_value=["sofia"])
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=True)
        session_mgr.tmux.get_pane_content = MagicMock(return_value="> ")

        stats = session_mgr.recover_from_storage()

        assert stats["restored"] == 1
        assert stats["crashed"] == 0
        assert "sofia" in session_mgr.active_sessions

    def test_recover_missing_tab_marks_crashed(self, session_mgr, fresh_storage):
        """A session whose tab no longer exists should be marked crashed."""
        fresh_storage.save_session(StoredSession(
            member="sofia", task_description="fix bug", status="active",
            started_at=time.time(), last_activity=time.time(),
        ))

        session_mgr.tmux.list_active_tabs = MagicMock(return_value=[])  # No tabs
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=False)

        stats = session_mgr.recover_from_storage()

        assert stats["restored"] == 0
        assert stats["crashed"] == 1

        updated = fresh_storage.load_session("sofia")
        assert updated.status == "crashed"

        # Should have logged a crash event
        events = fresh_storage.query_audit(member="sofia", event_type="session_crashed")
        assert len(events) == 1

    def test_recover_dead_pane_marks_crashed(self, session_mgr, fresh_storage):
        """A session whose tab exists but Claude is dead should be marked crashed."""
        fresh_storage.save_session(StoredSession(
            member="sofia", task_description="fix bug", status="active",
            started_at=time.time(), last_activity=time.time(),
        ))

        session_mgr.tmux.list_active_tabs = MagicMock(return_value=["sofia"])
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=True)
        session_mgr.tmux.get_pane_content = MagicMock(
            return_value="user@host:~/anchovies$ "  # shell prompt, Claude exited
        )

        stats = session_mgr.recover_from_storage()

        assert stats["crashed"] == 1
        # Should have closed the orphaned tab
        session_mgr.tmux.close_persona_tab.assert_called_once_with("sofia")

    def test_recover_no_tmux_marks_all_crashed(self, session_mgr, fresh_storage):
        """If tmux is down, all active sessions are marked crashed."""
        for name in ["sofia", "leo", "james"]:
            fresh_storage.save_session(StoredSession(
                member=name, task_description="t", status="active",
                started_at=time.time(), last_activity=time.time(),
            ))

        session_mgr.tmux.session_exists = MagicMock(return_value=False)

        stats = session_mgr.recover_from_storage()

        assert stats["crashed"] == 3

        for name in ["sofia", "leo", "james"]:
            assert fresh_storage.load_session(name).status == "crashed"

    def test_recover_ignores_completed_sessions(self, session_mgr, fresh_storage):
        """Completed sessions in storage are ignored during recovery."""
        fresh_storage.save_session(StoredSession(
            member="old", task_description="done task", status="completed",
            started_at=time.time(), last_activity=time.time(),
        ))

        session_mgr.tmux.list_active_tabs = MagicMock(return_value=[])

        stats = session_mgr.recover_from_storage()

        assert stats["restored"] == 0
        assert stats["crashed"] == 0

    def test_orphaned_tabs_noted(self, session_mgr, fresh_storage):
        """Tabs that exist without a storage record are noted but not touched."""
        session_mgr.tmux.list_active_tabs = MagicMock(return_value=["sofia", "unknown_persona"])
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=True)
        session_mgr.tmux.get_pane_content = MagicMock(return_value="> ")

        # No sessions in storage
        stats = session_mgr.recover_from_storage()

        assert stats["orphaned_tabs"] == 2

    def test_mixed_recovery(self, session_mgr, fresh_storage):
        """Mix of live, missing-tab, and dead-pane sessions."""
        for name in ["live_one", "missing_tab", "dead_pane"]:
            fresh_storage.save_session(StoredSession(
                member=name, task_description="t", status="active",
                started_at=time.time(), last_activity=time.time(),
            ))

        session_mgr.tmux.list_active_tabs = MagicMock(
            return_value=["live_one", "dead_pane"]  # missing_tab absent
        )
        session_mgr.tmux.persona_tab_exists = MagicMock(
            side_effect=lambda m: m in ["live_one", "dead_pane"]
        )

        def pane_content(target, lines=20):
            if "live_one" in target:
                return "How can I help?"
            return "user@host:~$ "  # dead pane

        session_mgr.tmux.get_pane_content = MagicMock(side_effect=pane_content)

        stats = session_mgr.recover_from_storage()

        assert stats["restored"] == 1
        assert stats["crashed"] == 2
        assert "live_one" in session_mgr.active_sessions
        assert "missing_tab" not in session_mgr.active_sessions
        assert "dead_pane" not in session_mgr.active_sessions


class TestSyncWithTmux:
    def test_sync_removes_externally_closed_tabs(self, session_mgr, fresh_storage):
        """If a tab was closed externally, in-memory tracking is removed."""
        session_mgr.active_sessions["sofia"] = WorkSession(
            member="sofia", task_description="task"
        )
        session_mgr._persist(session_mgr.active_sessions["sofia"])

        session_mgr.tmux.list_active_tabs = MagicMock(return_value=[])  # Tab gone

        session_mgr.sync_with_tmux()

        assert "sofia" not in session_mgr.active_sessions
        # Storage status updated to crashed
        stored = fresh_storage.load_session("sofia")
        assert stored.status == "crashed"
