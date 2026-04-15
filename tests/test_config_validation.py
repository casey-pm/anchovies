"""Tests for enhanced config validation (Phase 2.8)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from anchovies import config


@pytest.fixture
def all_valid(monkeypatch, tmp_path):
    """A config state where every check would pass."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "profile_marcus.yaml").write_text("name: Marcus\n")
    context_dir = tmp_path / "context"
    context_dir.mkdir()

    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-valid")
    monkeypatch.setattr(config, "SLACK_APP_TOKEN", "xapp-valid")
    monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "secret")
    monkeypatch.setattr(config, "CONTEXT_BASE", context_dir)
    monkeypatch.setattr(config, "PROFILES_DIR", profiles_dir)
    yield


# ---------------------------------------------------------------------------
# Slack credential checks
# ---------------------------------------------------------------------------


class TestSlackCredentials:
    def test_missing_bot_token(self, all_valid, monkeypatch):
        monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
        ok, errors = config.validate_config(strict=False)
        assert ok is False
        assert any("SLACK_BOT_TOKEN" in e for e in errors)

    def test_malformed_bot_token(self, all_valid, monkeypatch):
        monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "wrong-prefix-token")
        ok, errors = config.validate_config(strict=False)
        assert ok is False
        assert any("xoxb-" in e for e in errors)

    def test_missing_app_token(self, all_valid, monkeypatch):
        monkeypatch.setattr(config, "SLACK_APP_TOKEN", "")
        ok, errors = config.validate_config(strict=False)
        assert ok is False
        assert any("SLACK_APP_TOKEN" in e for e in errors)

    def test_malformed_app_token(self, all_valid, monkeypatch):
        monkeypatch.setattr(config, "SLACK_APP_TOKEN", "xoxb-wrong")
        ok, errors = config.validate_config(strict=False)
        assert ok is False
        assert any("xapp-" in e for e in errors)

    def test_missing_signing_secret(self, all_valid, monkeypatch):
        monkeypatch.setattr(config, "SLACK_SIGNING_SECRET", "")
        ok, errors = config.validate_config(strict=False)
        assert ok is False
        assert any("SIGNING_SECRET" in e for e in errors)


# ---------------------------------------------------------------------------
# Path checks
# ---------------------------------------------------------------------------


class TestPathValidation:
    def test_missing_context_base(self, all_valid, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "CONTEXT_BASE", tmp_path / "nonexistent")
        ok, errors = config.validate_config(strict=False)
        assert ok is False
        assert any("CONTEXT_BASE" in e for e in errors)

    def test_missing_profiles_dir(self, all_valid, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "nonexistent")
        ok, errors = config.validate_config(strict=False)
        assert ok is False
        assert any("PROFILES_DIR" in e for e in errors)

    def test_empty_profiles_dir(self, all_valid, monkeypatch, tmp_path):
        empty_dir = tmp_path / "empty_profiles"
        empty_dir.mkdir()
        monkeypatch.setattr(config, "PROFILES_DIR", empty_dir)
        ok, errors = config.validate_config(strict=False)
        assert ok is False
        assert any("no profile_*.yaml" in e for e in errors)

    def test_profiles_dir_with_yaml_passes(self, all_valid, monkeypatch, tmp_path):
        profiles = tmp_path / "p"
        profiles.mkdir()
        (profiles / "profile_sofia.yaml").write_text("name: Sofia\n")
        monkeypatch.setattr(config, "PROFILES_DIR", profiles)
        ok, errors = config.validate_config(strict=False)
        # Path errors should not include profiles
        assert not any("PROFILES_DIR" in e for e in errors)


# ---------------------------------------------------------------------------
# External binary checks (strict mode)
# ---------------------------------------------------------------------------


class TestExternalBinaries:
    def test_missing_tmux(self, all_valid, monkeypatch):
        original_which = config.shutil.which

        def fake_which(binary):
            if binary == "tmux":
                return None
            return original_which(binary)

        monkeypatch.setattr(config.shutil, "which", fake_which)
        ok, errors = config.validate_config(strict=True)
        assert ok is False
        assert any("tmux" in e for e in errors)

    def test_missing_claude(self, all_valid, monkeypatch):
        original_which = config.shutil.which

        def fake_which(binary):
            if binary == config.CLAUDE_CLI_PATH:
                return None
            return original_which(binary)

        monkeypatch.setattr(config.shutil, "which", fake_which)
        ok, errors = config.validate_config(strict=True)
        assert ok is False
        assert any("Claude CLI" in e for e in errors)

    def test_missing_gh(self, all_valid, monkeypatch):
        original_which = config.shutil.which

        def fake_which(binary):
            if binary == "gh":
                return None
            return original_which(binary)

        monkeypatch.setattr(config.shutil, "which", fake_which)
        ok, errors = config.validate_config(strict=True)
        assert ok is False
        assert any("GitHub CLI" in e for e in errors)

    def test_missing_git(self, all_valid, monkeypatch):
        original_which = config.shutil.which

        def fake_which(binary):
            if binary == "git":
                return None
            return original_which(binary)

        monkeypatch.setattr(config.shutil, "which", fake_which)
        ok, errors = config.validate_config(strict=True)
        assert ok is False
        assert any("git" in e.lower() for e in errors)

    def test_strict_false_skips_binary_checks(self, all_valid, monkeypatch):
        """Non-strict mode should NOT check binaries."""
        # All binaries unavailable
        monkeypatch.setattr(config.shutil, "which", lambda _: None)
        ok, errors = config.validate_config(strict=False)
        # Should pass — no binary errors
        assert ok is True
        assert errors == []


class TestAllValid:
    def test_all_valid_passes_non_strict(self, all_valid):
        ok, errors = config.validate_config(strict=False)
        assert ok is True
        assert errors == []

    def test_separation_of_concerns(self, all_valid, monkeypatch):
        """Multiple errors should accumulate, not short-circuit."""
        monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
        monkeypatch.setattr(config, "SLACK_APP_TOKEN", "")
        ok, errors = config.validate_config(strict=False)
        assert ok is False
        assert len(errors) >= 2
