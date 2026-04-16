"""Tests for project-aware context loading and prompt building (Phase 3)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from anchovies import config
from anchovies.context import load_member_context
from anchovies.chat_hub.prompt_builder import build_task_prompt
from anchovies.project_registry import Project, ProjectRegistry, reset_registry


@pytest.fixture
def project_setup(tmp_path, monkeypatch):
    """Set up a project with its own status directory + persona profiles."""
    # Project context directory with status files
    project_ctx = tmp_path / "calculator_ctx"
    (project_ctx / "status").mkdir(parents=True)
    (project_ctx / "status" / "status_sofia.md").write_text(
        "# Sofia on Calculator\n\nFixed the multiply function."
    )

    # Global/persona-level directories
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "profile_sofia.yaml").write_text(yaml.dump({
        "name": "Sofia",
        "nickname": "dbt Queen",
        "role": "Analytics Engineer",
    }))
    (profiles_dir / "profile_marcus.yaml").write_text(yaml.dump({
        "name": "Marcus",
        "nickname": "Boss",
        "role": "BI Manager",
    }))

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "memory_sofia.md").write_text("## Lesson 1\nAlways test dbt models.")

    global_status_dir = tmp_path / "global_status"
    (global_status_dir / "status").mkdir(parents=True)
    (global_status_dir / "status" / "status_sofia.md").write_text(
        "# Sofia General Status\n\nWorking across multiple projects."
    )

    # Patch config
    monkeypatch.setattr(config, "PROFILES_DIR", profiles_dir)
    monkeypatch.setattr(config, "BOT_DIR", tmp_path)
    monkeypatch.setattr(config, "CONTEXT_BASE", global_status_dir)
    monkeypatch.setattr(config, "PROJECT_NAME", "the current project")

    # Set up project registry
    reset_registry()
    reg = ProjectRegistry(tmp_path / "empty.yaml")
    reg.register(Project(
        name="calculator",
        display_name="Calculator App",
        context_base=project_ctx,
        working_dir=project_ctx,
        description="A test calculator",
    ))
    import anchovies.project_registry as pr_module
    monkeypatch.setattr(pr_module, "_registry", reg)

    yield {
        "project_ctx": project_ctx,
        "global_status_dir": global_status_dir,
        "profiles_dir": profiles_dir,
        "memory_dir": memory_dir,
        "registry": reg,
    }

    reset_registry()


# ---------------------------------------------------------------------------
# load_member_context
# ---------------------------------------------------------------------------


class TestLoadMemberContextProject:
    def test_with_project_loads_project_status(self, project_setup):
        ctx = load_member_context("sofia", project="calculator")
        assert "Calculator" in ctx.status_content
        assert "multiply" in ctx.status_content

    def test_without_project_loads_global_status(self, project_setup):
        ctx = load_member_context("sofia")
        assert "General Status" in ctx.status_content
        assert "multiple projects" in ctx.status_content

    def test_memory_always_persona_level(self, project_setup):
        """Memory should come from persona dir, not project dir, regardless of project param."""
        ctx_with = load_member_context("sofia", project="calculator")
        ctx_without = load_member_context("sofia")
        # Both should have the same memory content
        assert ctx_with.memory_content == ctx_without.memory_content
        assert "Always test dbt models" in ctx_with.memory_content

    def test_unknown_project_falls_back(self, project_setup):
        """If project slug doesn't match any registered project, fall back to global."""
        ctx = load_member_context("sofia", project="nonexistent")
        assert "General Status" in ctx.status_content

    def test_none_project_same_as_no_project(self, project_setup):
        ctx_none = load_member_context("sofia", project=None)
        ctx_no_arg = load_member_context("sofia")
        assert ctx_none.status_content == ctx_no_arg.status_content


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPromptProject:
    def test_with_project_name(self, project_setup):
        ctx = load_member_context("sofia", project="calculator")
        prompt = ctx.build_system_prompt(project_name="Calculator App")
        assert "Calculator App team" in prompt

    def test_without_project_uses_config(self, project_setup):
        ctx = load_member_context("sofia")
        prompt = ctx.build_system_prompt()
        assert "the current project team" in prompt


# ---------------------------------------------------------------------------
# build_task_prompt
# ---------------------------------------------------------------------------


class TestBuildTaskPromptProject:
    def test_with_project_uses_display_name(self, project_setup):
        prompt = build_task_prompt(
            persona="sofia",
            task_description="fix multiply function",
            project="calculator",
        )
        assert "Calculator App" in prompt

    def test_without_project_uses_config_name(self, project_setup):
        prompt = build_task_prompt(
            persona="sofia",
            task_description="fix something",
        )
        assert "the current project" in prompt

    def test_with_project_status_path_points_to_project(self, project_setup):
        prompt = build_task_prompt(
            persona="sofia",
            task_description="fix multiply function",
            project="calculator",
        )
        # The completion instructions should reference the project's status path
        assert "calculator_ctx" in prompt

    def test_unknown_project_falls_back(self, project_setup):
        prompt = build_task_prompt(
            persona="sofia",
            task_description="fix something",
            project="nonexistent",
        )
        assert "the current project" in prompt


# ---------------------------------------------------------------------------
# hub.process_message returns project
# ---------------------------------------------------------------------------


class TestHubProjectPassthrough:
    @pytest.mark.anyio
    async def test_chat_response_includes_project(self, project_setup):
        from anchovies.chat_hub.hub import create_chat_hub
        hub = create_chat_hub()

        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "Marcus here."

            result = await hub.process_message(
                "what's the status?",
                project="calculator",
            )

        assert result["project"] == "calculator"

    @pytest.mark.anyio
    async def test_work_request_includes_project(self, project_setup):
        from anchovies.chat_hub.hub import create_chat_hub
        hub = create_chat_hub()

        result = await hub.process_message(
            "fix the multiply bug in calculator.py",
            project="calculator",
        )

        assert result.get("project") == "calculator"

    @pytest.mark.anyio
    async def test_no_project_returns_none(self, project_setup):
        from anchovies.chat_hub.hub import create_chat_hub
        hub = create_chat_hub()

        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "Reply"
            result = await hub.process_message("hello")

        assert result.get("project") is None


# ---------------------------------------------------------------------------
# hub.get_system_prompt augments with project status
# ---------------------------------------------------------------------------


class TestHubSystemPromptProject:
    def test_project_aware_prompt_includes_project_name(self, project_setup):
        from anchovies.chat_hub.hub import create_chat_hub
        hub = create_chat_hub()
        prompt = hub.get_system_prompt(project="calculator")
        assert "Calculator App" in prompt

    def test_project_aware_prompt_includes_member_status(self, project_setup):
        from anchovies.chat_hub.hub import create_chat_hub
        hub = create_chat_hub()
        prompt = hub.get_system_prompt(project="calculator")
        # Sofia's project status should be included
        assert "multiply" in prompt.lower() or "Sofia" in prompt

    def test_no_project_uses_default(self, project_setup):
        from anchovies.chat_hub.hub import create_chat_hub
        hub = create_chat_hub()
        prompt = hub.get_system_prompt()
        assert "the current project" in prompt
