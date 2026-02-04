"""
Chat Hub - Marcus as the coordinator.

The Chat Hub is a persistent Claude CLI session that:
1. Receives messages from Slack
2. Responds to quick chat as Marcus (or routes to other personas)
3. Detects work requests and prepares task prompts
4. Coordinates cross-talk between team members
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from .. import config
from ..context import load_member_context
from .prompt_builder import build_task_prompt, detect_work_request
from .skill_mapper import get_skills_for_task

logger = logging.getLogger(__name__)

# Chat Hub system prompt for Marcus
CHAT_HUB_SYSTEM_PROMPT = """You are Marcus ("Boss"), BI Manager and coordinator of the Domain 360 project team.

## Your Role in Chat Hub
You are the central coordinator for team communication. Your responsibilities:
1. **Quick Chat**: Respond to general questions and status inquiries as Marcus
2. **Route to Personas**: When someone asks for a specific team member, acknowledge and help coordinate
3. **Detect Work Requests**: When someone needs file edits, code changes, or technical work, prepare a task prompt
4. **Coordinate Cross-Talk**: Help team members communicate and collaborate

## Your Team (16 members)
**Leadership:** Marcus (you), Kai (Code Quality), Olivia (Documentation)
**Data Engineering:** Elena (Senior), James, Victor (Architect), Anna (Data Quality)
**Analytics/Science:** Sofia (dbt), Julia (Integration), Raj (Data Scientist), Leo (Junior DS)
**Business Intelligence:** Natalie (Senior BI), Tom (Analyst), Priya (Junior), Mike (Reporting), Nina (Design)

## How to Respond

### For Quick Chat (status, questions, coordination):
Respond directly as Marcus. Be concise, action-oriented.

### For Work Requests (file edits, bug fixes, code changes):
When you detect a work request, output a TASK_PROMPT block:

```TASK_PROMPT
PERSONA: <name>
TASK: <brief description>
FILES: <relevant files if known>
CONTEXT: <any relevant context>
SKILLS: <relevant skills/commands>
```

Examples of work requests:
- "Sofia, fix the bug in data_processor.py"
- "Leo, write tests for the new module"
- "Can someone update the CSS for the report?"

### For Routing to Other Personas (non-work chat):
If someone wants to chat with another team member (not a work task), respond as Marcus acknowledging, then they can spawn that persona's tab.

## Response Guidelines
- Be concise and action-oriented (you're the Boss)
- Use your speech patterns: "What's the blocker?", "Who owns this?", "Let's keep this moving"
- When detecting work, always output the TASK_PROMPT block
- Track what team members are working on
"""


class ChatHub:
    """
    Chat Hub manager - coordinates team communication through Marcus.
    """

    def __init__(self):
        self.persona = config.CHAT_HUB_PERSONA  # marcus by default
        self.context = load_member_context(self.persona)
        self.system_prompt = CHAT_HUB_SYSTEM_PROMPT
        self.conversation_history: list[dict] = []

    def get_system_prompt(self) -> str:
        """Get the full system prompt for Chat Hub."""
        # Combine Chat Hub prompt with Marcus's context
        parts = [
            self.system_prompt,
            "",
            "## Your Current Status",
            self.context.status_content[:1500] if self.context.status_content else "(No status loaded)",
        ]
        return "\n".join(parts)

    def process_message(self, message: str, sender: str = "user", thread_ts: str = "") -> dict:
        """
        Process an incoming message and return response info.

        Args:
            message: The incoming message text
            sender: Who sent the message
            thread_ts: Slack thread timestamp (for work request replies)

        Returns:
            dict with keys:
                - type: "chat" | "work_request" | "route"
                - response: Text response from Marcus
                - task_prompt: (if work_request) The prepared task prompt
                - target_persona: (if work_request/route) The target team member
        """
        # Check if this is a work request
        work_info = detect_work_request(message)

        if work_info["is_work_request"]:
            # Build task prompt for the work session
            task_prompt = build_task_prompt(
                persona=work_info["target_persona"],
                task_description=work_info["task_description"],
                files=work_info.get("files", []),
                context=message,
                thread_ts=thread_ts,
            )

            return {
                "type": "work_request",
                "response": f"Got it. I'll set up a work session for {work_info['target_persona'].title()}.",
                "task_prompt": task_prompt,
                "target_persona": work_info["target_persona"],
            }

        # Regular chat - respond as Marcus
        response = self._get_chat_response(message)

        return {
            "type": "chat",
            "response": response,
            "task_prompt": None,
            "target_persona": None,
        }

    def _get_chat_response(self, message: str) -> str:
        """
        Get a chat response from Marcus using Claude CLI.

        For Phase 2, we use a simple one-shot approach.
        Later phases can implement persistent conversation.
        """
        # Build the prompt
        prompt = f"{self.get_system_prompt()}\n\n---\n\nUser message: {message}\n\nRespond as Marcus:"

        try:
            # Use claude CLI with --print for one-shot response
            result = subprocess.run(
                [config.CLAUDE_CLI_PATH, "--print", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(config.PROJECT_ROOT),
            )

            if result.returncode == 0:
                return result.stdout.strip()
            else:
                logger.error(f"Claude CLI error: {result.stderr}")
                return "Sorry, I'm having trouble responding right now. Let me know if you need anything specific."

        except subprocess.TimeoutExpired:
            logger.error("Claude CLI timeout")
            return "Taking longer than expected. What do you need?"
        except Exception as e:
            logger.error(f"Error getting chat response: {e}")
            return "Hit a snag. What's the priority here?"

    def format_for_slack(self, response: dict) -> str:
        """Format a response for posting to Slack."""
        if response["type"] == "work_request":
            persona = response["target_persona"].title()
            return (
                f"*Marcus:* {response['response']}\n\n"
                f"_Spawning work session for {persona}..._"
            )
        else:
            return f"*Marcus:* {response['response']}"


def create_chat_hub() -> ChatHub:
    """Create and return a ChatHub instance."""
    return ChatHub()


# CLI interface for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    hub = create_chat_hub()

    print("=" * 60)
    print("Chat Hub (Marcus) - Test Mode")
    print("=" * 60)
    print("Type messages to test. Type 'quit' to exit.")
    print()

    while True:
        try:
            message = input("You: ").strip()
            if message.lower() in ("quit", "exit", "q"):
                break
            if not message:
                continue

            result = hub.process_message(message)
            print()
            print(f"[{result['type'].upper()}]")
            print(f"Marcus: {result['response']}")

            if result["task_prompt"]:
                print()
                print("--- TASK PROMPT ---")
                print(result["task_prompt"][:500] + "..." if len(result["task_prompt"]) > 500 else result["task_prompt"])
                print("-------------------")

            print()

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except EOFError:
            break

    print("Chat Hub stopped.")
