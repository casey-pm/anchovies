"""Tests for structured JSON logging configuration."""

import json
import logging
from pathlib import Path

import pytest

from anchovies.logging_config import JSONFormatter, setup_logging


class TestJSONFormatter:
    def test_produces_valid_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="hello world", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"

    def test_includes_timestamp(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="msg", args=(), exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert "timestamp" in parsed
        assert "T" in parsed["timestamp"]  # ISO format

    def test_includes_module(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="msg", args=(), exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert "module" in parsed

    def test_includes_exception(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="test.py",
                lineno=1, msg="error occurred", args=(), exc_info=sys.exc_info(),
            )
        parsed = json.loads(formatter.format(record))
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]

    def test_includes_extra_fields(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="msg", args=(), exc_info=None,
        )
        record.member = "sofia"
        record.event_type = "session_started"
        parsed = json.loads(formatter.format(record))
        assert parsed["member"] == "sofia"
        assert parsed["event_type"] == "session_started"

    def test_single_line_json(self):
        """Each log entry must be a single line (for grep/jq friendliness)."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="multi\nline\nmessage", args=(), exc_info=None,
        )
        output = formatter.format(record)
        # The JSON itself should be one line (newlines in message are escaped)
        assert "\n" not in output


class TestSetupLogging:
    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "logs"
        assert not log_dir.exists()
        setup_logging(log_dir=log_dir, log_file=log_dir / "test.log")
        assert log_dir.exists()
        # Cleanup
        logging.getLogger().handlers.clear()

    def test_creates_log_file(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_file = log_dir / "test.log"
        setup_logging(log_dir=log_dir, log_file=log_file)

        # Write a log message
        logger = logging.getLogger("test_file_creation")
        logger.info("test message")

        # Force flush
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert log_file.exists()
        content = log_file.read_text()
        assert "test message" in content
        # Should be valid JSON
        parsed = json.loads(content.strip().split("\n")[0])
        assert parsed["message"] == "test message"

        logging.getLogger().handlers.clear()

    def test_file_disabled(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir=log_dir, enable_file=False)
        assert not log_dir.exists()
        logging.getLogger().handlers.clear()

    def test_has_console_handler(self, tmp_path):
        setup_logging(enable_file=False)
        root = logging.getLogger()
        console_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert len(console_handlers) >= 1
        logging.getLogger().handlers.clear()

    def test_quiets_noisy_libraries(self, tmp_path):
        setup_logging(enable_file=False)
        assert logging.getLogger("slack_bolt").level >= logging.WARNING
        assert logging.getLogger("slack_sdk").level >= logging.WARNING
        logging.getLogger().handlers.clear()

    def test_log_level_configurable(self, tmp_path):
        setup_logging(level="DEBUG", enable_file=False)
        assert logging.getLogger().level == logging.DEBUG
        logging.getLogger().handlers.clear()
