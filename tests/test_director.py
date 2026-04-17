"""Tests for the Director module (project briefs, consultation, summary)."""

import inspect
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from anchovies.director import (
    aggregate_session_summary,
    compile_consultation,
    create_project_brief,
)
from anchovies.project_registry import Project, ProjectRegistry, reset_registry
from anchovies.storage import Storage, reset_storage
from anchovies import config


@pytest.fixture
def director_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_NAME", "Test Project")

    reset_registry()
    reg = ProjectRegistry(tmp_path / "empty.yaml")
    reg.register(Project(
        name="calculator",
        display_name="Calculator App",
        context_base=Path(tmp_path / "ctx"),
        working_dir=Path(tmp_path / "ctx"),
        description="A test calculator",
        authority={
            "autonomous": ["Run tests", "Fix bugs"],
            "escalate": ["Delete files", "Change API"],
        },
    ))
    import anchovies.project_registry as pr_module
    monkeypatch.setattr(pr_module, "_registry", reg)
    yield reg
    reset_registry()


# ---------------------------------------------------------------------------
# create_project_brief
# ---------------------------------------------------------------------------


class TestCreateProjectBrief:
    def test_basic_brief(self):
        brief = create_project_brief("fix the multiply function")
        assert "Project Brief" in brief
        assert "fix the multiply function" in brief

    def test_brief_with_project(self, director_setup):
        brief = create_project_brief("fix the multiply function", project="calculator")
        assert "Calculator App" in brief
        assert "test calculator" in brief

    def test_brief_includes_suggested_team(self):
        brief = create_project_brief("fix the data pipeline and update dbt models")
        assert "Suggested Team" in brief

    def test_brief_includes_files(self):
        brief = create_project_brief("fix bug", files=["app.py", "handler.py"])
        assert "app.py" in brief
        assert "handler.py" in brief

    def test_brief_includes_authority(self, director_setup):
        brief = create_project_brief("fix bug", project="calculator")
        assert "Decision Authority" in brief
        assert "Run tests" in brief
        assert "Delete files" in brief

    def test_brief_no_project_uses_config(self):
        brief = create_project_brief("fix bug")
        assert "Test Project" in brief or "Project Brief" in brief


# ---------------------------------------------------------------------------
# compile_consultation
# ---------------------------------------------------------------------------


class TestCompileConsultation:
    def test_basic_compilation(self):
        inputs = {
            "sofia": "I'd focus on the dbt model first.",
            "elena": "Check the pipeline dependencies.",
        }
        result = compile_consultation("fix the data flow", inputs)
        assert "Sofia" in result
        assert "Elena" in result
        assert "dbt model" in result
        assert "pipeline" in result

    def test_includes_project_tag(self):
        result = compile_consultation("task", {"sofia": "input"}, project="calculator")
        assert "[calculator]" in result

    def test_includes_persona_count(self):
        inputs = {"a": "x", "b": "y", "c": "z"}
        result = compile_consultation("task", inputs)
        assert "3" in result

    def test_includes_timestamp(self):
        result = compile_consultation("task", {"sofia": "input"})
        assert "compiled by Marcus" in result


# ---------------------------------------------------------------------------
# aggregate_session_summary
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_storage(tmp_path, monkeypatch):
    reset_storage()
    storage = Storage(tmp_path / "test.db")
    import anchovies.storage as sm
    monkeypatch.setattr(sm, "_storage", storage)
    yield storage
    storage.close()
    reset_storage()


class TestAggregateSessionSummary:
    def test_no_sessions(self, fresh_storage):
        with patch("anchovies.work_sessions.get_session_manager") as mock_mgr:
            mgr = type("Mgr", (), {"list_sessions": lambda self: [], "active_sessions": {}})()
            mock_mgr.return_value = mgr
            result = aggregate_session_summary()
        assert "No active" in result or "Session Summary" in result

    def test_with_active_sessions(self, fresh_storage):
        from anchovies.work_sessions.session_manager import WorkSession
        with patch("anchovies.work_sessions.get_session_manager") as mock_mgr:
            session = WorkSession(member="sofia", task_description="fixing stuff")
            mgr = type("Mgr", (), {
                "list_sessions": lambda self: [session],
                "active_sessions": {"sofia": session},
            })()
            mock_mgr.return_value = mgr
            result = aggregate_session_summary()
        assert "Sofia" in result
        assert "Active" in result


# ---------------------------------------------------------------------------
# Handler integration
# ---------------------------------------------------------------------------


class TestDirectorInHandler:
    def test_brief_command_in_handler(self):
        import anchovies.handlers as h
        source = inspect.getsource(h._handle_control_command)
        assert "brief" in source
        assert "create_project_brief" in source

    def test_consult_command_in_handler(self):
        import anchovies.handlers as h
        source = inspect.getsource(h._handle_control_command)
        assert "consult" in source
        assert "compile_consultation" in source
