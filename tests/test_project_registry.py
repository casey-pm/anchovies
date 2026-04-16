"""Tests for the Project Registry (multi-project Phase 1)."""

import time
from pathlib import Path

import pytest
import yaml

from anchovies.project_registry import (
    Project,
    ProjectRegistry,
    ensure_project_dirs,
    get_project_registry,
    reset_registry,
)
from anchovies.storage import Storage, StoredSession, reset_storage


# ---------------------------------------------------------------------------
# Project dataclass
# ---------------------------------------------------------------------------


class TestProjectDataclass:
    def test_basic_creation(self):
        p = Project(
            name="calculator",
            display_name="Calculator App",
            context_base=Path("/tmp/calc"),
            working_dir=Path("/tmp/calc"),
        )
        assert p.name == "calculator"
        assert p.active is True
        assert p.default_branch == "main"

    def test_defaults(self):
        p = Project(name="x", display_name="X", context_base=Path("/x"), working_dir=Path("/x"))
        assert p.description == ""
        assert p.default_branch == "main"
        assert p.active is True


# ---------------------------------------------------------------------------
# ensure_project_dirs
# ---------------------------------------------------------------------------


class TestEnsureProjectDirs:
    def test_creates_subdirectories(self, tmp_path):
        ctx = tmp_path / "project_ctx"
        p = Project(name="test", display_name="Test", context_base=ctx, working_dir=ctx)
        ensure_project_dirs(p)
        assert (ctx / "status").is_dir()
        assert (ctx / "questions").is_dir()
        assert (ctx / "instructions").is_dir()

    def test_idempotent(self, tmp_path):
        ctx = tmp_path / "project_ctx"
        p = Project(name="test", display_name="Test", context_base=ctx, working_dir=ctx)
        ensure_project_dirs(p)
        ensure_project_dirs(p)  # should not error
        assert (ctx / "status").is_dir()


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict):
    path.write_text(yaml.dump(data, default_flow_style=False))


