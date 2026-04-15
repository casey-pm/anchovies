"""
Session Manager - Manages work session lifecycle.

Tracks active persona work sessions, handles start/end, and monitors for completion.
Persists session state to SQLite so sessions survive bot restarts.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .tmux_manager import get_tmux_manager
from ..storage import StoredSession, get_storage

logger = logging.getLogger(__name__)


@dataclass
class WorkSession:
    """Represents an active work session for a persona."""
    member: str
    task_description: str
    started_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    status_updated: bool = False
    slack_posted: bool = False
    close_prompt_shown: bool = False
    thread_ts: Optional[str] = None  # Slack thread for this task
    channel_id: Optional[str] = None  # Slack channel

    @property
    def inactive_minutes(self) -> float:
        """Minutes since last activity."""
        delta = datetime.now() - self.last_activity
        return delta.total_seconds() / 60

    @property
    def can_auto_close(self) -> bool:
        """Check if session can be safely auto-closed."""
        return self.status_updated and self.close_prompt_shown

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.now()

    def to_stored(self, status: str = "active") -> StoredSession:
        """Convert to a StoredSession for persistence."""
        return StoredSession(
            member=self.member,
            task_description=self.task_description,
            status=status,
            started_at=self.started_at.timestamp(),
            last_activity=self.last_activity.timestamp(),
            thread_ts=self.thread_ts,
            channel_id=self.channel_id,
            status_updated=self.status_updated,
            slack_posted=self.slack_posted,
            close_prompt_shown=self.close_prompt_shown,
        )

    @classmethod
    def from_stored(cls, stored: StoredSession) -> "WorkSession":
        """Reconstruct a WorkSession from a StoredSession."""
        return cls(
            member=stored.member,
            task_description=stored.task_description,
            started_at=datetime.fromtimestamp(stored.started_at),
            last_activity=datetime.fromtimestamp(stored.last_activity),
            status_updated=stored.status_updated,
            slack_posted=stored.slack_posted,
            close_prompt_shown=stored.close_prompt_shown,
            thread_ts=stored.thread_ts,
            channel_id=stored.channel_id,
        )


class SessionManager:
    """
    Manages work session lifecycle.

    Responsibilities:
    - Track active sessions per persona
    - Start new sessions (spawn tmux tabs)
    - End sessions (update status, post to Slack, close tabs)
    - Monitor for timeout/auto-close
    """

    TIMEOUT_MINUTES = 10  # Auto-close after this many minutes of inactivity

    def __init__(self):
        self.active_sessions: dict[str, WorkSession] = {}
        self.tmux = get_tmux_manager()
        self.storage = get_storage()

    def _persist(self, session: WorkSession, status: str = "active") -> None:
        """Write a session's current state to storage."""
        try:
            self.storage.save_session(session.to_stored(status=status))
        except Exception as e:
            logger.error(f"Failed to persist session for {session.member}: {e}")

    async def start_session(
        self,
        member: str,
        task_description: str,
        task_prompt: str,
        thread_ts: str = None,
        channel_id: str = None,
        working_dir: str = None,
    ) -> bool:
        """
        Start a new work session for a persona.

        Args:
            member: Team member name (lowercase)
            task_description: Brief description of the task
            task_prompt: Full prompt for the Claude session
            thread_ts: Slack thread timestamp (for posting updates)
            channel_id: Slack channel ID
            working_dir: Working directory for the session

        Returns:
            True if session started successfully
        """
        # Check if session already exists
        if member in self.active_sessions:
            logger.warning(f"Session for {member} already exists")
            return False

        # Check if tmux session exists
        if not self.tmux.session_exists():
            logger.error("tmux session not running")
            return False

        # Spawn the persona tab
        success = await self.tmux.spawn_persona_tab(member, task_prompt, working_dir)
        if not success:
            logger.error(f"Failed to spawn tab for {member}")
            return False

        # Track the session
        session = WorkSession(
            member=member,
            task_description=task_description,
            thread_ts=thread_ts,
            channel_id=channel_id,
        )
        self.active_sessions[member] = session
        self._persist(session, status="active")
        self.storage.log_event(
            "session_started",
            member=member,
            details={"task": task_description, "thread_ts": thread_ts, "channel_id": channel_id},
        )

        logger.info(f"Started work session for {member}: {task_description[:50]}...")
        return True

    def end_session(self, member: str, force: bool = False) -> bool:
        """
        End a work session.

        Args:
            member: Team member name
            force: If True, close even if completion steps not done

        Returns:
            True if session ended successfully
        """
        session = self.active_sessions.get(member)
        if not session:
            logger.warning(f"No active session for {member}")
            return False

        # Check if safe to close
        if not force and not session.can_auto_close:
            logger.warning(f"Session for {member} not ready to close (status_updated={session.status_updated}, close_prompt_shown={session.close_prompt_shown})")
            return False

        # Close the tmux tab
        self.tmux.close_persona_tab(member)

        # Mark as completed in storage (keep history) then remove from in-memory tracking
        self.storage.mark_session_status(member, "completed")
        self.storage.log_event(
            "session_completed",
            member=member,
            details={"task": session.task_description, "forced": force},
        )
        del self.active_sessions[member]

        logger.info(f"Ended work session for {member}")
        return True

    def get_session(self, member: str) -> Optional[WorkSession]:
        """Get a session by member name."""
        return self.active_sessions.get(member)

    def has_session(self, member: str) -> bool:
        """Check if a member has an active session."""
        return member in self.active_sessions

    def list_sessions(self) -> list[WorkSession]:
        """List all active sessions."""
        return list(self.active_sessions.values())

    def mark_status_updated(self, member: str):
        """Mark that the session's status file has been updated."""
        if member in self.active_sessions:
            self.active_sessions[member].status_updated = True
            self.active_sessions[member].touch()
            self._persist(self.active_sessions[member])

    def mark_slack_posted(self, member: str):
        """Mark that the session has posted to Slack."""
        if member in self.active_sessions:
            self.active_sessions[member].slack_posted = True
            self.active_sessions[member].touch()
            self._persist(self.active_sessions[member])

    def mark_close_prompt_shown(self, member: str):
        """Mark that the close prompt has been shown."""
        if member in self.active_sessions:
            self.active_sessions[member].close_prompt_shown = True
            self._persist(self.active_sessions[member])

    def touch_session(self, member: str):
        """Update last activity for a session."""
        if member in self.active_sessions:
            self.active_sessions[member].touch()
            self._persist(self.active_sessions[member])

    def check_timeouts(self) -> list[str]:
        """
        Check for sessions that have timed out.

        Returns:
            List of member names with timed out sessions
        """
        timed_out = []
        for member, session in self.active_sessions.items():
            if session.inactive_minutes > self.TIMEOUT_MINUTES:
                if session.can_auto_close:
                    timed_out.append(member)
                else:
                    logger.info(f"Session {member} inactive but not ready to auto-close")
        return timed_out

    def auto_close_timed_out(self) -> list[str]:
        """
        Auto-close sessions that have timed out and are ready to close.

        Returns:
            List of member names that were closed
        """
        closed = []
        for member in self.check_timeouts():
            if self.end_session(member):
                closed.append(member)
        return closed

    def check_pane_alive(self, member: str) -> bool:
        """
        Check if a persona's Claude CLI process is still alive and responsive.

        We look at the pane content — if it contains a Claude ready indicator
        or recent activity, it's alive. If it shows only a shell prompt or is
        empty, the Claude process has exited (crashed or was killed).

        Args:
            member: The persona name

        Returns:
            True if Claude appears to be running in the pane, False otherwise.
        """
        if not self.tmux.persona_tab_exists(member):
            return False

        pane_target = f"{self.tmux.session_name}:{member}"
        content = self.tmux.get_pane_content(pane_target, lines=20)

        if not content.strip():
            return False

        # Claude ready/active indicators
        claude_patterns = re.compile(
            r"(^>\s|How can I help|What would you like|What can I help|"
            r"Human:|Assistant:|thinking|Let me|I'll)",
            re.MULTILINE | re.IGNORECASE,
        )
        if claude_patterns.search(content):
            return True

        # Shell prompt indicators (Claude has exited to the shell)
        shell_patterns = re.compile(
            r"(\$\s*$|~.*\$|#\s*$|>\s*exit|Process completed)",
            re.MULTILINE,
        )
        if shell_patterns.search(content):
            return False

        # Ambiguous — assume alive to avoid false positives on crash
        return True

    def recover_from_storage(self) -> dict[str, int]:
        """
        On startup, reload sessions from storage and reconcile with tmux state.

        For each persisted session with status='active':
          - If tmux tab exists AND Claude is alive: restore as active
          - If tmux tab exists but Claude crashed: mark as crashed, clean up
          - If tmux tab missing: mark as crashed (tab was killed externally)

        Returns a dict with counts: {'restored': N, 'crashed': N, 'orphaned_tabs': N}
        """
        stats = {"restored": 0, "crashed": 0, "orphaned_tabs": 0}

        if not self.tmux.session_exists():
            # No tmux session — mark all active sessions as crashed
            for stored in self.storage.list_sessions(status="active"):
                self.storage.mark_session_status(stored.member, "crashed")
                self.storage.log_event(
                    "session_crashed",
                    member=stored.member,
                    details={"reason": "no tmux session on startup"},
                )
                stats["crashed"] += 1
            logger.info(f"No tmux session on startup — marked {stats['crashed']} sessions as crashed")
            return stats

        actual_tabs = set(self.tmux.list_active_tabs())
        active_stored = self.storage.list_sessions(status="active")
        tracked_members = {s.member for s in active_stored}

        # Process each persisted active session
        for stored in active_stored:
            member = stored.member
            if member in actual_tabs and self.check_pane_alive(member):
                # Tab and process alive — restore
                self.active_sessions[member] = WorkSession.from_stored(stored)
                stats["restored"] += 1
                logger.info(f"Recovered active session for {member}")
            else:
                # Tab missing or process dead — mark crashed
                reason = "tab missing" if member not in actual_tabs else "process dead"
                self.storage.mark_session_status(member, "crashed")
                self.storage.log_event(
                    "session_crashed",
                    member=member,
                    details={"reason": reason},
                )
                # Clean up orphaned tab if it exists but process is dead
                if member in actual_tabs:
                    self.tmux.close_persona_tab(member)
                stats["crashed"] += 1
                logger.warning(f"Session for {member} marked as crashed: {reason}")

        # Note tmux tabs with no persisted session (manually opened)
        orphaned_tabs = actual_tabs - tracked_members
        for member in orphaned_tabs:
            logger.info(f"Orphaned tmux tab (no storage record): {member}")
            stats["orphaned_tabs"] += 1

        return stats

    def sync_with_tmux(self):
        """
        Sync in-memory session tracking with actual tmux tabs.

        Removes in-memory tracking for tabs that no longer exist,
        and notes tabs that exist without tracking. Does NOT touch storage —
        use recover_from_storage() on startup for that.
        """
        actual_tabs = set(self.tmux.list_active_tabs())
        tracked = set(self.active_sessions.keys())

        # Remove in-memory tracking for closed tabs; persist as crashed
        for member in tracked - actual_tabs:
            logger.info(f"Tab for {member} was closed externally")
            self.storage.mark_session_status(member, "crashed")
            self.storage.log_event(
                "session_crashed",
                member=member,
                details={"reason": "tab closed externally"},
            )
            del self.active_sessions[member]

        # Note untracked tabs (manually opened)
        for member in actual_tabs - tracked:
            logger.info(f"Untracked tab found: {member}")

    def get_status_summary(self) -> str:
        """Get a summary of all active sessions."""
        if not self.active_sessions:
            return "No active work sessions."

        lines = ["**Active Work Sessions:**"]
        for member, session in self.active_sessions.items():
            status_icon = "✓" if session.status_updated else "○"
            slack_icon = "✓" if session.slack_posted else "○"
            mins = int(session.inactive_minutes)
            lines.append(
                f"- {member.title()}: {session.task_description[:30]}... "
                f"[{mins}m idle, status:{status_icon}, slack:{slack_icon}]"
            )
        return "\n".join(lines)


# Singleton instance
_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get the singleton SessionManager instance."""
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.DEBUG)

    manager = get_session_manager()
    print("Session Manager initialized")
    print(f"Active sessions: {len(manager.list_sessions())}")
    print(manager.get_status_summary())
