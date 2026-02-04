"""Work Sessions module - manages persona work tabs and session lifecycle."""

from .tmux_manager import TmuxManager, get_tmux_manager
from .session_manager import SessionManager, WorkSession, get_session_manager
from .completion import complete_task, update_status_file, get_completion_instructions

__all__ = [
    "TmuxManager",
    "get_tmux_manager",
    "SessionManager",
    "WorkSession",
    "get_session_manager",
    "complete_task",
    "update_status_file",
    "get_completion_instructions",
]