class TestYamlLoading:
    def test_load_valid_yaml(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        _write_yaml(yaml_path, {
            "projects": {
                "calculator": {
                    "display_name": "Calculator App",
                    "context_base": "/tmp/calc",
                    "working_dir": "/tmp/calc",
                    "description": "A test calculator",
                    "default_branch": "develop",
                },
            },
            "default_project": "calculator",
        })
        reg = ProjectRegistry(yaml_path)
        assert not reg.is_empty()
        p = reg.get("calculator")
        assert p is not None
        assert p.display_name == "Calculator App"
        assert p.context_base == Path("/tmp/calc")
        assert p.default_branch == "develop"
        assert reg.get_default_name() == "calculator"

    def test_load_empty_yaml(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        _write_yaml(yaml_path, {"projects": {}, "default_project": None})
        reg = ProjectRegistry(yaml_path)
        assert reg.is_empty()

    def test_missing_yaml_file(self, tmp_path):
        yaml_path = tmp_path / "nonexistent.yaml"
        reg = ProjectRegistry(yaml_path)
        assert reg.is_empty()

    def test_case_insensitive_names(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        _write_yaml(yaml_path, {
            "projects": {"MyProject": {"context_base": "/tmp/x"}},
        })
        reg = ProjectRegistry(yaml_path)
        assert reg.get("myproject") is not None
        assert reg.get("MYPROJECT") is not None

    def test_working_dir_defaults_to_context_base(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        _write_yaml(yaml_path, {
            "projects": {"calc": {"context_base": "/tmp/calc"}},
        })
        reg = ProjectRegistry(yaml_path)
        p = reg.get("calc")
        assert p.working_dir == Path("/tmp/calc")


# ---------------------------------------------------------------------------
# Hot-reload
# ---------------------------------------------------------------------------


class TestHotReload:
    def test_reload_detects_change(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        _write_yaml(yaml_path, {"projects": {}})
        reg = ProjectRegistry(yaml_path)
        assert reg.is_empty()

        # Modify the file
        time.sleep(0.05)  # ensure mtime changes
        _write_yaml(yaml_path, {
            "projects": {"new_project": {"context_base": "/tmp/new"}},
        })

        reloaded = reg.reload_if_changed()
        assert reloaded is True
        assert reg.get("new_project") is not None

    def test_no_reload_when_unchanged(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        _write_yaml(yaml_path, {"projects": {}})
        reg = ProjectRegistry(yaml_path)

        reloaded = reg.reload_if_changed()
        assert reloaded is False

    def test_reload_missing_file(self, tmp_path):
        yaml_path = tmp_path / "missing.yaml"
        reg = ProjectRegistry(yaml_path)
        assert reg.reload_if_changed() is False


# ---------------------------------------------------------------------------
# Register / unregister / list / get
# ---------------------------------------------------------------------------


class TestRegistryOperations:
    def test_register_and_get(self, tmp_path):
        reg = ProjectRegistry(tmp_path / "empty.yaml")
        p = Project(name="test", display_name="Test", context_base=Path("/tmp"), working_dir=Path("/tmp"))
        reg.register(p)
        assert reg.get("test") is p
        assert not reg.is_empty()

    def test_unregister(self, tmp_path):
        reg = ProjectRegistry(tmp_path / "empty.yaml")
        p = Project(name="test", display_name="Test", context_base=Path("/tmp"), working_dir=Path("/tmp"))
        reg.register(p)
        assert reg.unregister("test") is True
        assert reg.get("test") is None
        assert reg.is_empty()

    def test_unregister_nonexistent(self, tmp_path):
        reg = ProjectRegistry(tmp_path / "empty.yaml")
        assert reg.unregister("nope") is False

    def test_list_projects(self, tmp_path):
        reg = ProjectRegistry(tmp_path / "empty.yaml")
        for name in ["a", "b", "c"]:
            reg.register(Project(name=name, display_name=name.upper(),
                                 context_base=Path(f"/tmp/{name}"), working_dir=Path(f"/tmp/{name}")))
        assert len(reg.list_projects()) == 3

    def test_list_active_only(self, tmp_path):
        reg = ProjectRegistry(tmp_path / "empty.yaml")
        reg.register(Project(name="active", display_name="A",
                             context_base=Path("/tmp/a"), working_dir=Path("/tmp/a"), active=True))
        reg.register(Project(name="inactive", display_name="I",
                             context_base=Path("/tmp/i"), working_dir=Path("/tmp/i"), active=False))
        assert len(reg.list_projects(active_only=True)) == 1
        assert len(reg.list_projects(active_only=False)) == 2

    def test_set_and_get_default(self, tmp_path):
        reg = ProjectRegistry(tmp_path / "empty.yaml")
        reg.register(Project(name="calc", display_name="Calc",
                             context_base=Path("/tmp/c"), working_dir=Path("/tmp/c")))
        reg.set_default("calc")
        assert reg.get_default() is not None
        assert reg.get_default().name == "calc"

    def test_clear_default(self, tmp_path):
        reg = ProjectRegistry(tmp_path / "empty.yaml")
        reg.set_default("calc")
        reg.set_default(None)
        assert reg.get_default() is None


# ---------------------------------------------------------------------------
# save_to_yaml roundtrip
# ---------------------------------------------------------------------------


class TestSaveToYaml:
    def test_roundtrip(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        reg = ProjectRegistry(yaml_path)
        reg.register(Project(
            name="calc", display_name="Calculator App",
            context_base=Path("/tmp/calc"), working_dir=Path("/tmp/calc/repo"),
            description="A calculator", default_branch="develop",
        ))
        reg.set_default("calc")
        reg.save_to_yaml()

        # Reload from the saved file
        reg2 = ProjectRegistry(yaml_path)
        assert not reg2.is_empty()
        p = reg2.get("calc")
        assert p.display_name == "Calculator App"
        assert p.working_dir == Path("/tmp/calc/repo")
        assert p.description == "A calculator"
        assert p.default_branch == "develop"
        assert reg2.get_default_name() == "calc"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_returns_same_instance(self, tmp_path):
        reset_registry()
        try:
            r1 = get_project_registry(tmp_path / "p.yaml")
            r2 = get_project_registry()
            assert r1 is r2
        finally:
            reset_registry()

    def test_reset_creates_new(self, tmp_path):
        reset_registry()
        try:
            r1 = get_project_registry(tmp_path / "p.yaml")
            reset_registry()
            r2 = get_project_registry(tmp_path / "p.yaml")
            assert r1 is not r2
        finally:
            reset_registry()


# ---------------------------------------------------------------------------
# Storage migration (project column on sessions)
# ---------------------------------------------------------------------------


class TestStorageMigration:
    def test_new_db_has_project_column(self, tmp_path):
        """A fresh database should have the project column from the start."""
        storage = Storage(tmp_path / "new.db")
        s = StoredSession(
            member="sofia", task_description="t", status="active",
            started_at=time.time(), last_activity=time.time(),
            project="calculator",
        )
        storage.save_session(s)
        loaded = storage.load_session("sofia")
        assert loaded.project == "calculator"
        storage.close()

    def test_existing_db_gets_project_column(self, tmp_path):
        """An existing DB without the project column should get it via migration."""
        db_path = tmp_path / "old.db"
        # Create a DB with the old schema (no project column)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE sessions (
                member TEXT PRIMARY KEY,
                task_description TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at REAL NOT NULL,
                last_activity REAL NOT NULL,
                thread_ts TEXT,
                channel_id TEXT,
                status_updated INTEGER DEFAULT 0,
                slack_posted INTEGER DEFAULT 0,
                close_prompt_shown INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            INSERT INTO sessions (member, task_description, status, started_at, last_activity)
            VALUES ('sofia', 'old task', 'active', 0, 0)
        """)
        conn.commit()
        conn.close()

        # Open with Storage — should migrate
        storage = Storage(db_path)
        loaded = storage.load_session("sofia")
        assert loaded is not None
        assert loaded.project is None  # old row has NULL project
        assert loaded.task_description == "old task"

        # Can now save with project
        s = StoredSession(
            member="leo", task_description="new task", status="active",
            started_at=time.time(), last_activity=time.time(), project="calc",
        )
        storage.save_session(s)
        assert storage.load_session("leo").project == "calc"
        storage.close()

    def test_null_project_backwards_compatible(self, tmp_path):
        """Sessions without a project (legacy) should have project=None."""
        storage = Storage(tmp_path / "test.db")
        s = StoredSession(
            member="sofia", task_description="t", status="active",
            started_at=time.time(), last_activity=time.time(),
        )
        storage.save_session(s)
        loaded = storage.load_session("sofia")
        assert loaded.project is None
        storage.close()
