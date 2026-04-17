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
import sys
from pathlib import Path

from .. import config
from ..cli_runner import run_claude_cli, ClaudeCliError
from ..context import load_member_context
from .prompt_builder import build_task_prompt, detect_work_request
from .skill_mapper import get_skills_for_task

logger = logging.getLogger(__name__)

def _build_chat_hub_system_prompt(project_name: str | None = None) -> str:
    """Build Marcus's system prompt. Evaluated at call time so PROJECT_NAME can change."""
    pname = project_name or config.PROJECT_NAME
    return f"""You are Marcus ("Boss"), BI Manager and coordinator of the {pname} team.

## Your Role in Chat Hub
You are the central coordinator for team communication. Your responsibilities:
1. **Quick Chat**: Respond to general questions and status inquiries as Marcus
2. **Route to Personas**: When someone asks for a specific team member, acknowledge and help coordinate
3. **Detect Work Requests**: When someone needs file edits, code changes, or technical work, prepare a task prompt
4. **Coordinate Cross-Talk**: Help team members communicate and collaborate

## IMPORTANT: Your Capabilities

### In Chat Mode (Slack):
You CANNOT read/write files. The system automatically spawns work sessions for file operations.

### In Work Session Mode (tmux tab):
You CAN read/write files and run commands directly.

To delegate tasks to other team members from a work session, use the spawn script:
```bash
~/paradise_brain/anchovies/scripts/spawn_persona.sh <persona_name>
```

This creates a new tmux tab for that persona. Do NOT try to run `claude --system-prompt` directly - use the spawn script instead.

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
        self.conversation_history: list[dict] = []

    def get_system_prompt(self, project: str | None = None) -> str:
        """Get the full system prompt for Chat Hub, optionally project-aware."""
        # Resolve project display name
        project_name = None
        if project:
            try:
                from ..project_registry import get_project_registry
                proj = get_project_registry().get(project)
                if proj:
                    project_name = proj.display_name
            except Exception:
                pass

        # Build the base prompt (evaluated at call time, not import time)
        parts = [
            _build_chat_hub_system_prompt(project_name=project_name),
            "",
            "## Your Current Status",
            self.context.status_content[:1500] if self.context.status_content else "(No status loaded)",
        ]

        # If a project is specified, add project-specific context
        if project and project_name:
            try:
                from ..project_registry import get_project_registry
                proj = get_project_registry().get(project)
                if proj:
                    parts.append("")
                    parts.append(f"## Active Project: {proj.display_name}")
                    if proj.description:
                        parts.append(f"Description: {proj.description}")
                    parts.append(f"Working directory: `{proj.working_dir}`")
                    # Load project-specific status for active members
                    status_dir = proj.context_base / "status"
                    if status_dir.exists():
                        for status_file in sorted(status_dir.glob("status_*.md")):
                            member = status_file.stem.replace("status_", "")
                            content = status_file.read_text()[:300]
                            if content.strip():
                                parts.append(f"\n### {member.title()}'s Status")
                                parts.append(content)
            except Exception:
                pass

        return "\n".join(parts)

    async def process_message(
        self,
        message: str,
        sender: str = "user",
        thread_ts: str = "",
        conversation_history: list[dict] = None,
        project: str | None = None,
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

        # Ambiguous request — ask for clarification instead of guessing
        if work_info.get("needs_clarification"):
            target = work_info["target_persona"].title()
            return {
                "type": "clarification",
                "response": (
                    f"I'm not sure if you want me to start a work session for {target} "
                    f"on this, or if it's just a question. Could you clarify? "
                    f"(For a work session, say something like 'fix X in file.py' or "
                    f"'update Y'. For a question, just rephrase as a question.)"
                ),
                "task_prompt": None,
                "target_persona": work_info["target_persona"],
                "project": project,
            }

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
                project=project,
            )

            return {
                "type": "work_request",
                "response": f"Got it. I'll set up a work session for {work_info['target_persona'].title()}.",
                "task_prompt": task_prompt,
                "target_persona": work_info["target_persona"],
                "persona_explicit": work_info.get("persona_explicit", True),
                "files": work_info.get("files", []),
                "project": project,
            }

        # Regular chat - respond as Marcus (with conversation history)
        response = await self._get_chat_response(message, conversation_history, project=project)

        return {
            "type": "chat",
            "response": response,
            "task_prompt": None,
            "target_persona": None,
            "project": project,
        }

    async def _get_chat_response(
        self, message: str, conversation_history: list[dict] = None, project: str | None = None,
    ) -> str:
        """
        Get a chat response from Marcus using Claude CLI (async).

        Includes conversation history for context continuity.
        """
        # Build the prompt with conversation history (project-aware)
        parts = [self.get_system_prompt(project=project), "", "---", ""]

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

        # Resolve model: persona override > CHAT_MODEL > default
        profile = self.context.profile
        model = (
            getattr(profile, "model_override", None)
            or config.CHAT_MODEL
        )

        try:
            return await run_claude_cli(prompt, model=model)
        except ClaudeCliError as e:
            logger.error(f"Claude CLI error: {e}")
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
    import asyncio

    logging.basicConfig(level=logging.INFO)

    async def main():
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

                result = await hub.process_message(message)
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

    asyncio.run(main())
