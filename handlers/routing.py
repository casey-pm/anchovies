"""
Message routing for Anchovies.

Main entry point: handle_team_message() receives Slack messages and routes them to:
  - Control commands (stop, pause, assign, brief, etc.)
  - Chat Hub (Marcus as Director)
  - Individual persona responses
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from slack_sdk import WebClient

from .. import config
from .. import messages
from ..context import load_member_context
from ..cli_runner import generate_team_member_response, ClaudeCliError
from ..chat_hub import ChatHub, create_chat_hub, detect_work_request
from ..rate_limit import get_rate_limiter
from ..sanitiser import log_if_suspicious
from ..router import route_message, extract_bot_mention

from .memory import get_conversation_history, add_to_conversation
from .control_commands import (
    handle_control_command, parse_project_command, handle_project_command, is_paused,
)
from .spawner import spawn_session_for_task, auto_spawn_from_director, save_project_spec

logger = logging.getLogger(__name__)

# Singleton Chat Hub instance
_chat_hub: ChatHub | None = None

# Cross-talk chain depth tracking
chain_depth: dict[str, int] = {}
MAX_CHAIN_DEPTH = int(os.getenv("MAX_SUMMON_DEPTH", "3"))


def get_chat_hub() -> ChatHub:
    """Get or create the Chat Hub instance."""
    global _chat_hub
    if _chat_hub is None:
        _chat_hub = create_chat_hub()
    return _chat_hub


# ---------------------------------------------------------------------------
# Chat Hub message handler (Marcus)
# ---------------------------------------------------------------------------


async def handle_chat_hub_message(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    user_message: str,
    bot_user_id: str,
    project: str | None = None,
) -> bool:
    """
    Process a message through the Chat Hub (Marcus).

    Returns True if handled, False to fall through to legacy handling.
    """
    cleaned_message = extract_bot_mention(user_message, bot_user_id)
    logger.info(f"[ChatHub] Processing: '{cleaned_message[:80]}' project={project}")

    log_if_suspicious(cleaned_message, source=f"slack:{channel_id}:{thread_ts}")

    # Post thinking indicator
    thinking_response = await client.chat_postMessage(
        channel=channel_id, thread_ts=thread_ts,
        text=":hourglass: Marcus is thinking...",
    )
    thinking_ts = thinking_response["ts"]
    logger.info("[ChatHub] Posted thinking indicator, calling Claude CLI...")

    # Process through hub
    hub = get_chat_hub()
    history = get_conversation_history(thread_ts)
    logger.info(f"[ChatHub] Conversation history for thread {thread_ts}: {len(history)} messages")
    result = await hub.process_message(
        cleaned_message, thread_ts=thread_ts,
        conversation_history=history, project=project,
    )
    logger.info(
        f"[ChatHub] Hub returned type={result['type']} "
        f"target={result.get('target_persona')} "
        f"explicit={result.get('persona_explicit')} "
        f"project={result.get('project')}"
    )

    if result["type"] == "work_request":
        # Delete thinking message — other messages will follow
        try:
            await client.chat_delete(channel=channel_id, ts=thinking_ts)
        except Exception:
            pass

        member = result["target_persona"]
        task_prompt = result.get("task_prompt")

        # Budget gate
        logger.info("[ChatHub] Budget check...")
        from ..cost_tracking import is_budget_exceeded, get_today_spend, DAILY_BUDGET_USD
        if is_budget_exceeded():
            spend, _ = get_today_spend()
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=(
                    f":no_entry: Daily Claude budget reached "
                    f"(${spend:.2f} / ${DAILY_BUDGET_USD:.2f}). "
                    f"No new work sessions until midnight."
                ),
            )
            return True

        # No explicit persona → Marcus responds as Director
        if not result.get("persona_explicit", True):
            logger.info("[ChatHub] No explicit persona — Marcus responds as Director")
            context_member = load_member_context(config.CHAT_HUB_PERSONA)
            profile = context_member.profile
            response = result.get("response", "")
            if response:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    blocks=messages.build_response_message(profile.name, response, profile.avatar_emoji),
                    text=f"{profile.name}: {response[:100]}...",
                )
            add_to_conversation(thread_ts, "user", cleaned_message)
            add_to_conversation(thread_ts, "assistant", response, config.CHAT_HUB_PERSONA)
            logger.info(f"[ChatHub] Marcus's response (first 200 chars): {response[:200]}")

            if project and response:
                save_project_spec(project, response, cleaned_message)

            if response:
                await auto_spawn_from_director(
                    client, channel_id, thread_ts, response, cleaned_message, project
                )
            return True

        # Marcus is the target → respond as Director (never spawn Marcus)
        if member == config.CHAT_HUB_PERSONA:
            logger.info("[ChatHub] Marcus is the target — responding as Director")
            context_member = load_member_context(config.CHAT_HUB_PERSONA)
            profile = context_member.profile
            response = result.get("response", "")
            if response:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    blocks=messages.build_response_message(profile.name, response, profile.avatar_emoji),
                    text=f"{profile.name}: {response[:100]}...",
                )
            add_to_conversation(thread_ts, "user", cleaned_message)
            add_to_conversation(thread_ts, "assistant", response, config.CHAT_HUB_PERSONA)

            if project and response:
                save_project_spec(project, response, cleaned_message)
            if response:
                await auto_spawn_from_director(
                    client, channel_id, thread_ts, response, cleaned_message, project
                )
            return True

        # Explicit persona named → spawn directly
        logger.info(f"[ChatHub] Explicit persona: {member}")
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":hammer_and_wrench: Marcus: {result.get('response', '')}",
        )

        files_for_task = result.get("files", [])
        tmux = get_tmux_manager()
        if not tmux.session_exists():
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=":warning: tmux session not running. Start it with `./scripts/start_anchovies.sh`",
            )
        else:
            await spawn_session_for_task(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                member=member,
                task_description=cleaned_message[:100],
                task_prompt=task_prompt,
                files=files_for_task,
                project=result.get("project"),
                announce=False,  # Already posted acknowledgment above
            )
        return True

    # Quick chat — Marcus responds directly
    context_member = load_member_context(config.CHAT_HUB_PERSONA)
    profile = context_member.profile

    try:
        response = result["response"]
        if not response:
            system_prompt = context_member.build_system_prompt()
            history = get_conversation_history(thread_ts)
            response = await generate_team_member_response(
                member_name=profile.name,
                system_prompt=system_prompt,
                user_message=cleaned_message,
                conversation_history=history,
            )

        await client.chat_update(
            channel=channel_id, ts=thinking_ts,
            blocks=messages.build_response_message(profile.name, response, profile.avatar_emoji),
            text=f"{profile.name}: {response[:100]}...",
        )
        add_to_conversation(thread_ts, "user", cleaned_message)
        add_to_conversation(thread_ts, "assistant", response, config.CHAT_HUB_PERSONA)
        return True

    except Exception as e:
        logger.error(f"Error in Chat Hub response: {e}")
        await client.chat_update(
            channel=channel_id, ts=thinking_ts,
            blocks=messages.build_error_message(
                f"{profile.name} encountered an error. Please try again."
            ),
            text=f"Error from {profile.name}",
        )
        return True


# ---------------------------------------------------------------------------
# Main message handler
# ---------------------------------------------------------------------------


def should_route_to_chat_hub(cleaned_message: str, routing_result, is_crosstalk: bool) -> bool:
    """Single decision: should this message go to Marcus (Chat Hub)?"""
    if is_crosstalk:
        return False

    # Work request → always Chat Hub
    work_info = detect_work_request(cleaned_message)
    if work_info["is_work_request"]:
        return True

    # No specific member OR Marcus mentioned → Chat Hub
    if not routing_result.members:
        return True
    if config.CHAT_HUB_PERSONA in routing_result.members:
        return True
    if routing_result.members == [config.DEFAULT_MEMBER]:
        return True

    return False


async def handle_team_message(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    user_message: str,
    bot_user_id: str,
    is_crosstalk: bool = False,
    source_member: str = "",
    use_chat_hub: bool = True,
    user_id: str = "anonymous",
) -> None:
    """Main message handler — routes Slack messages to the right destination."""

    logger.info(
        f"[Handler] Message from {user_id}: '{user_message[:60]}' "
        f"channel={channel_id} crosstalk={is_crosstalk}"
    )

    # Rate limiting (skip for crosstalk)
    if not is_crosstalk:
        limiter = get_rate_limiter()
        if not limiter.allow(user_id=user_id):
            logger.warning(f"Rate limit hit for user {user_id}")
            try:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=":hourglass_flowing_sand: I'm at capacity — please try again in a moment.",
                )
            except Exception:
                pass
            return

    # Chain depth tracking
    if not is_crosstalk:
        chain_depth[thread_ts] = 0
    else:
        chain_depth[thread_ts] = chain_depth.get(thread_ts, 0) + 1
        if chain_depth[thread_ts] > MAX_CHAIN_DEPTH:
            logger.warning(f"Chain depth limit reached in thread {thread_ts}")
            return

    # Clean the message
    cleaned_message = extract_bot_mention(user_message, bot_user_id)

    # Extract [project] tag
    from ..router import extract_project_tag
    message_project, cleaned_message = extract_project_tag(cleaned_message)
    logger.info(f"[Handler] Project tag: {message_project}, cleaned: '{cleaned_message[:60]}'")

    # Default project from registry
    if message_project is None:
        try:
            from ..project_registry import get_project_registry
            default = get_project_registry().get_default()
            if default:
                message_project = default.name
        except Exception:
            pass

    # Help
    if cleaned_message.lower().strip() in ("help", "?", ""):
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            blocks=messages.build_help_message(),
            text="BI Team Forum Help",
        )
        return

    # Control commands (stop, pause, assign, brief, etc.)
    control_result = await handle_control_command(client, channel_id, thread_ts, cleaned_message)
    if control_result:
        return

    # Paused check
    if is_paused() and not is_crosstalk:
        work_info = detect_work_request(cleaned_message)
        if work_info["is_work_request"]:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=":pause_button: Bot is paused — not accepting new work requests. Use `resume` to re-enable.",
            )
            return

    # Project management commands
    project_cmd = parse_project_command(cleaned_message)
    if project_cmd:
        await handle_project_command(client, channel_id, thread_ts, project_cmd)
        return

    # Route the message
    routing = route_message(cleaned_message)

    # Single routing decision: Chat Hub or individual persona?
    if use_chat_hub and should_route_to_chat_hub(cleaned_message, routing, is_crosstalk):
        handled = await handle_chat_hub_message(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_message=user_message,
            bot_user_id=bot_user_id,
            project=message_project or routing.project,
        )
        if handled:
            return

    # Prevent self-triggering in crosstalk
    if is_crosstalk and source_member.lower() in routing.members:
        routing.members.remove(source_member.lower())
    if not routing.members:
        return

    logger.info(f"Routed to members: {routing.members}, broadcast: {routing.is_broadcast}")

    # Store conversation history
    if is_crosstalk:
        add_to_conversation(thread_ts, "assistant", routing.cleaned_message, source_member)
    else:
        add_to_conversation(thread_ts, "user", routing.cleaned_message)

    # Generate responses from targeted personas
    for member_name in routing.members:
        await process_member_response(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            member_name=member_name,
            user_message=routing.cleaned_message,
            bot_user_id=bot_user_id,
        )
        if len(routing.members) > 1:
            await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Individual persona response
# ---------------------------------------------------------------------------


def detect_summons_in_response(response: str) -> list[str]:
    """Detect explicit /summon commands in a persona's response."""
    summoned = []
    all_names = config.TEAM_MEMBERS + list(config.MEMBER_ALIASES.keys())
    pattern = r"/summon\s+(" + "|".join(re.escape(name) for name in all_names) + r")\b"

    for match in re.finditer(pattern, response, re.IGNORECASE):
        member = config.get_member_name(match.group(1))
        if member and member not in summoned:
            summoned.append(member)

    return summoned


