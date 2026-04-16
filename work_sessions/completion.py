"""
Completion Handler - Manages task completion sequence.

When a work session is complete:
1. Update status file
2. Post summary to Slack
3. Prompt to close session
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .session_manager import get_session_manager
from .tmux_manager import get_tmux_manager
from .. import config

logger = logging.getLogger(__name__)

# Status files are in CONTEXT_BASE/status/
STATUS_DIR = config.CONTEXT_BASE / "status"


def update_status_file(member: str, summary: str, task: str = "", project: str | None = None) -> bool:
    """
    Update a member's status file with completion info.

    Args:
        member: Team member name (lowercase)
        summary: Summary of what was accomplished
        task: Original task description
        project: Optional project slug — writes to project's status dir instead of global

    Returns:
        True if status file updated successfully
    """
    # Resolve status directory: project-specific or global
    status_dir = STATUS_DIR
    if project:
        try:
            from ..project_registry import get_project_registry
            proj = get_project_registry().get(project)
            if proj:
                status_dir = proj.context_base / "status"
        except Exception:
            pass  # Fall back to global STATUS_DIR

    status_file = status_dir / f"status_{member}.md"

    # Read existing content
    existing = ""
    if status_file.exists():
        existing = status_file.read_text()

    # Build new entry
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_entry = f"""
## Latest Update: {timestamp}

**Task:** {task or 'Work session'}

**Summary:** {summary}

---

"""

    # Prepend new entry (most recent first)
    # Find where old content starts (after header)
    lines = existing.split("\n")
    header_end = 0
    for i, line in enumerate(lines):
        if line.startswith("## ") or line.startswith("---"):
            header_end = i
            break

    # Keep header, insert new entry, then old entries
    header = "\n".join(lines[:header_end]) if header_end > 0 else f"# Status: {member.title()}\n"
    old_entries = "\n".join(lines[header_end:]) if header_end > 0 else ""

    new_content = header.strip() + "\n\n" + new_entry + old_entries

    try:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(new_content)
        logger.info(f"Updated status file for {member}")

        # Mark session as status updated
        session_mgr = get_session_manager()
        session_mgr.mark_status_updated(member)

        return True
    except Exception as e:
        logger.error(f"Failed to update status file for {member}: {e}")
        return False


def format_slack_message(member: str, summary: str) -> str:
    """Format a completion message for Slack."""
    return f"*{member.title()}:* {summary}"


def complete_task(
    member: str,
    summary: str,
    task: str = "",
    auto_close: bool = False,
) -> dict:
    """
    Run the completion sequence for a work session.

    Args:
        member: Team member name
        summary: Summary of what was accomplished
        task: Original task description
        auto_close: If True, close without prompting

    Returns:
        dict with completion status info
    """
    result = {
        "member": member,
        "status_updated": False,
        "slack_message": None,
        "closed": False,
    }

    # Step 1: Update status file
    if update_status_file(member, summary, task):
        result["status_updated"] = True

    # Step 2: Prepare Slack message
    result["slack_message"] = format_slack_message(member, summary)

    # Step 3: Mark close prompt shown
    session_mgr = get_session_manager()
    session_mgr.mark_close_prompt_shown(member)

    # Step 4: Close if auto_close
    if auto_close:
        session = session_mgr.get_session(member)
        if session and session.can_auto_close:
            session_mgr.end_session(member)
            result["closed"] = True

    return result


def get_completion_instructions(member: str) -> str:
    """
    Get completion instructions to display in a work session.

    Returns instructions for the persona to follow when done.
    """
    return f"""
## When You're Done

1. **Update your status file:**
   ```
   Edit: ~/paradise_brain/anchovies/status/status_{member}.md
   ```

2. **Post summary to Slack:**
   ```bash
   ~/paradise_brain/anchovies/scripts/post_to_slack.sh "Brief summary of what you did"
   ```

3. **Close this session:**
   Type `/exit` or tell the user you're done so they can close the tab.

---
"""


if __name__ == "__main__":
    # Test
    import sys

    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) > 2:
        member = sys.argv[1]
        summary = sys.argv[2]
        result = complete_task(member, summary, auto_close=False)
        print(f"Completion result: {result}")
    else:
        print("Usage: python -m work_sessions.completion <member> <summary>")
