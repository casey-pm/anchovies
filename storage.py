"""
SQLite storage layer for Anchovies.

Persists state across bot restarts: conversation history, session tracking,
audit log, and daily budget. Uses SQLite with WAL mode for reliability and
concurrent read access.

Tables:
    conversations — thread history keyed by Slack thread_ts
    sessions     — work session tracking (for crash recovery)
    audit_log    — event log for observability
    budget       — daily API cost tracking

All public methods are thread-safe. SQLite handles serialization via its
internal locking; we use a single connection with check_same_thread=False
and a module-level lock to serialize writes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StoredSession:
    """Represents a persisted work session."""
    member: str
    task_description: str
    status: str  # "active" | "completed" | "crashed" | "timeout"
    started_at: float  # Unix timestamp
    last_activity: float
    thread_ts: Optional[str] = None
    channel_id: Optional[str] = None
    status_updated: bool = False
    slack_posted: bool = False
    close_prompt_shown: bool = False
    project: Optional[str] = None  # Project slug (None = legacy/no project)


@dataclass
class AuditEntry:
    """Represents an audit log entry."""
    id: int
    timestamp: float
    event_type: str
    member: Optional[str]
    details: dict


# ---------------------------------------------------------------------------
# Storage class
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    thread_ts TEXT PRIMARY KEY,
    messages TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_accessed REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_last_accessed
    ON conversations (last_accessed);

CREATE TABLE IF NOT EXISTS sessions (
    member TEXT PRIMARY KEY,
    task_description TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    last_activity REAL NOT NULL,
    thread_ts TEXT,
    channel_id TEXT,
    status_updated INTEGER DEFAULT 0,
    slack_posted INTEGER DEFAULT 0,
    close_prompt_shown INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions (status);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    member TEXT,
    details TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_member ON audit_log (member);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log (event_type);

CREATE TABLE IF NOT EXISTS budget (
    date TEXT PRIMARY KEY,
    total_cost REAL NOT NULL DEFAULT 0.0,
    call_count INTEGER NOT NULL DEFAULT 0
);
"""


