"""
tmux Manager for Anchovies.

Manages the tmux session with:
- Left pane: Chat Hub (always visible)
- Right side: Work session tabs (one per active persona)
"""

import logging
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Configuration
SESSION_NAME = os.getenv("TMUX_SESSION_NAME", "anchovies")
ANCHOVIES_DIR = Path(__file__).parent.parent
CHAT_PANE_WIDTH = 35  # Percentage


class TmuxManager:
    """Manages tmux session, windows, and panes for Anchovies."""

    def __init__(self, session_name: str = SESSION_NAME):
        self.session_name = session_name
        self.anchovies_dir = ANCHOVIES_DIR

    def _run_tmux(self, *args, capture_output: bool = False) -> subprocess.CompletedProcess:
        """Run a tmux command."""
        cmd = ["tmux"] + list(args)
        logger.debug(f"Running: {' '.join(cmd)}")
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
        )

    def session_exists(self) -> bool:
        """Check if the tmux session already exists."""
        result = self._run_tmux("has-session", "-t", self.session_name, capture_output=True)
        return result.returncode == 0

    def create_session(self) -> bool:
        """
        Create the Anchovies tmux session with the standard layout.

        Layout:
        ┌─────────────────────┬───────────────────────────────┐
        │  Chat Hub (35%)     │  Work Area (65%)              │
        │  (pane 0)           │  (pane 1 - placeholder)       │
        └─────────────────────┴───────────────────────────────┘

        Returns:
            True if session created, False if it already exists
        """
        if self.session_exists():
            logger.info(f"Session '{self.session_name}' already exists")
            return False

        # Create new detached session with 'chat' window
        self._run_tmux(
            "new-session", "-d",
            "-s", self.session_name,
            "-n", "chat",
            "-c", str(self.anchovies_dir),
        )

        # Split window horizontally: left=chat (35%), right=work area (65%)
        self._run_tmux(
            "split-window", "-h",
            "-t", f"{self.session_name}:chat",
            "-p", str(100 - CHAT_PANE_WIDTH),
            "-c", str(self.anchovies_dir),
        )

        # Set pane titles for clarity
        self._run_tmux(
            "select-pane", "-t", f"{self.session_name}:chat.0",
            "-T", "Chat Hub (Marcus)",
        )
        self._run_tmux(
            "select-pane", "-t", f"{self.session_name}:chat.1",
            "-T", "Work Sessions",
        )

        # Select the chat pane as active
        self._run_tmux("select-pane", "-t", f"{self.session_name}:chat.0")

        logger.info(f"Created tmux session '{self.session_name}'")
        return True

    def kill_session(self) -> bool:
        """Kill the tmux session if it exists."""
        if not self.session_exists():
            logger.info(f"Session '{self.session_name}' does not exist")
            return False

        self._run_tmux("kill-session", "-t", self.session_name)
        logger.info(f"Killed tmux session '{self.session_name}'")
        return True

    def spawn_persona_tab(
        self,
        member: str,
        task_prompt: str,
        working_dir: str | None = None,
    ) -> bool:
        """
        Spawn a new work session tab for a persona.

        Args:
            member: Team member name (lowercase)
            task_prompt: The prepared task prompt for this session
            working_dir: Working directory for the session (default: anchovies_dir parent)

        Returns:
            True if tab created successfully
        """
        if not self.session_exists():
            logger.error(f"Session '{self.session_name}' does not exist")
            return False

        work_dir = working_dir or str(self.anchovies_dir.parent)

        # Check if tab already exists for this member
        if self.persona_tab_exists(member):
            logger.warning(f"Tab for '{member}' already exists")
            return False

        # Write task prompt to temp file
        prompt_file = self._write_prompt_file(member, task_prompt)

        # Create new window (tab) for the persona
        self._run_tmux(
            "new-window",
            "-t", self.session_name,
            "-n", member,
            "-c", work_dir,
        )

        # Start claude interactively, then pipe in the prompt as first message
        # We use a bash script that:
        # 1. Starts claude
        # 2. Waits for it to be ready
        # 3. Sends the prompt content

        # Use a unique buffer name to avoid collisions when multiple personas spawn
        buffer_id = f"prompt_{member}_{uuid.uuid4().hex[:8]}"

        # Load the prompt into a uniquely named buffer first
        self._run_tmux(
            "load-buffer",
            "-b", buffer_id,
            str(prompt_file),
        )

        # Create a small script that waits for claude and pastes
        # This runs in the tmux pane itself
        wait_and_paste_cmd = f'''
claude && exit 1
# If claude starts, this loop waits then pastes
'''

        # Start claude in the new window
        self._run_tmux(
            "send-keys",
            "-t", f"{self.session_name}:{member}",
            "claude",
            "Enter",
        )

        # Wait for claude to start (it needs time to initialize)
        # Claude CLI can take 10-20 seconds to fully start and show the prompt
        time.sleep(18)

        # Paste the buffer content
        self._run_tmux(
            "paste-buffer",
            "-b", buffer_id,
            "-t", f"{self.session_name}:{member}",
        )

        # Small delay then send Enter to submit
        time.sleep(1)
        self._run_tmux(
            "send-keys",
            "-t", f"{self.session_name}:{member}",
            "Enter",
        )

        # Clean up the buffer
        self._run_tmux(
            "delete-buffer",
            "-b", buffer_id,
        )

        logger.info(f"Spawned work tab for '{member}'")
        return True

    def _write_prompt_file(self, member: str, prompt: str) -> Path:
        """Write task prompt to a temp file and return the path."""
        prompt_dir = self.anchovies_dir / "tmp"
        prompt_dir.mkdir(exist_ok=True)

        prompt_file = prompt_dir / f"prompt_{member}.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        return prompt_file

    def persona_tab_exists(self, member: str) -> bool:
        """Check if a tab exists for the given persona."""
        result = self._run_tmux(
            "list-windows",
            "-t", self.session_name,
            "-F", "#{window_name}",
            capture_output=True,
        )
        if result.returncode != 0:
            return False

        windows = result.stdout.strip().split("\n")
        return member in windows

    def close_persona_tab(self, member: str) -> bool:
        """Close a persona's work tab."""
        if not self.persona_tab_exists(member):
            logger.warning(f"Tab for '{member}' does not exist")
            return False

        self._run_tmux("kill-window", "-t", f"{self.session_name}:{member}")
        logger.info(f"Closed work tab for '{member}'")
        return True

    def list_active_tabs(self) -> list[str]:
        """List all active persona tabs (excludes 'chat' window)."""
        result = self._run_tmux(
            "list-windows",
            "-t", self.session_name,
            "-F", "#{window_name}",
            capture_output=True,
        )
        if result.returncode != 0:
            return []

        windows = result.stdout.strip().split("\n")
        # Filter out the chat window
        return [w for w in windows if w and w != "chat"]

    def switch_to_tab(self, tab_name: str) -> bool:
        """Switch to a specific tab (window)."""
        self._run_tmux("select-window", "-t", f"{self.session_name}:{tab_name}")
        return True

    def switch_to_chat(self) -> bool:
        """Switch to the chat hub tab."""
        return self.switch_to_tab("chat")

    def send_to_chat_pane(self, text: str) -> bool:
        """Send text/command to the chat hub pane."""
        self._run_tmux(
            "send-keys",
            "-t", f"{self.session_name}:chat.0",
            text,
            "Enter",
        )
        return True

    def send_to_work_pane(self, member: str, text: str) -> bool:
        """Send text/command to a persona's work pane."""
        if not self.persona_tab_exists(member):
            logger.warning(f"Tab for '{member}' does not exist")
            return False

        self._run_tmux(
            "send-keys",
            "-t", f"{self.session_name}:{member}",
            text,
            "Enter",
        )
        return True

    def get_pane_content(self, pane_target: str, lines: int = 50) -> str:
        """Capture recent content from a pane."""
        result = self._run_tmux(
            "capture-pane",
            "-t", pane_target,
            "-p",
            "-S", f"-{lines}",
            capture_output=True,
        )
        return result.stdout if result.returncode == 0 else ""

    def attach(self) -> None:
        """Attach to the tmux session (blocking)."""
        if not self.session_exists():
            logger.error(f"Session '{self.session_name}' does not exist")
            return

        os.execvp("tmux", ["tmux", "attach", "-t", self.session_name])


# Singleton instance
_manager: TmuxManager | None = None


def get_tmux_manager() -> TmuxManager:
    """Get the singleton TmuxManager instance."""
    global _manager
    if _manager is None:
        _manager = TmuxManager()
    return _manager


if __name__ == "__main__":
    # Quick test
    import sys

    logging.basicConfig(level=logging.DEBUG)
    manager = get_tmux_manager()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "create":
            manager.create_session()
        elif cmd == "kill":
            manager.kill_session()
        elif cmd == "list":
            print("Active tabs:", manager.list_active_tabs())
        elif cmd == "attach":
            manager.attach()
        elif cmd == "spawn" and len(sys.argv) > 2:
            member = sys.argv[2]
            test_prompt = f"You are {member.title()}. This is a test session."
            manager.spawn_persona_tab(member, test_prompt)
        elif cmd == "close" and len(sys.argv) > 2:
            member = sys.argv[2]
            manager.close_persona_tab(member)
        else:
            print("Usage: python tmux_manager.py [create|kill|list|attach|spawn <name>|close <name>]")
    else:
        print("Usage: python tmux_manager.py [create|kill|list|attach|spawn <name>|close <name>]")
