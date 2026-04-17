"""Tests for project-specific decision authority."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from anchovies import config
from anchovies.chat_hub.prompt_builder import build_task_prompt
from anchovies.project_registry import Project, ProjectRegistry, reset_registry


@pytest.fixture
def authority_setup(tmp_path, monkeypatch):
    """Set up projects with and without authority configs."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "profile_sofia.yaml").write_text(yaml.dump({
        "name": "Sofia", "nickname": "dbt Queen", "role": "Analytics Engineer",
    }))

    ctx = tmp_path / "ctx"
    (ctx / "status").mkdir(parents=True)

    monkeypatch.setattr(config, "PROFILES_DIR", profiles_dir)
    monkeypatch.setattr(config, "BOT_DIR", tmp_path)
    monkeypatch.setattr(config, "CONTEXT_BASE", ctx)
    monkeypatch.setattr(config, "PROJECT_NAME", "test project")
    monkeypatch.setattr(config, "PROTECTED_FILES", [".env"])

    reset_registry()
    reg = ProjectRegistry(tmp_path / "empty.yaml")

    # Project WITH authority
    reg.register(Project(
        name="strict",
        display_name="Strict Project",
        context_base=ctx,
        working_dir=ctx,
        authority={
            "autonomous": ["Run tests", "Fix obvious bugs", "Update docs"],
            "escalate": ["Delete files", "Change API signatures", "Modify database schema"],
        },
    ))

    # Project WITHOUT authority
    reg.register(Project(
        name="relaxed",
        display_name="Relaxed Project",
        context_base=ctx,
        working_dir=ctx,
    ))

    import anchovies.project_registry as pr_module
    monkeypatch.setattr(pr_module, "_registry", reg)

    yield reg
    reset_registry()


class TestAuthorityInPrompt:
    def test_project_with_authority_injected(self, authority_setup):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug", project="strict")
        assert "Decision Authority" in prompt
        assert "Run tests" in prompt
        assert "Delete files" in prompt
        assert "autonomously" in prompt.lower()
        assert "escalate" in prompt.lower()

    def test_project_without_authority_gets_default(self, authority_setup):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug", project="relaxed")
        assert "Default" in prompt
        assert "commit to feature branch" in prompt.lower()

    def test_no_project_gets_default(self, authority_setup):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "Default" in prompt

    def test_autonomous_items_listed(self, authority_setup):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug", project="strict")
        assert "Fix obvious bugs" in prompt
        assert "Update docs" in prompt

    def test_escalate_items_listed(self, authority_setup):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug", project="strict")
        assert "Change API signatures" in prompt
        assert "Modify database schema" in prompt


class TestAuthorityInRegistry:
    def test_authority_loaded_from_yaml(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        yaml_path.write_text(yaml.dump({
            "projects": {
                "myproject": {
                    "context_base": "/tmp/ctx",
                    "authority": {
                        "autonomous": ["Run tests"],
                        "escalate": ["Delete files"],
                    },
                },
            },
        }))
        reg = ProjectRegistry(yaml_path)
        proj = reg.get("myproject")
        assert proj.authority is not None
        assert "Run tests" in proj.authority["autonomous"]
        assert "Delete files" in proj.authority["escalate"]

    def test_no_authority_in_yaml(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        yaml_path.write_text(yaml.dump({
            "projects": {"simple": {"context_base": "/tmp/ctx"}},
        }))
        reg = ProjectRegistry(yaml_path)
        proj = reg.get("simple")
        assert proj.authority is None

    def test_authority_roundtrips_through_yaml(self, tmp_path):
        yaml_path = tmp_path / "projects.yaml"
        reg = ProjectRegistry(yaml_path)
        reg.register(Project(
            name="test", display_name="Test",
            context_base=Path("/tmp/t"), working_dir=Path("/tmp/t"),
            authority={"autonomous": ["A"], "escalate": ["B"]},
        ))
        reg.save_to_yaml()

        reg2 = ProjectRegistry(yaml_path)
        proj = reg2.get("test")
        assert proj.authority == {"autonomous": ["A"], "escalate": ["B"]}
