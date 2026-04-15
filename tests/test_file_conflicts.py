"""Tests for file conflict detection between concurrent work sessions."""

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
    """Create a SessionManager with mocked tmux."""
    with patch("anchovies.work_sessions.session_manager.get_tmux_manager") as mock_get_tmux:
        mock_tmux = MagicMock()
        mock_tmux.session_name = "anchovies"
        mock_tmux.session_exists = MagicMock(return_value=True)
        mock_get_tmux.return_value = mock_tmux
        mgr = SessionManager()
        mgr.tmux = mock_tmux
        yield mgr


# ---------------------------------------------------------------------------
# detect_file_conflicts logic
# ---------------------------------------------------------------------------


class TestDetectFileConflicts:
    def test_no_conflict_when_no_active_sessions(self, session_mgr):
        conflicts = session_mgr.detect_file_conflicts("sofia", ["app.py"])
        assert conflicts == []

    def test_no_conflict_when_no_files_passed(self, session_mgr):
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t", files=["app.py"]
        )
        conflicts = session_mgr.detect_file_conflicts("sofia", [])
        assert conflicts == []

    def test_no_conflict_when_other_has_no_files(self, session_mgr):
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t", files=[]
        )
        conflicts = session_mgr.detect_file_conflicts("sofia", ["app.py"])
        assert conflicts == []

    def test_no_conflict_with_different_files(self, session_mgr):
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t", files=["handler.py"]
        )
        conflicts = session_mgr.detect_file_conflicts("sofia", ["app.py"])
        assert conflicts == []

    def test_conflict_with_same_file(self, session_mgr):
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t", files=["app.py"]
        )
        conflicts = session_mgr.detect_file_conflicts("sofia", ["app.py"])
        assert len(conflicts) == 1
        assert conflicts[0][0] == "leo"
        assert "app.py" in conflicts[0][1]

    def test_conflict_with_different_path_same_basename(self, session_mgr):
        """Sessions referencing the same file with different paths should still conflict."""
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t", files=["./src/app.py"]
        )
        conflicts = session_mgr.detect_file_conflicts("sofia", ["~/proj/src/app.py"])
        assert len(conflicts) == 1

    def test_case_insensitive_matching(self, session_mgr):
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t", files=["App.py"]
        )
        conflicts = session_mgr.detect_file_conflicts("sofia", ["app.PY"])
        assert len(conflicts) == 1

    def test_multiple_conflicts_with_one_session(self, session_mgr):
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t",
            files=["app.py", "handler.py", "config.py"]
        )
        conflicts = session_mgr.detect_file_conflicts(
            "sofia", ["app.py", "handler.py"]
        )
        assert len(conflicts) == 1
        assert set(conflicts[0][1]) == {"app.py", "handler.py"}

    def test_conflicts_with_multiple_sessions(self, session_mgr):
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t", files=["app.py"]
        )
        session_mgr.active_sessions["james"] = WorkSession(
            member="james", task_description="t", files=["handler.py"]
        )
        conflicts = session_mgr.detect_file_conflicts(
            "sofia", ["app.py", "handler.py"]
        )
        # Should find both leo and james
        members = {c[0] for c in conflicts}
        assert members == {"leo", "james"}

    def test_excludes_self(self, session_mgr):
        """A session shouldn't conflict with itself."""
        session_mgr.active_sessions["sofia"] = WorkSession(
            member="sofia", task_description="t", files=["app.py"]
        )
        conflicts = session_mgr.detect_file_conflicts("sofia", ["app.py"])
        assert conflicts == []

    def test_partial_overlap(self, session_mgr):
        """Sessions with some overlapping and some unique files report only overlap."""
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t",
            files=["shared.py", "leo_only.py"]
        )
        conflicts = session_mgr.detect_file_conflicts(
            "sofia", ["shared.py", "sofia_only.py"]
        )
        assert len(conflicts) == 1
        assert "shared.py" in conflicts[0][1]
        assert "leo_only.py" not in conflicts[0][1]


# ---------------------------------------------------------------------------
# start_session integrates conflict detection
# ---------------------------------------------------------------------------


class TestStartSessionWithConflicts:
    @pytest.mark.anyio
    async def test_start_session_logs_conflict_to_audit(self, session_mgr, fresh_storage):
        """Conflicts during start should appear in the audit log."""
        # First session
        session_mgr.active_sessions["leo"] = WorkSession(
            member="leo", task_description="t", files=["app.py"]
        )

        # Mock the spawn so it succeeds
        async def fake_spawn(*args, **kwargs):
            return True
        session_mgr.tmux.spawn_persona_tab = fake_spawn

        # Second session targeting same file
        await session_mgr.start_session(
            member="sofia",
            task_description="also fix app",
            task_prompt="prompt",
            files=["app.py"],
        )

        # Audit log should record the conflict
        events = fresh_storage.query_audit(event_type="file_conflict")
        assert len(events) == 1
        assert events[0].member == "sofia"
        assert "leo" in events[0].details["other_member"]

    @pytest.mark.anyio
    async def test_session_files_persisted(self, session_mgr, fresh_storage):
        """The new session's files list should be tracked in active_sessions."""
        async def fake_spawn(*args, **kwargs):
            return True
        session_mgr.tmux.spawn_persona_tab = fake_spawn

        await session_mgr.start_session(
            member="sofia",
            task_description="fix bug",
            task_prompt="prompt",
            files=["app.py", "handler.py"],
        )

        session = session_mgr.active_sessions["sofia"]
        assert session.files == ["app.py", "handler.py"]

    @pytest.mark.anyio
    async def test_no_files_no_audit_entry(self, session_mgr, fresh_storage):
        """A session without files should not produce a conflict audit entry."""
        async def fake_spawn(*args, **kwargs):
            return True
        session_mgr.tmux.spawn_persona_tab = fake_spawn

        await session_mgr.start_session(
            member="sofia",
            task_description="general task",
            task_prompt="prompt",
        )

        events = fresh_storage.query_audit(event_type="file_conflict")
        assert len(events) == 0


class TestHandlerConflictWarning:
    """Verify the handler posts a Slack warning on file conflict."""

    def test_handler_calls_detect_file_conflicts(self):
        """The chat hub message handler source should call detect_file_conflicts."""
        import inspect
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_chat_hub_message)
        assert "detect_file_conflicts" in source

    def test_handler_passes_files_to_start_session(self):
        """start_session should be called with files= when work request has files."""
        import inspect
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_chat_hub_message)
        assert "files=" in source
