"""Tests for skill tracking from reflections."""

from pathlib import Path

import pytest
import yaml

from anchovies import config
from anchovies.skill_tracking import (
    extract_skills_from_reflection,
    format_skills_for_prompt,
    get_skill_names,
    load_acquired_skills,
    save_skills_to_profile,
)


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "profile_sofia.yaml").write_text(yaml.dump({
        "name": "Sofia", "nickname": "dbt Queen", "role": "Analytics Engineer",
        "expertise": ["dbt", "SQL"],
    }))
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    return d


# ---------------------------------------------------------------------------
# extract_skills_from_reflection
# ---------------------------------------------------------------------------


class TestExtractSkills:
    def test_extracts_learned_about(self):
        text = "- Learned about zero-division handling\n- Learned how to write fixtures"
        skills = extract_skills_from_reflection(text)
        assert any("zero-division" in s for s in skills)
        assert any("fixtures" in s for s in skills)

    def test_extracts_used_applied(self):
        text = "- Used pytest for the first time\n- Applied error handling patterns"
        skills = extract_skills_from_reflection(text)
        assert "pytest" in skills

    def test_extracts_tech_terms(self):
        text = "Fixed a bug using Python and wrote a unit test with pytest"
        skills = extract_skills_from_reflection(text)
        assert "python" in skills
        assert "pytest" in skills

    def test_deduplicates(self):
        text = "Used pytest. Also applied pytest fixtures. pytest is great."
        skills = extract_skills_from_reflection(text)
        assert skills.count("pytest") == 1

    def test_caps_at_10(self):
        text = "\n".join(f"- Learned about skill{i} and used technique{i}" for i in range(20))
        skills = extract_skills_from_reflection(text)
        assert len(skills) <= 10

    def test_empty_text(self):
        assert extract_skills_from_reflection("") == []

    def test_no_skills_in_generic_text(self):
        skills = extract_skills_from_reflection("Everything went well today.")
        # May find some or none — shouldn't crash
        assert isinstance(skills, list)


# ---------------------------------------------------------------------------
# save_skills_to_profile
# ---------------------------------------------------------------------------


class TestSaveSkills:
    def test_saves_new_skills(self, profiles_dir):
        added = save_skills_to_profile("sofia", ["error handling", "zero division"], project="calculator")
        assert added == 2

        data = yaml.safe_load((profiles_dir / "profile_sofia.yaml").read_text())
        assert len(data["acquired_skills"]) == 2
        assert data["acquired_skills"][0]["skill"] == "error handling"
        assert data["acquired_skills"][0]["project"] == "calculator"

    def test_no_duplicates(self, profiles_dir):
        save_skills_to_profile("sofia", ["pytest"])
        save_skills_to_profile("sofia", ["pytest", "dbt"])  # pytest already exists
        data = yaml.safe_load((profiles_dir / "profile_sofia.yaml").read_text())
        pytest_count = sum(1 for s in data["acquired_skills"] if s["skill"] == "pytest")
        assert pytest_count == 1

    def test_returns_zero_for_all_duplicates(self, profiles_dir):
        save_skills_to_profile("sofia", ["pytest"])
        added = save_skills_to_profile("sofia", ["pytest"])
        assert added == 0

    def test_missing_profile(self, profiles_dir):
        added = save_skills_to_profile("nonexistent", ["skill"])
        assert added == 0

    def test_preserves_existing_profile_data(self, profiles_dir):
        save_skills_to_profile("sofia", ["new skill"])
        data = yaml.safe_load((profiles_dir / "profile_sofia.yaml").read_text())
        assert data["name"] == "Sofia"
        assert data["nickname"] == "dbt Queen"
        assert "acquired_skills" in data


# ---------------------------------------------------------------------------
# load / get
# ---------------------------------------------------------------------------


class TestLoadSkills:
    def test_load_empty(self, profiles_dir):
        skills = load_acquired_skills("sofia")
        assert skills == []

    def test_load_after_save(self, profiles_dir):
        save_skills_to_profile("sofia", ["pytest", "asyncio"])
        skills = load_acquired_skills("sofia")
        assert len(skills) == 2

    def test_get_skill_names(self, profiles_dir):
        save_skills_to_profile("sofia", ["pytest", "dbt"])
        names = get_skill_names("sofia")
        assert "pytest" in names
        assert "dbt" in names


# ---------------------------------------------------------------------------
# format_skills_for_prompt
# ---------------------------------------------------------------------------


class TestFormatForPrompt:
    def test_empty_when_no_skills(self, profiles_dir):
        assert format_skills_for_prompt("sofia") == ""

    def test_formats_with_skills(self, profiles_dir):
        save_skills_to_profile("sofia", ["pytest", "error handling"], project="calc")
        result = format_skills_for_prompt("sofia")
        assert "Acquired Skills" in result
        assert "pytest" in result
        assert "calc" in result

    def test_limits_to_10(self, profiles_dir):
        save_skills_to_profile("sofia", [f"skill{i}" for i in range(15)])
        result = format_skills_for_prompt("sofia")
        # Should show at most 10
        assert result.count("- ") <= 10


# ---------------------------------------------------------------------------
# Integration with reflection
# ---------------------------------------------------------------------------


class TestReflectionIntegration:
    def test_reflection_extracts_skills(self):
        """Verify reflection.py imports and calls skill extraction."""
        import inspect
        from anchovies import reflection
        source = inspect.getsource(reflection.auto_reflect)
        assert "extract_skills_from_reflection" in source
        assert "save_skills_to_profile" in source

    def test_prompt_builder_includes_skills(self):
        """Verify prompt_builder loads acquired skills."""
        import inspect
        from anchovies.chat_hub import prompt_builder
        source = inspect.getsource(prompt_builder.build_task_prompt)
        assert "format_skills_for_prompt" in source
