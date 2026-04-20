"""
Structured logging configuration for Anchovies.

Sets up JSON-formatted log output to both console and file:
  - Console: human-readable format (existing behaviour)
  - File: JSON lines to anchovies/logs/anchovies.log (7-day rotation)

JSON log fields: timestamp, level, module, message, and any extra
fields passed via logger.info("msg", extra={...}).

Usage:
    from anchovies.logging_config import setup_logging
    setup_logging()  # Call once at startup in app.py
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Log directory — created lazily
LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "anchovies.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
LOG_BACKUP_COUNT = 7  # 7 rotated files = ~7 days at typical volume


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "function": record.funcName,
            "message": record.getMessage(),
        }

        # Include exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include any extra fields passed via logger.info("msg", extra={...})
        # Standard LogRecord attributes to skip
        _STANDARD = {
            "name", "msg", "args", "created", "relativeCreated", "thread",
            "threadName", "msecs", "pathname", "filename", "module", "exc_info",
            "exc_text", "stack_info", "lineno", "funcName", "levelno",
            "levelname", "processName", "process", "taskName",
            "message",  # from getMessage()
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                try:
                    json.dumps(value)  # only include JSON-serializable extras
                    log_entry[key] = value
                except (TypeError, ValueError):
                    log_entry[key] = str(value)

        return json.dumps(log_entry, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable format for console output."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def setup_logging(
    level: str = LOG_LEVEL,
    log_dir: Path = LOG_DIR,
    log_file: Path | None = None,
    enable_file: bool = True,
) -> None:
    """
    Configure logging for the Anchovies application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Directory for log files
        log_file: Override log file path (default: log_dir/anchovies.log)
        enable_file: If False, only console output (useful for tests)
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Clear existing handlers (prevents duplicate handlers on re-setup)
    root.handlers.clear()

    # Console handler — human-readable
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, level, logging.INFO))
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)

    # File handler — JSON, rotating
    if enable_file:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_path = log_file or (log_dir / "anchovies.log")
        file_handler = logging.handlers.RotatingFileHandler(
            str(file_path),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # File gets everything
        file_handler.setFormatter(JSONFormatter())
        root.addHandler(file_handler)

    # Human-readable session log — wiped on each restart so it only has current session.
    # NOT gitignored — Claude Code can read this to debug issues.
    session_log_path = log_dir.parent / "session.log"
    if enable_file:
        # Wipe previous session
        try:
            session_log_path.write_text("")
        except Exception:
            pass
        session_handler = logging.FileHandler(
            str(session_log_path),
            mode="w",  # Overwrite on each startup
            encoding="utf-8",
        )
        session_handler.setLevel(logging.DEBUG)
        session_handler.setFormatter(ConsoleFormatter())
        root.addHandler(session_handler)

    # Quiet down noisy libraries
    logging.getLogger("slack_bolt").setLevel(logging.WARNING)
    logging.getLogger("slack_sdk").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
