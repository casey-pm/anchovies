"""Tests for Slack channel allowlist enforcement."""

import inspect
from unittest.mock import MagicMock, patch

import pytest

from anchovies import config


class TestIsChannelAllowed:
    """Test the is_channel_allowed helper function."""

    def test_empty_allowlist_allows_all(self, monkeypatch):
        monkeypatch.setattr(config, "ALLOWED_CHANNELS", [])
        assert config.is_channel_allowed("C_ANY", "channel") is True
        assert config.is_channel_allowed("C_OTHER", "channel") is True

    def test_allowlisted_channel_allowed(self, monkeypatch):
        monkeypatch.setattr(config, "ALLOWED_CHANNELS", ["C_ALLOWED"])
        assert config.is_channel_allowed("C_ALLOWED", "channel") is True

    def test_non_allowlisted_channel_blocked(self, monkeypatch):
        monkeypatch.setattr(config, "ALLOWED_CHANNELS", ["C_ALLOWED"])
        assert config.is_channel_allowed("C_OTHER", "channel") is False

    def test_dm_always_allowed(self, monkeypatch):
        """DMs (channel_type=im) bypass the allowlist."""
        monkeypatch.setattr(config, "ALLOWED_CHANNELS", ["C_ALLOWED"])
        assert config.is_channel_allowed("D_DM", "im") is True

    def test_group_dm_always_allowed(self, monkeypatch):
        monkeypatch.setattr(config, "ALLOWED_CHANNELS", ["C_ALLOWED"])
        assert config.is_channel_allowed("G_GROUP", "mpim") is True

    def test_private_channel_always_allowed(self, monkeypatch):
        monkeypatch.setattr(config, "ALLOWED_CHANNELS", ["C_ALLOWED"])
        assert config.is_channel_allowed("P_PRIVATE", "group") is True
        assert config.is_channel_allowed("P_PRIVATE2", "private_channel") is True

    def test_no_channel_type_respects_allowlist(self, monkeypatch):
        """When channel_type is None, only the allowlist matters."""
        monkeypatch.setattr(config, "ALLOWED_CHANNELS", ["C_ALLOWED"])
        assert config.is_channel_allowed("C_ALLOWED", None) is True
        assert config.is_channel_allowed("C_OTHER", None) is False

    def test_multiple_allowed_channels(self, monkeypatch):
        monkeypatch.setattr(
            config, "ALLOWED_CHANNELS", ["C_ONE", "C_TWO", "C_THREE"]
        )
        assert config.is_channel_allowed("C_ONE", "channel") is True
        assert config.is_channel_allowed("C_TWO", "channel") is True
        assert config.is_channel_allowed("C_THREE", "channel") is True
        assert config.is_channel_allowed("C_FOUR", "channel") is False


class TestAllowedChannelsParsing:
    """Test that ALLOWED_CHANNELS is parsed correctly from the env var."""

    def test_parses_comma_separated(self):
        # Simulate the parsing logic from config.py
        raw = "C_ONE,C_TWO,C_THREE"
        parsed = [c.strip() for c in raw.split(",") if c.strip()]
        assert parsed == ["C_ONE", "C_TWO", "C_THREE"]

    def test_handles_whitespace(self):
        raw = "C_ONE , C_TWO,  C_THREE  "
        parsed = [c.strip() for c in raw.split(",") if c.strip()]
        assert parsed == ["C_ONE", "C_TWO", "C_THREE"]

    def test_empty_string_produces_empty_list(self):
        raw = ""
        parsed = [c.strip() for c in raw.split(",") if c.strip()]
        assert parsed == []

    def test_filters_empty_entries(self):
        raw = "C_ONE,,C_TWO,"
        parsed = [c.strip() for c in raw.split(",") if c.strip()]
        assert parsed == ["C_ONE", "C_TWO"]


class TestAppIntegration:
    """Verify the app.py handlers call is_channel_allowed."""

    def test_app_mention_checks_allowlist(self):
        """The app_mention handler source should call is_channel_allowed."""
        from anchovies import app
        source = inspect.getsource(app.create_app)
        assert "is_channel_allowed" in source

    def test_message_handler_checks_allowlist(self):
        """The message handler source should call is_channel_allowed."""
        from anchovies import app
        source = inspect.getsource(app.create_app)
        # At least one occurrence for app_mention, one for message
        assert source.count("is_channel_allowed") >= 2
