"""Tests for the audit trail CLI."""

import time

import pytest

from anchovies.audit import format_entry, format_timestamp, main, parse_duration
from anchovies.storage import AuditEntry, Storage, reset_storage


# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------


class TestParseDuration:
    def test_hours(self):
        assert parse_duration("24h") == 86400

    def test_minutes(self):
        assert parse_duration("30m") == 1800

    def test_days(self):
        assert parse_duration("7d") == 604800

    def test_seconds(self):
        assert parse_duration("60s") == 60

    def test_with_spaces(self):
        assert parse_duration("  24h  ") == 86400

    def test_case_insensitive(self):
        assert parse_duration("24H") == 86400

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_duration("abc")
        with pytest.raises(ValueError):
            parse_duration("24x")
        with pytest.raises(ValueError):
            parse_duration("")


# ---------------------------------------------------------------------------
# format functions
# ---------------------------------------------------------------------------


class TestFormatTimestamp:
    def test_produces_readable_string(self):
        ts = 1713200000.0  # some timestamp
        result = format_timestamp(ts)
        assert "-" in result  # date separator
        assert ":" in result  # time separator


class TestFormatEntry:
    def test_basic_entry(self):
        entry = AuditEntry(
            id=1, timestamp=time.time(), event_type="session_started",
            member="sofia", details={"task": "fix bug"},
        )
        line = format_entry(entry)
        assert "session_started" in line
        assert "sofia" in line
        assert "fix bug" in line

    def test_no_member(self):
        entry = AuditEntry(
            id=1, timestamp=time.time(), event_type="bot_started",
            member=None, details={},
        )
        line = format_entry(entry)
        assert "bot_started" in line
        assert "-" in line  # member placeholder

    def test_long_details_truncated(self):
        entry = AuditEntry(
            id=1, timestamp=time.time(), event_type="test",
            member="sofia", details={"long_value": "x" * 100},
        )
        line = format_entry(entry)
        assert "..." in line


# ---------------------------------------------------------------------------
# CLI main function
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_storage(tmp_path, monkeypatch):
    """Create a storage with some audit events."""
    reset_storage()
    db_path = tmp_path / "test.db"
    storage = Storage(db_path)
    import anchovies.storage as storage_module
    monkeypatch.setattr(storage_module, "_storage", storage)

    # Add some events
    storage.log_event("bot_started", details={"version": "0.1"})
    time.sleep(0.01)
    storage.log_event("session_started", member="sofia", details={"task": "fix bug"})
    time.sleep(0.01)
    storage.log_event("session_completed", member="sofia", details={"task": "fix bug"})
    time.sleep(0.01)
    storage.log_event("session_started", member="leo", details={"task": "write tests"})

    yield storage
    storage.close()
    reset_storage()


class TestCLI:
    def test_no_args_shows_events(self, populated_storage, capsys):
        exit_code = main([])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "Audit Trail" in output
        assert "session_started" in output

    def test_filter_by_member(self, populated_storage, capsys):
        exit_code = main(["--member", "sofia"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "sofia" in output
        assert "leo" not in output

    def test_filter_by_type(self, populated_storage, capsys):
        exit_code = main(["--type", "session_started"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "session_started" in output
        assert "bot_started" not in output

    def test_filter_by_last(self, populated_storage, capsys):
        exit_code = main(["--last", "1h"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "Audit Trail" in output

    def test_limit(self, populated_storage, capsys):
        exit_code = main(["--limit", "2"])
        assert exit_code == 0
        output = capsys.readouterr().out
        # Should have at most 2 event lines (plus header)
        lines = [l for l in output.strip().split("\n") if l.strip() and not l.startswith("  -") and "Audit Trail" not in l and "TIMESTAMP" not in l]
        assert len(lines) <= 2

    def test_no_results(self, populated_storage, capsys):
        exit_code = main(["--member", "nonexistent"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "No audit events" in output

    def test_invalid_duration(self, capsys):
        exit_code = main(["--last", "abc"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "Invalid duration" in err

    def test_combined_filters(self, populated_storage, capsys):
        exit_code = main(["--member", "sofia", "--type", "session_completed", "--last", "1h"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "session_completed" in output