class Storage:
    """SQLite-backed storage for Anchovies state."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Create tables and indexes if they don't exist, enable WAL."""
        with self._lock:
            # WAL mode for better concurrent read performance
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._migrate_schema()

    def _migrate_schema(self):
        """Apply any schema migrations for columns added after initial release."""
        with self._lock:
            # Check if 'project' column exists on sessions table
            columns = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "project" not in columns:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN project TEXT DEFAULT NULL"
                )
                self._conn.commit()
                logger.info("Migrated sessions table: added 'project' column")

    def close(self):
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    # -----------------------------------------------------------------------
    # Conversations
    # -----------------------------------------------------------------------

    def save_conversation(self, thread_ts: str, messages: list[dict]) -> None:
        """Persist a conversation's full message list."""
        now = time.time()
        serialized = json.dumps(messages)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO conversations (thread_ts, messages, created_at, last_accessed)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(thread_ts) DO UPDATE SET
                    messages = excluded.messages,
                    last_accessed = excluded.last_accessed
                """,
                (thread_ts, serialized, now, now),
            )
            self._conn.commit()

    def load_conversation(self, thread_ts: str) -> list[dict]:
        """Load a conversation's messages. Returns empty list if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT messages FROM conversations WHERE thread_ts = ?",
                (thread_ts,),
            ).fetchone()
        if not row:
            return []
        try:
            return json.loads(row["messages"])
        except json.JSONDecodeError:
            logger.error(f"Corrupted conversation data for {thread_ts}")
            return []

    def delete_conversation(self, thread_ts: str) -> None:
        """Remove a conversation."""
        with self._lock:
            self._conn.execute("DELETE FROM conversations WHERE thread_ts = ?", (thread_ts,))
            self._conn.commit()

    def list_conversations(self, limit: Optional[int] = None) -> list[tuple[str, float]]:
        """
        List all conversation thread_ts values and their last_accessed times,
        ordered by most recently accessed first.
        """
        query = "SELECT thread_ts, last_accessed FROM conversations ORDER BY last_accessed DESC"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(query).fetchall()
        return [(r["thread_ts"], r["last_accessed"]) for r in rows]

    def cleanup_old_conversations(self, ttl_seconds: float) -> int:
        """
        Delete conversations not accessed within ttl_seconds.
        Returns the number of rows deleted.
        """
        cutoff = time.time() - ttl_seconds
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM conversations WHERE last_accessed < ?",
                (cutoff,),
            )
            self._conn.commit()
            return cursor.rowcount

    # -----------------------------------------------------------------------
    # Sessions
    # -----------------------------------------------------------------------

    def save_session(self, session: StoredSession) -> None:
        """Persist or update a session."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (
                    member, task_description, status,
                    started_at, last_activity,
                    thread_ts, channel_id,
                    status_updated, slack_posted, close_prompt_shown,
                    project
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(member) DO UPDATE SET
                    task_description = excluded.task_description,
                    status = excluded.status,
                    last_activity = excluded.last_activity,
                    thread_ts = excluded.thread_ts,
                    channel_id = excluded.channel_id,
                    status_updated = excluded.status_updated,
                    slack_posted = excluded.slack_posted,
                    close_prompt_shown = excluded.close_prompt_shown,
                    project = excluded.project
                """,
                (
                    session.member, session.task_description, session.status,
                    session.started_at, session.last_activity,
                    session.thread_ts, session.channel_id,
                    int(session.status_updated), int(session.slack_posted),
                    int(session.close_prompt_shown),
                    session.project,
                ),
            )
            self._conn.commit()

    def load_session(self, member: str) -> Optional[StoredSession]:
        """Load a session by member name."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE member = ?",
                (member,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, status: Optional[str] = None) -> list[StoredSession]:
        """List all sessions, optionally filtered by status."""
        if status:
            query = "SELECT * FROM sessions WHERE status = ? ORDER BY started_at"
            params = (status,)
        else:
            query = "SELECT * FROM sessions ORDER BY started_at"
            params = ()
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_session(r) for r in rows]

    def delete_session(self, member: str) -> None:
        """Remove a session."""
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE member = ?", (member,))
            self._conn.commit()

    def mark_session_status(self, member: str, status: str) -> None:
        """Update a session's status (active/completed/crashed/timeout)."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET status = ?, last_activity = ? WHERE member = ?",
                (status, time.time(), member),
            )
            self._conn.commit()

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> StoredSession:
        # project column may not exist on old databases (pre-migration)
        try:
            project = row["project"]
        except (IndexError, KeyError):
            project = None
        return StoredSession(
            member=row["member"],
            task_description=row["task_description"],
            status=row["status"],
            started_at=row["started_at"],
            last_activity=row["last_activity"],
            thread_ts=row["thread_ts"],
            channel_id=row["channel_id"],
            status_updated=bool(row["status_updated"]),
            slack_posted=bool(row["slack_posted"]),
            close_prompt_shown=bool(row["close_prompt_shown"]),
            project=project,
        )

    # -----------------------------------------------------------------------
    # Audit log
    # -----------------------------------------------------------------------

    def log_event(
        self,
        event_type: str,
        member: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> int:
        """
        Append an event to the audit log. Returns the new row id.

        Common event_types: task_assigned, session_started, session_completed,
        session_crashed, file_changed, commit_created, slack_posted, error.
        """
        details_json = json.dumps(details or {})
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO audit_log (timestamp, event_type, member, details) VALUES (?, ?, ?, ?)",
                (time.time(), event_type, member, details_json),
            )
            self._conn.commit()
            return cursor.lastrowid

    def query_audit(
        self,
        since: Optional[float] = None,
        member: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """
        Query audit log entries, most recent first.

        Args:
            since: Only entries with timestamp >= this (Unix timestamp)
            member: Only entries for this persona
            event_type: Only entries of this type
            limit: Max rows to return

        Returns:
            List of AuditEntry, newest first.
        """
        conditions = []
        params: list = []
        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since)
        if member is not None:
            conditions.append("member = ?")
            params.append(member)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(int(limit))

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        return [
            AuditEntry(
                id=r["id"],
                timestamp=r["timestamp"],
                event_type=r["event_type"],
                member=r["member"],
                details=json.loads(r["details"]) if r["details"] else {},
            )
            for r in rows
        ]

    # -----------------------------------------------------------------------
    # Budget
    # -----------------------------------------------------------------------

    def _today_key(self) -> str:
        """Return today's date as an ISO date string."""
        return date.today().isoformat()

    def add_cost(self, cost: float, call_count: int = 1, day: Optional[str] = None) -> None:
        """Add to today's budget (or a specified day's)."""
        day = day or self._today_key()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO budget (date, total_cost, call_count) VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_cost = total_cost + excluded.total_cost,
                    call_count = call_count + excluded.call_count
                """,
                (day, cost, call_count),
            )
            self._conn.commit()

    def get_budget(self, day: Optional[str] = None) -> tuple[float, int]:
        """
        Get (total_cost, call_count) for a given day (defaults to today).
        Returns (0.0, 0) if no record exists.
        """
        day = day or self._today_key()
        with self._lock:
            row = self._conn.execute(
                "SELECT total_cost, call_count FROM budget WHERE date = ?",
                (day,),
            ).fetchone()
        if not row:
            return (0.0, 0)
        return (row["total_cost"], row["call_count"])

    def budget_exceeded(self, daily_cap: float, day: Optional[str] = None) -> bool:
        """Check if today's budget exceeds the cap."""
        total, _ = self.get_budget(day)
        return total >= daily_cap


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_storage: Optional[Storage] = None
_storage_lock = threading.Lock()


def get_storage(db_path: Optional[Path | str] = None) -> Storage:
    """Get the singleton Storage instance."""
    global _storage
    with _storage_lock:
        if _storage is None:
            if db_path is None:
                # Default: anchovies/data/anchovies.db
                db_path = Path(__file__).parent / "data" / "anchovies.db"
            _storage = Storage(db_path)
    return _storage


def reset_storage():
    """Reset the singleton (useful for tests)."""
    global _storage
    with _storage_lock:
        if _storage is not None:
            _storage.close()
        _storage = None
