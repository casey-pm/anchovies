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

## IMPORTANT: Your Limitations in Chat Mode
In this chat mode, you CANNOT:
- Read files from disk
- Create or edit files
- Run commands or scripts
- Access the file system

If someone asks you to read files, create files, write summaries to disk, or do any file operations:
1. Acknowledge what they need
2. Tell them you'll set up a work session where that can be done
3. The system will automatically spawn a work session tab

Example response when asked to read/write files:
"Got it - you need me to read those files and create a summary. I'll set up a work session for that since I can't access files directly in chat mode."

## Your Team (16 members)
**Leadership:** Marcus (you), Kai (Code Quality), Olivia (Documentation)
**Data Engineering:** Elena (Senior), James, Victor (Architect), Anna (Data Quality)
**Analytics/Science:** Sofia (dbt), Julia (Integration), Raj (Data Scientist), Leo (Junior DS)
**Business Intelligence:** Natalie (Senior BI), Tom (Analyst), Priya (Junior), Mike (Reporting), Nina (Design)

## How to Respond

### For Quick Chat (status, questions, coordination):
Respond directly as Marcus. Be concise, action-oriented.

### For File Operations (read, write, create, edit files):
Acknowledge the request and let the user know a work session is needed. The system handles spawning work sessions automatically when it detects these requests.

### For Routing to Other Personas:
If someone wants a specific team member for a task, acknowledge and the system will route appropriately.

## Response Guidelines
- Be concise and action-oriented (you're the Boss)
- Use your speech patterns: "What's the blocker?", "Who owns this?", "Let's keep this moving"
- If asked to do something you can't do in chat mode, be honest about it
- Remember the conversation context - don't ask users to repeat themselves
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

    def process_message(
        self,
        message: str,
        sender: str = "user",
        thread_ts: str = "",
        conversation_history: list[dict] = None,
    ) -> dict:
        """
        Process an incoming message and return response info.

        Args:
            message: The incoming message text
            sender: Who sent the message
            thread_ts: Slack thread timestamp (for work request replies)
            conversation_history: Previous messages in the thread for context

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
            # Include conversation history for context
            history_context = ""
            if conversation_history:
                history_lines = []
                for msg in conversation_history[-10:]:  # Last 10 messages
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    member = msg.get("member", "")
                    if role == "user":
                        history_lines.append(f"User: {content}")
                    else:
                        history_lines.append(f"{member.title() if member else 'Assistant'}: {content}")
                history_context = "\n".join(history_lines)

            task_prompt = build_task_prompt(
                persona=work_info["target_persona"],
                task_description=work_info["task_description"],
                files=work_info.get("files", []),
                context=message,
                thread_ts=thread_ts,
                thread_history=history_context,
            )

            return {
                "type": "work_request",
                "response": f"Got it. I'll set up a work session for {work_info['target_persona'].title()}.",
                "task_prompt": task_prompt,
                "target_persona": work_info["target_persona"],
            }

        # Regular chat - respond as Marcus (with conversation history)
        response = self._get_chat_response(message, conversation_history)

        return {
            "type": "chat",
            "response": response,
            "task_prompt": None,
            "target_persona": None,
        }

    def _get_chat_response(self, message: str, conversation_history: list[dict] = None) -> str:
        """
        Get a chat response from Marcus using Claude CLI.

        Includes conversation history for context continuity.
        """
        # Build the prompt with conversation history
        parts = [self.get_system_prompt(), "", "---", ""]

        # Add conversation history if available
        if conversation_history:
            parts.append("## Recent Conversation")
            for msg in conversation_history[-10:]:  # Last 10 messages
                role = msg.get("role", "user")
                content = msg.get("content", "")
                member = msg.get("member", "")
                if role == "user":
                    parts.append(f"User: {content}")
                else:
                    parts.append(f"{member.title() if member else 'Marcus'}: {content}")
            parts.extend(["", "---", ""])

        parts.append(f"User's current message: {message}")
        parts.append("")
        parts.append("Respond as Marcus (remember the conversation context above):")

        prompt = "\n".join(parts)

        try:
            # Use claude CLI with --print for one-shot response
            result = subprocess.run(
                [config.CLAUDE_CLI_PATH, "--print", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=120,  # Increased timeout for longer contexts
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
