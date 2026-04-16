"""
Claude CLI subprocess runner for Team Forum Bot.

Spawns Claude CLI to generate responses for team members.
"""

import asyncio
import logging
import shutil

from . import config

logger = logging.getLogger(__name__)


class ClaudeCliError(Exception):
    """Error running Claude CLI."""
    pass


async def run_claude_cli(
    prompt: str,
    timeout: float = 120.0,
    model: str | None = None,
) -> str:
    """
    Run Claude CLI and return the response.

    Args:
        prompt: The prompt to send to Claude
        timeout: Maximum time to wait for response (seconds)
        model: Model name (e.g., 'haiku', 'sonnet', 'opus'). If None,
               uses config.CLAUDE_MODEL (legacy default).

    Returns:
        Claude's response text

    Raises:
        ClaudeCliError: If CLI execution fails
    """
    # Check if claude CLI is available
    claude_path = shutil.which(config.CLAUDE_CLI_PATH)
    if not claude_path:
        raise ClaudeCliError(f"Claude CLI not found: {config.CLAUDE_CLI_PATH}")

    # Build the command — add --model flag if model is specified
    cmd = [claude_path, "--print"]
    selected_model = model or config.CLAUDE_MODEL
    if selected_model:
        cmd.extend(["--model", selected_model])
    cmd.extend(["-p", prompt])

    try:
        # Run claude with --print flag for non-interactive output
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise ClaudeCliError(f"Claude CLI timed out after {timeout} seconds")

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise ClaudeCliError(f"Claude CLI failed (code {process.returncode}): {error_msg}")

        response = stdout.decode().strip()

        if not response:
            raise ClaudeCliError("Claude CLI returned empty response")

        # Record the cost for budget tracking. Best-effort — never fail the
        # call if cost recording fails.
        try:
            from . import cost_tracking
            cost_tracking.record_call(prompt, response, model=selected_model)
        except Exception as e:
            logger.error(f"Cost tracking failed: {e}")

        return response

    except asyncio.CancelledError:
        raise
    except ClaudeCliError:
        raise
    except Exception as e:
        raise ClaudeCliError(f"Unexpected error running Claude CLI: {e}")


async def generate_team_member_response(
    member_name: str,
    system_prompt: str,
    user_message: str,
    conversation_history: list[dict] | None = None,
    model: str | None = None,
) -> str:
    """
    Generate a response from a team member using Claude CLI.

    Args:
        member_name: The team member's name
        system_prompt: The system prompt with member context
        user_message: The user's message
        conversation_history: Optional list of previous messages

    Returns:
        The team member's response
    """
    # Build the full prompt
    parts = [
        system_prompt,
        "",
        "---",
        "",
    ]

    # Add conversation history if present
    if conversation_history:
        parts.append("## Recent Conversation")
        for msg in conversation_history[-10:]:  # Limit to last 10 messages
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"User: {content}")
            else:
                parts.append(f"{member_name}: {content}")
        parts.append("")
        parts.append("---")
        parts.append("")

    # Add the current message
    parts.append(f"User's message: {user_message}")
    parts.append("")
    parts.append(f"Respond as {member_name} would:")

    full_prompt = "\n".join(parts)

    logger.info(f"Generating response for {member_name}")
    logger.debug(f"Prompt length: {len(full_prompt)} chars")

    response = await run_claude_cli(full_prompt, model=model)

    logger.info(f"Got response from {member_name} ({len(response)} chars)")

    return response
