"""Tests for [project] tag extraction in the message router."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anchovies.router import (
    extract_project_tag,
    infer_project_from_paths,
    route_message,
)
from anchovies.project_registry import Project, ProjectRegistry, reset_registry


# ---------------------------------------------------------------------------
# extract_project_tag
# ---------------------------------------------------------------------------


class TestExtractProjectTag:
    def test_extracts_tag(self):
        project, cleaned = extract_project_tag("[calculator] fix the bug")
        assert project == "calculator"
        assert cleaned == "fix the bug"

    def test_case_insensitive(self):
        project, _ = extract_project_tag("[Calculator] fix bug")
        assert project == "calculator"

    def test_tag_in_middle(self):
        project, cleaned = extract_project_tag("sofia [calculator] fix the bug")
        assert project == "calculator"
        assert "[calculator]" not in cleaned
        assert "sofia" in cleaned
        assert "fix the bug" in cleaned

    def test_tag_at_end(self):
        project, cleaned = extract_project_tag("fix the bug [calculator]")
        assert project == "calculator"
        assert cleaned == "fix the bug"

    def test_no_tag(self):
        project, cleaned = extract_project_tag("fix the bug in app.py")
        assert project is None
        assert cleaned == "fix the bug in app.py"

    def test_hyphenated_name(self):
        project, _ = extract_project_tag("[my-project] do stuff")
        assert project == "my-project"

    def test_underscored_name(self):
        project, _ = extract_project_tag("[my_project] do stuff")
        assert project == "my_project"

    def test_numeric_name(self):
        project, _ = extract_project_tag("[project123] do stuff")
        assert project == "project123"

    def test_first_tag_wins(self):
        """If multiple tags, only the first is extracted."""
        project, cleaned = extract_project_tag("[first] and [second] here")
        assert project == "first"
        # Second tag remains in the cleaned text
        assert "[second]" in cleaned

    def test_empty_brackets_not_matched(self):
        """[] should not match — needs at least one character."""
        project, cleaned = extract_project_tag("[] empty brackets")
        assert project is None

    def test_spaces_in_brackets_not_matched(self):
        """[with spaces] should not match — slug must be a-z0-9_-."""
        project, cleaned = extract_project_tag("[with spaces] not valid")
        assert project is None


# ---------------------------------------------------------------------------
# infer_project_from_paths
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_with_projects(tmp_path, monkeypatch):
    """Set up a registry with two projects for path inference testing."""
    reset_registry()

    calc_dir = tmp_path / "calculator"
    calc_dir.mkdir()
    brain_dir = tmp_path / "paradise_brain"
    brain_dir.mkdir()

    reg = ProjectRegistry(tmp_path / "empty.yaml")
    reg.register(Project(
        name="calculator", display_name="Calculator",
        context_base=calc_dir, working_dir=calc_dir,
    ))
    reg.register(Project(
        name="brain", display_name="Paradise Brain",
        context_base=brain_dir, working_dir=brain_dir,
    ))

    import anchovies.project_registry as pr_module
    monkeypatch.setattr(pr_module, "_registry", reg)

    yield reg, calc_dir, brain_dir

    reset_registry()


class TestInferProjectFromPaths:
    def test_infers_from_absolute_path(self, registry_with_projects):
        reg, calc_dir, _ = registry_with_projects
        result = infer_project_from_paths(f"fix bug in {calc_dir}/app.py", registry=reg)
        assert result == "calculator"

    def test_infers_different_project(self, registry_with_projects):
        reg, _, brain_dir = registry_with_projects
        result = infer_project_from_paths(f"update {brain_dir}/config.py", registry=reg)
        assert result == "brain"

    def test_tilde_path_no_crash(self, registry_with_projects):
        """~ paths should not crash even when they don't match."""
        reg, _, _ = registry_with_projects
        result = infer_project_from_paths("fix ~/some/random/path.py", registry=reg)
        assert result is None or isinstance(result, str)

    def test_no_match_returns_none(self, registry_with_projects):
        reg, _, _ = registry_with_projects
        result = infer_project_from_paths("fix the bug please", registry=reg)
        assert result is None

    def test_empty_registry_returns_none(self, tmp_path):
        empty_reg = ProjectRegistry(tmp_path / "empty.yaml")
        result = infer_project_from_paths("/some/path/file.py", registry=empty_reg)
        assert result is None


# ---------------------------------------------------------------------------
# route_message integration
# ---------------------------------------------------------------------------


class TestRouteMessageProject:
    def test_project_extracted_from_tag(self):
        result = route_message("[calculator] @sofia fix the bug")
        assert result.project == "calculator"
        assert "[calculator]" not in result.cleaned_message

    def test_no_tag_project_is_none(self):
        result = route_message("@sofia fix the bug")
        # Without registry, inference also returns None
        assert result.project is None

    def test_project_and_member_both_extracted(self):
        result = route_message("[myproject] @sofia fix the bug in app.py")
        assert result.project == "myproject"
        assert "sofia" in result.members
        assert "fix the bug" in result.cleaned_message

    def test_project_with_broadcast(self):
        result = route_message("[myproject] @all update your status")
        assert result.project == "myproject"
        assert result.is_broadcast is True

    def test_project_with_default_member(self):
        """Message with project tag but no member should default to marcus."""
        result = route_message("[calculator] what's the status?")
        assert result.project == "calculator"
        assert "marcus" in result.members

    def test_backwards_compatible_no_tag(self):
        """Without any project tag, routing works exactly as before."""
        result = route_message("hey sofia, can you help?")
        assert result.project is None
        assert "sofia" in result.members
