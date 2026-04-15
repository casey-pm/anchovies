"""Tests for per-context model selection (CHAT_MODEL vs WORK_MODEL + overrides)."""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anchovies import config
from anchovies.cli_runner import generate_team_member_response, run_claude_cli
from anchovies.context import MemberProfile, load_profile


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestConfigModels:
    def test_chat_model_default(self):
        """CHAT_MODEL defaults to haiku for cheap chat responses."""
        # The config value reflects whatever is in the environment, but the
        # default (when env var unset) should be haiku
        import os
        prev = os.environ.pop("CHAT_MODEL", None)
        try:
            # Reload config to pick up the missing env var
            import importlib
            import anchovies.config as cfg
            importlib.reload(cfg)
            assert cfg.CHAT_MODEL == "haiku"
        finally:
            if prev is not None:
                os.environ["CHAT_MODEL"] = prev
            import importlib
            import anchovies.config as cfg
            importlib.reload(cfg)

    def test_work_model_default(self):
        """WORK_MODEL defaults to sonnet for capable work sessions."""
        import os
        prev = os.environ.pop("WORK_MODEL", None)
        try:
            import importlib
            import anchovies.config as cfg
            importlib.reload(cfg)
            assert cfg.WORK_MODEL == "sonnet"
        finally:
            if prev is not None:
                os.environ["WORK_MODEL"] = prev
            import importlib
            import anchovies.config as cfg
            importlib.reload(cfg)


# ---------------------------------------------------------------------------
# CLI runner accepts model
# ---------------------------------------------------------------------------


class TestRunClaudeCliModel:
    @pytest.mark.anyio
    async def test_model_passed_as_cli_flag(self):
        """The model argument should be passed to claude as --model."""
        with patch("anchovies.cli_runner.shutil.which") as mock_which, \
             patch("anchovies.cli_runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_which.return_value = "/usr/bin/claude"

            mock_proc = MagicMock()
            mock_proc.returncode = 0

            async def fake_communicate():
                return (b"response text", b"")

            mock_proc.communicate = fake_communicate
            mock_exec.return_value = mock_proc

            await run_claude_cli("test prompt", model="haiku")

            args = mock_exec.call_args[0]
            assert "--model" in args
            assert "haiku" in args

    @pytest.mark.anyio
    async def test_no_model_uses_default(self):
        """If no model is passed, falls back to CLAUDE_MODEL."""
        with patch("anchovies.cli_runner.shutil.which") as mock_which, \
             patch("anchovies.cli_runner.asyncio.create_subprocess_exec") as mock_exec, \
             patch("anchovies.cli_runner.config.CLAUDE_MODEL", "sonnet"):
            mock_which.return_value = "/usr/bin/claude"

            mock_proc = MagicMock()
            mock_proc.returncode = 0

            async def fake_communicate():
                return (b"resp", b"")

            mock_proc.communicate = fake_communicate
            mock_exec.return_value = mock_proc

            await run_claude_cli("prompt")

            args = mock_exec.call_args[0]
            # Should still include --model with sonnet
            assert "--model" in args
            assert "sonnet" in args


# ---------------------------------------------------------------------------
# Profile model_override
# ---------------------------------------------------------------------------


class TestProfileModelOverride:
    def test_member_profile_has_model_override_field(self):
        profile = MemberProfile(name="Test", model_override="opus")
        assert profile.model_override == "opus"

    def test_member_profile_default_no_override(self):
        profile = MemberProfile(name="Test")
        assert profile.model_override is None

    def test_load_profile_picks_up_override(self, tmp_path, monkeypatch):
        import yaml
        from anchovies import config as cfg
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        profile_file = profiles_dir / "profile_sofia.yaml"
        profile_file.write_text(yaml.dump({
            "name": "Sofia",
            "nickname": "dbt Queen",
            "role": "Analytics Engineer",
            "model_override": "opus",
        }))
        monkeypatch.setattr(cfg, "PROFILES_DIR", profiles_dir)

        profile = load_profile("sofia")
        assert profile.model_override == "opus"

    def test_load_profile_no_override_when_absent(self, tmp_path, monkeypatch):
        import yaml
        from anchovies import config as cfg
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        profile_file = profiles_dir / "profile_sofia.yaml"
        profile_file.write_text(yaml.dump({
            "name": "Sofia",
            "role": "Analytics Engineer",
        }))
        monkeypatch.setattr(cfg, "PROFILES_DIR", profiles_dir)

        profile = load_profile("sofia")
        assert profile.model_override is None


# ---------------------------------------------------------------------------
# Hub uses CHAT_MODEL with override
# ---------------------------------------------------------------------------


class TestHubModelSelection:
    @pytest.mark.anyio
    async def test_hub_uses_chat_model_by_default(self, anchovies_config, monkeypatch):
        from anchovies.chat_hub.hub import create_chat_hub

        monkeypatch.setattr(config, "CHAT_MODEL", "haiku")

        hub = create_chat_hub()
        # Make sure profile has no override
        hub.context.profile.model_override = None

        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "Marcus reply"
            await hub.process_message("hey there")

            # Verify model=haiku was passed
            assert mock_cli.call_args.kwargs.get("model") == "haiku"

    @pytest.mark.anyio
    async def test_hub_uses_profile_override(self, anchovies_config, monkeypatch):
        from anchovies.chat_hub.hub import create_chat_hub

        monkeypatch.setattr(config, "CHAT_MODEL", "haiku")

        hub = create_chat_hub()
        hub.context.profile.model_override = "opus"

        with patch("anchovies.chat_hub.hub.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = "Marcus reply"
            await hub.process_message("hey there")

            # Override should win over CHAT_MODEL
            assert mock_cli.call_args.kwargs.get("model") == "opus"


# ---------------------------------------------------------------------------
# Handlers use WORK_MODEL with override
# ---------------------------------------------------------------------------


class TestHandlerModelSelection:
    def test_process_member_response_resolves_work_model(self):
        """Source check that handlers.process_member_response uses WORK_MODEL."""
        from anchovies import handlers
        source = inspect.getsource(handlers.process_member_response)
        assert "WORK_MODEL" in source
        assert "model_override" in source
