"""Tests for the reflection system (auto + manual)."""

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from anchovies import config
from anchovies.reflection import (
    _build_auto_reflect_prompt,
    _build_manual_reflect_prompt,
    auto_reflect,
    format_reflection_entry,
    manual_reflect,
    _append_to_memory,
)
from anchovies.storage import Storage, reset_storage


@pytest.fixture
def memory_setup(tmp_path, monkeypatch):
    """Set up memory directory for reflection tests."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "memory_sofia.md").write_text("# Sofia's Memory\n\n## Old lesson\nSomething learned.\n")
    monkeypatch.setattr(config, "BOT_DIR", tmp_path)
    monkeypatch.setattr(config, "CHAT_MODEL", "haiku")
    return memory_dir


@pytest.fixture
def fresh_storage(tmp_path, monkeypatch):
    reset_storage()
    storage = Storage(tmp_path / "test.db")
    import anchovies.storage as sm
    monkeypatch.setattr(sm, "_storage", storage)
    yield storage
    storage.close()
    reset_storage()


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


class TestPromptBuilding:
    def test_auto_reflect_prompt_includes_task(self):
        prompt = _build_auto_reflect_prompt("sofia", "fix the multiply bug")
        assert "fix the multiply bug" in prompt
        assert "Sofia" in prompt

    def test_auto_reflect_prompt_includes_project(self):
        prompt = _build_auto_reflect_prompt("sofia", "fix bug", project="calculator")
        assert "calculator" in prompt

    def test_manual_reflect_prompt_deeper(self):
        prompt = _build_manual_reflect_prompt("sofia", project="calculator")
        assert "Skills" in prompt
        assert "Patterns" in prompt
        assert "Collaboration" in prompt

    def test_manual_reflect_includes_memory(self):
        prompt = _build_manual_reflect_prompt("sofia", recent_memory="## Old lesson\nDon't mock the DB")
        assert "Don't mock the DB" in prompt


# ---------------------------------------------------------------------------
# Format entry
# ---------------------------------------------------------------------------


class TestFormatEntry:
    def test_basic_format(self):
        entry = format_reflection_entry("sofia", "fix multiply", "- Fixed the bug\n- Learned about edge cases")
        assert "Reflection:" in entry
        assert "fix multiply" in entry
        assert "Fixed the bug" in entry

    def test_deep_reflection_tag(self):
        entry = format_reflection_entry("sofia", "deep", "- Skills grew", deep=True)
        assert "[Deep Reflection]" in entry

    def test_project_tag(self):
        entry = format_reflection_entry("sofia", "task", "- Done", project="calculator")
        assert "[calculator]" in entry

    def test_no_project_tag_when_none(self):
        entry = format_reflection_entry("sofia", "task", "- Done", project=None)
        assert "[None]" not in entry


# ---------------------------------------------------------------------------
# Memory appending
# ---------------------------------------------------------------------------


class TestAppendToMemory:
    def test_appends_to_existing_file(self, memory_setup):
        _append_to_memory("sofia", "\n## New reflection\n- Something new\n")
        content = (memory_setup / "memory_sofia.md").read_text()
        assert "Old lesson" in content  # original content preserved
        assert "New reflection" in content  # new content added

    def test_creates_file_if_missing(self, memory_setup):
        _append_to_memory("leo", "\n## Leo's first reflection\n- Learned stuff\n")
        path = memory_setup / "memory_leo.md"
        assert path.exists()
        assert "Leo's first reflection" in path.read_text()


# ---------------------------------------------------------------------------
# Auto-reflect (mocked CLI)
# ---------------------------------------------------------------------------


class TestAutoReflect:
    @pytest.mark.anyio
    async def test_auto_reflect_saves_to_memory(self, memory_setup, fresh_storage):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "- Fixed the multiply function\n- Learned about zero division"

            result = await auto_reflect("sofia", "fix multiply bug", project="calculator")

        assert result is not None
        assert "multiply" in result

        # Memory file should have the reflection
        content = (memory_setup / "memory_sofia.md").read_text()
        assert "multiply" in content.lower()
        assert "Reflection:" in content

    @pytest.mark.anyio
    async def test_auto_reflect_logs_audit(self, memory_setup, fresh_storage):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "- Done"
            await auto_reflect("sofia", "task", project="calc")

        events = fresh_storage.query_audit(event_type="reflection")
        assert len(events) == 1
        assert events[0].details["type"] == "auto"

    @pytest.mark.anyio
    async def test_auto_reflect_uses_haiku(self, memory_setup, fresh_storage):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "- Reflection"
            await auto_reflect("sofia", "task")

        mock_cli.assert_called_once()
        assert mock_cli.call_args.kwargs.get("model") == "haiku"

    @pytest.mark.anyio
    async def test_auto_reflect_handles_failure(self, memory_setup):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.side_effect = Exception("CLI failed")
            result = await auto_reflect("sofia", "task")

        assert result is None  # Doesn't crash, returns None


# ---------------------------------------------------------------------------
# Manual reflect (mocked CLI)
# ---------------------------------------------------------------------------


class TestManualReflect:
    @pytest.mark.anyio
    async def test_manual_reflect_saves_deep_tag(self, memory_setup, fresh_storage):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "## Skills\n- Grew in testing\n## Patterns\n- TDD works"

            result = await manual_reflect("sofia", project="calculator")

        assert result is not None
        content = (memory_setup / "memory_sofia.md").read_text()
        assert "[Deep Reflection]" in content

    @pytest.mark.anyio
    async def test_manual_reflect_reads_existing_memory(self, memory_setup, fresh_storage):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "- Reflection based on history"
            await manual_reflect("sofia")

        # The prompt should have included the existing memory
        call_prompt = mock_cli.call_args[0][0]
        assert "Old lesson" in call_prompt


# ---------------------------------------------------------------------------
# Handler integration
# ---------------------------------------------------------------------------


class TestHandlerIntegration:
    def test_reflect_command_in_handler(self):
        from anchovies import handlers
        source = inspect.getsource(handlers._handle_control_command)
        assert "reflect" in source
        assert "manual_reflect" in source

    def test_completion_has_reflect_trigger(self):
        from anchovies.work_sessions import completion
        source = inspect.getsource(completion)
        assert "auto_reflect" in source or "trigger_auto_reflect" in source
