"""Tests for session timeout hardening (soft + hard + crash detection)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from anchovies.storage import Storage, reset_storage
from anchovies.work_sessions.session_manager import SessionManager, WorkSession


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
def session_mgr(fresh_storage):
    """SessionManager with mocked tmux."""
    with patch("anchovies.work_sessions.session_manager.get_tmux_manager") as mock_get_tmux:
        mock_tmux = MagicMock()
        mock_tmux.session_name = "anchovies"
        mock_tmux.session_exists = MagicMock(return_value=True)
        mock_tmux.persona_tab_exists = MagicMock(return_value=True)
        mock_tmux.close_persona_tab = MagicMock(return_value=True)
        mock_tmux.list_active_tabs = MagicMock(return_value=[])
        mock_tmux.get_pane_content = MagicMock(return_value="> ")
        mock_get_tmux.return_value = mock_tmux
        mgr = SessionManager()
        mgr.tmux = mock_tmux
        yield mgr


def _make_session(member: str, started_minutes_ago: float, last_active_minutes_ago: float,
                  ready_to_close: bool = False) -> WorkSession:
    """Helper to build a WorkSession with controlled timestamps."""
    now = datetime.now()
    session = WorkSession(
        member=member,
        task_description=f"task for {member}",
        started_at=now - timedelta(minutes=started_minutes_ago),
        last_activity=now - timedelta(minutes=last_active_minutes_ago),
        status_updated=ready_to_close,
        close_prompt_shown=ready_to_close,
    )
    return session


# ---------------------------------------------------------------------------
# total_minutes property
# ---------------------------------------------------------------------------


class TestTotalMinutesProperty:
    def test_freshly_started(self):
        s = _make_session("sofia", started_minutes_ago=0, last_active_minutes_ago=0)
        assert s.total_minutes < 0.1

    def test_long_running(self):
        s = _make_session("sofia", started_minutes_ago=45, last_active_minutes_ago=2)
        assert 44 < s.total_minutes < 46

    def test_total_independent_of_activity(self):
        """A session active 1m ago but started 30m ago has total_minutes ~= 30."""
        s = _make_session("sofia", started_minutes_ago=30, last_active_minutes_ago=1)
        assert 29 < s.total_minutes < 31
        assert 0.5 < s.inactive_minutes < 1.5


# ---------------------------------------------------------------------------
# Soft timeout (existing behaviour preserved)
# ---------------------------------------------------------------------------


class TestSoftTimeout:
    def test_inactive_but_not_ready_not_returned(self, session_mgr):
        """Soft timeout requires ready_to_close to be True."""
        with patch.object(session_mgr, "TIMEOUT_MINUTES", 10):
            s = _make_session("sofia", 20, 15, ready_to_close=False)
            session_mgr.active_sessions["sofia"] = s
            assert "sofia" not in session_mgr.check_timeouts()

    def test_inactive_and_ready_returned(self, session_mgr):
        with patch.object(session_mgr, "TIMEOUT_MINUTES", 10):
            s = _make_session("sofia", 20, 15, ready_to_close=True)
            session_mgr.active_sessions["sofia"] = s
            assert "sofia" in session_mgr.check_timeouts()

    def test_recently_active_not_returned(self, session_mgr):
        with patch.object(session_mgr, "TIMEOUT_MINUTES", 10):
            s = _make_session("sofia", 5, 2, ready_to_close=True)
            session_mgr.active_sessions["sofia"] = s
            assert "sofia" not in session_mgr.check_timeouts()


# ---------------------------------------------------------------------------
# Hard timeout
# ---------------------------------------------------------------------------


class TestHardTimeout:
    def test_under_hard_timeout_not_returned(self, session_mgr):
        with patch.object(session_mgr, "HARD_TIMEOUT_MINUTES", 30):
            s = _make_session("sofia", started_minutes_ago=20, last_active_minutes_ago=1)
            session_mgr.active_sessions["sofia"] = s
            assert "sofia" not in session_mgr.check_hard_timeouts()

    def test_over_hard_timeout_returned_regardless_of_activity(self, session_mgr):
        """Hard timeout fires even if the session is actively responding."""
        with patch.object(session_mgr, "HARD_TIMEOUT_MINUTES", 30):
            # Active 1 minute ago but started 35 minutes ago
            s = _make_session("sofia", started_minutes_ago=35, last_active_minutes_ago=1)
            session_mgr.active_sessions["sofia"] = s
            assert "sofia" in session_mgr.check_hard_timeouts()

    def test_over_hard_timeout_regardless_of_completion_state(self, session_mgr):
        with patch.object(session_mgr, "HARD_TIMEOUT_MINUTES", 30):
            # Not ready to close, but exceeded hard timeout
            s = _make_session("sofia", 35, 5, ready_to_close=False)
            session_mgr.active_sessions["sofia"] = s
            assert "sofia" in session_mgr.check_hard_timeouts()


# ---------------------------------------------------------------------------
# Crash detection
# ---------------------------------------------------------------------------


class TestFindCrashedSessions:
    def test_alive_pane_not_crashed(self, session_mgr):
        session_mgr.active_sessions["sofia"] = _make_session("sofia", 5, 1)
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=True)
        session_mgr.tmux.get_pane_content = MagicMock(return_value="> ")
        assert "sofia" not in session_mgr.find_crashed_sessions()

    def test_dead_pane_returned(self, session_mgr):
        session_mgr.active_sessions["sofia"] = _make_session("sofia", 5, 1)
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=True)
        session_mgr.tmux.get_pane_content = MagicMock(
            return_value="user@host:~$ "
        )
        assert "sofia" in session_mgr.find_crashed_sessions()

    def test_missing_tab_returned(self, session_mgr):
        session_mgr.active_sessions["sofia"] = _make_session("sofia", 5, 1)
        session_mgr.tmux.persona_tab_exists = MagicMock(return_value=False)
        assert "sofia" in session_mgr.find_crashed_sessions()


# ---------------------------------------------------------------------------
# auto_close_timed_out integrates all three categories
# ---------------------------------------------------------------------------


class TestAutoCloseTimedOut:
    def test_closes_soft_timeout(self, session_mgr, fresh_storage):
        with patch.object(session_mgr, "TIMEOUT_MINUTES", 10), \
             patch.object(session_mgr, "HARD_TIMEOUT_MINUTES", 30):
            s = _make_session("sofia", 20, 15, ready_to_close=True)
            session_mgr.active_sessions["sofia"] = s
            session_mgr._persist(s)

            closed = session_mgr.auto_close_timed_out()
            assert "sofia" in closed
            assert "sofia" not in session_mgr.active_sessions

    def test_closes_hard_timeout_force(self, session_mgr, fresh_storage):
        """Hard timeout closes even if not ready, marks status='timeout'."""
        with patch.object(session_mgr, "TIMEOUT_MINUTES", 10), \
             patch.object(session_mgr, "HARD_TIMEOUT_MINUTES", 30):
            s = _make_session("sofia", started_minutes_ago=35, last_active_minutes_ago=1,
                              ready_to_close=False)
            session_mgr.active_sessions["sofia"] = s
            session_mgr._persist(s)

            closed = session_mgr.auto_close_timed_out()
            assert "sofia" in closed
            assert "sofia" not in session_mgr.active_sessions

            # Storage status should be 'timeout'
            stored = fresh_storage.load_session("sofia")
            assert stored.status == "timeout"

            # Audit log should have a session_timeout event
            events = fresh_storage.query_audit(member="sofia", event_type="session_timeout")
            assert len(events) == 1

    def test_closes_crashed(self, session_mgr, fresh_storage):
        with patch.object(session_mgr, "TIMEOUT_MINUTES", 10), \
             patch.object(session_mgr, "HARD_TIMEOUT_MINUTES", 30):
            s = _make_session("sofia", started_minutes_ago=5, last_active_minutes_ago=1)
            session_mgr.active_sessions["sofia"] = s
            session_mgr._persist(s)

            # Pane is dead
            session_mgr.tmux.get_pane_content = MagicMock(return_value="user@host:~$ ")

            closed = session_mgr.auto_close_timed_out()
            assert "sofia" in closed

            stored = fresh_storage.load_session("sofia")
            assert stored.status == "crashed"

            events = fresh_storage.query_audit(member="sofia", event_type="session_crashed")
            assert len(events) >= 1

    def test_no_double_close(self, session_mgr, fresh_storage):
        """A session that hits both soft and hard timeout is closed only once."""
        with patch.object(session_mgr, "TIMEOUT_MINUTES", 10), \
             patch.object(session_mgr, "HARD_TIMEOUT_MINUTES", 30):
            s = _make_session("sofia", started_minutes_ago=35, last_active_minutes_ago=20,
                              ready_to_close=True)
            session_mgr.active_sessions["sofia"] = s
            session_mgr._persist(s)

            closed = session_mgr.auto_close_timed_out()
            # 'sofia' should appear only once
            assert closed.count("sofia") == 1

    def test_healthy_session_not_closed(self, session_mgr, fresh_storage):
        with patch.object(session_mgr, "TIMEOUT_MINUTES", 10), \
             patch.object(session_mgr, "HARD_TIMEOUT_MINUTES", 30):
            s = _make_session("sofia", started_minutes_ago=5, last_active_minutes_ago=1)
            session_mgr.active_sessions["sofia"] = s
            session_mgr._persist(s)

            closed = session_mgr.auto_close_timed_out()
            assert closed == []
            assert "sofia" in session_mgr.active_sessions


# ---------------------------------------------------------------------------
# Configurability
# ---------------------------------------------------------------------------


class TestConfigurability:
    def test_hard_timeout_env_var_default(self):
        """HARD_TIMEOUT_MINUTES defaults to 30."""
        import os
        prev = os.environ.pop("HARD_TIMEOUT_MINUTES", None)
        try:
            # Reload module to re-read class-level constant
            import importlib
            from anchovies.work_sessions import session_manager
            importlib.reload(session_manager)
            assert session_manager.SessionManager.HARD_TIMEOUT_MINUTES == 30
        finally:
            if prev is not None:
                os.environ["HARD_TIMEOUT_MINUTES"] = prev
            import importlib
            from anchovies.work_sessions import session_manager
            importlib.reload(session_manager)

    def test_soft_timeout_env_var_default(self):
        """TIMEOUT_MINUTES defaults to 10."""
        import os
        prev = os.environ.pop("SESSION_TIMEOUT_MINUTES", None)
        try:
            import importlib
            from anchovies.work_sessions import session_manager
            importlib.reload(session_manager)
            assert session_manager.SessionManager.TIMEOUT_MINUTES == 10
        finally:
            if prev is not None:
                os.environ["SESSION_TIMEOUT_MINUTES"] = prev
            import importlib
            from anchovies.work_sessions import session_manager
            importlib.reload(session_manager)