async def process_member_response(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    member_name: str,
    user_message: str,
    bot_user_id: str = "",
) -> str | None:
    """Generate and post a response from a specific team member."""
    context = load_member_context(member_name)
    profile = context.profile

    thinking_response = await client.chat_postMessage(
        channel=channel_id, thread_ts=thread_ts,
        blocks=messages.build_thinking_message(profile.name, profile.avatar_emoji),
        text=f"{profile.name} is thinking...",
    )
    thinking_ts = thinking_response["ts"]

    try:
        system_prompt = context.build_system_prompt()
        history = get_conversation_history(thread_ts)
        model = getattr(profile, "model_override", None) or config.WORK_MODEL

        response = await generate_team_member_response(
            member_name=profile.name,
            system_prompt=system_prompt,
            user_message=user_message,
            conversation_history=history,
            model=model,
        )

        await client.chat_update(
            channel=channel_id, ts=thinking_ts,
            blocks=messages.build_response_message(profile.name, response, profile.avatar_emoji),
            text=f"{profile.name}: {response[:100]}...",
        )

        add_to_conversation(thread_ts, "assistant", response, member_name)

        # Check for /summon cross-talk
        summoned_members = detect_summons_in_response(response)
        if summoned_members:
            logger.info(f"{profile.name} summoned: {summoned_members}")
            await asyncio.sleep(0.5)
            await handle_team_message(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_message=response,
                bot_user_id=bot_user_id,
                is_crosstalk=True,
                source_member=profile.name,
            )

        return response

    except ClaudeCliError as e:
        logger.error(f"Claude CLI error for {member_name}: {e}")
        await client.chat_update(
            channel=channel_id, ts=thinking_ts,
            blocks=messages.build_error_message(f"{profile.name} couldn't respond: {str(e)[:100]}"),
            text=f"Error from {profile.name}",
        )
        return None

    except Exception as e:
        logger.error(f"Unexpected error for {member_name}: {e}")
        await client.chat_update(
            channel=channel_id, ts=thinking_ts,
            blocks=messages.build_error_message(f"{profile.name} encountered an error. Please try again."),
            text=f"Error from {profile.name}",
        )
        return None
