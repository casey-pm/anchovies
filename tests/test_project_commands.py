"""Tests for Slack project management commands (Phase 5)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from anchovies.handlers import parse_project_command, handle_project_command
from anchovies.project_registry import Project, ProjectRegistry, reset_registry


# ---------------------------------------------------------------------------
# parse_project_command
# ---------------------------------------------------------------------------


class TestParseProjectCommand:
    def test_list_projects(self):
        assert parse_project_command("projects")["command"] == "list"
        assert parse_project_command("list projects")["command"] == "list"

    def test_add_project_basic(self):
        result = parse_project_command("add project calculator --context /tmp/calc")
        assert result["command"] == "add"
        assert result["args"]["name"] == "calculator"
        assert result["args"]["context"] == "/tmp/calc"
        assert result["args"]["working_dir"] == "/tmp/calc"  # defaults to context

    def test_add_project_with_working_dir(self):
        result = parse_project_command(
            "add project calc --context /tmp/ctx --working-dir /tmp/repo"
        )
        assert result["args"]["working_dir"] == "/tmp/repo"

    def test_add_project_with_description(self):
        result = parse_project_command(
            'add project calc --context /tmp/calc --desc "A test project"'
        )
        assert result["args"]["description"] == "A test project"

    def test_remove_project(self):
        result = parse_project_command("remove project calculator")
        assert result["command"] == "remove"
        assert result["args"] == "calculator"

    def test_set_default_project(self):
        result = parse_project_command("set default project calculator")
        assert result["command"] == "set_default"
        assert result["args"] == "calculator"

    def test_clear_default_project(self):
        result = parse_project_command("clear default project")
        assert result["command"] == "clear_default"

    def test_project_info(self):
        result = parse_project_command("project info calculator")
        assert result["command"] == "info"
        assert result["args"] == "calculator"

    def test_not_a_command(self):
        assert parse_project_command("fix the bug in app.py") is None
        assert parse_project_command("hey marcus what's up") is None
        assert parse_project_command("what projects are we working on?") is None

    def test_case_insensitive(self):
        assert parse_project_command("Projects")["command"] == "list"
        assert parse_project_command("LIST PROJECTS")["command"] == "list"
        assert parse_project_command("Add Project calc --context /tmp/c") is not None


# ---------------------------------------------------------------------------
# handle_project_command
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reset_registry()
    yaml_path = tmp_path / "projects.yaml"
    yaml_path.write_text(yaml.dump({"projects": {}, "default_project": None}))
    reg = ProjectRegistry(yaml_path)
    import anchovies.project_registry as pr_module
    monkeypatch.setattr(pr_module, "_registry", reg)
    yield reg
    reset_registry()


class TestHandleProjectCommand:
    @pytest.mark.anyio
    async def test_list_empty(self, registry):
        client = AsyncMock()
        await handle_project_command(client, "C1", "T1", {"command": "list", "args": ""})
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "No projects" in text

    @pytest.mark.anyio
    async def test_add_then_list(self, registry, tmp_path):
        client = AsyncMock()
        ctx = tmp_path / "calc_ctx"
        await handle_project_command(client, "C1", "T1", {
            "command": "add",
            "args": {"name": "calc", "context": str(ctx), "working_dir": str(ctx), "description": "Test"},
        })
        # Verify registered
        assert registry.get("calc") is not None
        # Status dirs created
        assert (ctx / "status").is_dir()

        # List should show it
        client.reset_mock()
        await handle_project_command(client, "C1", "T1", {"command": "list", "args": ""})
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "Calc" in text

    @pytest.mark.anyio
    async def test_remove_project(self, registry):
        registry.register(Project(
            name="calc", display_name="Calc",
            context_base=Path("/tmp/c"), working_dir=Path("/tmp/c"),
        ))
        client = AsyncMock()
        await handle_project_command(client, "C1", "T1", {"command": "remove", "args": "calc"})
        assert registry.get("calc") is None
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "removed" in text.lower()

    @pytest.mark.anyio
    async def test_remove_nonexistent(self, registry):
        client = AsyncMock()
        await handle_project_command(client, "C1", "T1", {"command": "remove", "args": "nope"})
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "not found" in text.lower()

    @pytest.mark.anyio
    async def test_set_default(self, registry):
        registry.register(Project(
            name="calc", display_name="Calc",
            context_base=Path("/tmp/c"), working_dir=Path("/tmp/c"),
        ))
        client = AsyncMock()
        await handle_project_command(client, "C1", "T1", {"command": "set_default", "args": "calc"})
        assert registry.get_default_name() == "calc"
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "default" in text.lower()

    @pytest.mark.anyio
    async def test_clear_default(self, registry):
        registry.set_default("something")
        client = AsyncMock()
        await handle_project_command(client, "C1", "T1", {"command": "clear_default", "args": ""})
        assert registry.get_default_name() is None

    @pytest.mark.anyio
    async def test_project_info(self, registry):
        registry.register(Project(
            name="calc", display_name="Calculator App",
            context_base=Path("/tmp/c"), working_dir=Path("/tmp/c/repo"),
            description="A test calculator",
        ))
        client = AsyncMock()
        await handle_project_command(client, "C1", "T1", {"command": "info", "args": "calc"})
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "Calculator App" in text
        assert "/tmp/c/repo" in text
        assert "test calculator" in text

    @pytest.mark.anyio
    async def test_info_nonexistent(self, registry):
        client = AsyncMock()
        await handle_project_command(client, "C1", "T1", {"command": "info", "args": "nope"})
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "not found" in text.lower()

    @pytest.mark.anyio
    async def test_add_saves_to_yaml(self, registry, tmp_path):
        client = AsyncMock()
        ctx = tmp_path / "new_ctx"
        await handle_project_command(client, "C1", "T1", {
            "command": "add",
            "args": {"name": "new", "context": str(ctx), "working_dir": str(ctx), "description": "New project"},
        })
        # YAML file should have been saved
        data = yaml.safe_load(registry._yaml_path.read_text())
        assert "new" in data["projects"]


class TestCommandIntegration:
    def test_parse_wired_into_handler(self):
        """handlers.handle_team_message should call parse_project_command."""
        import inspect
        from anchovies import handlers
        source = inspect.getsource(handlers.handle_team_message)
        assert "parse_project_command" in source
        assert "handle_project_command" in source
