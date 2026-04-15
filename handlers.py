"""
Slack event handlers for Anchovies Hybrid Chat System.

Handles incoming Slack messages and routes them through the Chat Hub:
- Quick chat: Marcus responds directly via Claude CLI
- Work requests: Spawns a persona tab in tmux for file editing tasks
"""

import asyncio
import logging
import re
import time
from collections import OrderedDict

from slack_sdk import WebClient

from . import config
from . import messages
from .context import load_member_context
from .router import route_message, extract_bot_mention
from .cli_runner import generate_team_member_response, ClaudeCliError
from .chat_hub import ChatHub, create_chat_hub, detect_work_request
from .sanitiser import log_if_suspicious
from .work_sessions import get_tmux_manager, get_session_manager

logger = logging.getLogger(__name__)

# Singleton Chat Hub instance
_chat_hub: ChatHub | None = None

def get_chat_hub() -> ChatHub:
    """Get or create the Chat Hub instance."""
    global _chat_hub
    if _chat_hub is None:
        _chat_hub = create_chat_hub()
    return _chat_hub

# In-memory conversation store (thread_ts -> list of messages)
# Uses OrderedDict for LRU eviction — most recently accessed threads move to end
conversation_store: OrderedDict[str, list[dict]] = OrderedDict()
# Timestamps for when each thread was last accessed (for 24h cleanup)
_thread_last_accessed: dict[str, float] = {}
# Counter for periodic cleanup
_message_counter: int = 0

# Limits
MAX_THREADS = 500  # Max threads tracked before LRU eviction
MAX_MESSAGES_PER_THREAD = 200  # Max messages per thread
THREAD_TTL_SECONDS = 86400  # 24 hours

# Track chain depth per thread to prevent infinite loops
chain_depth: dict[str, int] = {}
MAX_CHAIN_DEPTH = 10  # Safety limit for cross-talk


def get_conversation_history(thread_ts: str) -> list[dict]:
    """Get conversation history for a thread, updating LRU position."""
    if thread_ts in conversation_store:
        # Move to end (most recently accessed)
        conversation_store.move_to_end(thread_ts)
        _thread_last_accessed[thread_ts] = time.time()
    return conversation_store.get(thread_ts, [])


async def handle_chat_hub_message(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    user_message: str,
    bot_user_id: str,
) -> bool:
    """
    Process a message through the Chat Hub.

    The Chat Hub (Marcus) decides whether this is:
    - Quick chat: Respond directly as Marcus
    - Work request: Spawn a persona tab in tmux

    Args:
        client: Slack WebClient
        channel_id: Channel where message was posted
        thread_ts: Thread timestamp
        user_message: The user's message text
        bot_user_id: The bot's Slack user ID

    Returns:
        True if handled by Chat Hub, False to fall through to legacy handling
    """
    # Remove bot mention from message
    cleaned_message = extract_bot_mention(user_message, bot_user_id)

    # Scan for prompt injection attempts (logs to audit, does NOT block)
    log_if_suspicious(cleaned_message, source=f"slack:{channel_id}:{thread_ts}")

    # Check if this is a work request
    hub = get_chat_hub()
    history = get_conversation_history(thread_ts)
    result = await hub.process_message(cleaned_message, thread_ts=thread_ts, conversation_history=history)

    if result["type"] == "work_request":
        # Work request detected - spawn persona tab
        member = result["target_persona"]
        task_prompt = result["task_prompt"]

        # Post acknowledgment to Slack
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f":hammer_and_wrench: *Marcus:* {result['response']}\n\n_Check the `{member}` tab in tmux for the work session._",
        )

        # Use session manager to spawn and track the session
        session_mgr = get_session_manager()
        tmux = get_tmux_manager()

        if not tmux.session_exists():
            # No tmux session - warn user
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=":warning: tmux session not running. Start it with `./scripts/start_anchovies.sh`",
            )
        elif session_mgr.has_session(member):
            # Session already exists - send task to existing session
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f":warning: {member.title()} already has an active session. Sending task to existing tab.",
            )
            tmux.send_to_work_pane(member, f"# New task from Slack:\n{cleaned_message}")
            session_mgr.touch_session(member)
        else:
            # Detect file conflicts and warn in Slack BEFORE starting
            files_for_task = result.get("files") if isinstance(result, dict) else []
            if files_for_task:
                conflicts = session_mgr.detect_file_conflicts(member, files_for_task)
                for other_member, overlap in conflicts:
                    client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=(
                            f":warning: *File conflict warning*: {member.title()} and "
                            f"{other_member.title()} both targeting `{', '.join(overlap)}`. "
                            f"Coordinate to avoid clobbering each other's changes."
                        ),
                    )

            # Start new session
            success = await session_mgr.start_session(
                member=member,
                task_description=cleaned_message[:100],
                task_prompt=task_prompt,
                thread_ts=thread_ts,
                channel_id=channel_id,
                files=files_for_task or [],
            )
            if not success:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f":x: Failed to spawn work session for {member.title()}.",
                )

        return True

    # Quick chat - respond as Marcus directly
    # Load Marcus's context for the response
    context = load_member_context(config.CHAT_HUB_PERSONA)
    profile = context.profile

    # Post "thinking" message
    thinking_response = client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        blocks=messages.build_thinking_message(
            profile.name,
            profile.avatar_emoji
        ),
        text=f"{profile.name} is thinking...",
    )
    thinking_ts = thinking_response["ts"]

    try:
        # Generate response using Claude CLI (through Chat Hub)
        response = result["response"]

        # If the Chat Hub didn't generate a response (shouldn't happen), generate one
        if not response:
            system_prompt = context.build_system_prompt()
            history = get_conversation_history(thread_ts)
            response = await generate_team_member_response(
                member_name=profile.name,
                system_prompt=system_prompt,
                user_message=cleaned_message,
                conversation_history=history,
            )

        # Update the thinking message with the response
        client.chat_update(
            channel=channel_id,
            ts=thinking_ts,
            blocks=messages.build_response_message(
                profile.name,
                response,
                profile.avatar_emoji
            ),
            text=f"{profile.name}: {response[:100]}...",
        )

        # Add to conversation history
        add_to_conversation(thread_ts, "user", cleaned_message)
        add_to_conversation(thread_ts, "assistant", response, config.CHAT_HUB_PERSONA)

        return True

    except Exception as e:
        logger.error(f"Error in Chat Hub response: {e}")
        client.chat_update(
            channel=channel_id,
            ts=thinking_ts,
            blocks=messages.build_error_message(
                f"{profile.name} encountered an error. Please try again."
            ),
            text=f"Error from {profile.name}",
        )
        return True


def add_to_conversation(thread_ts: str, role: str, content: str, member: str = ""):
    """Add a message to conversation history with LRU eviction."""
    global _message_counter

    if thread_ts not in conversation_store:
        # Evict oldest thread if at capacity
        while len(conversation_store) >= MAX_THREADS:
            evicted_ts, _ = conversation_store.popitem(last=False)
            _thread_last_accessed.pop(evicted_ts, None)
            logger.debug(f"LRU evicted thread {evicted_ts}")
        conversation_store[thread_ts] = []

    conversation_store[thread_ts].append({
        "role": role,
        "content": content,
        "member": member,
    })

    # Move to end (most recently accessed)
    conversation_store.move_to_end(thread_ts)
    _thread_last_accessed[thread_ts] = time.time()

    # Cap messages per thread
    if len(conversation_store[thread_ts]) > MAX_MESSAGES_PER_THREAD:
        conversation_store[thread_ts] = conversation_store[thread_ts][-MAX_MESSAGES_PER_THREAD:]

    # Periodic cleanup of old threads (every 100 messages)
    _message_counter += 1
    if _message_counter % 100 == 0:
        _cleanup_old_threads()


def _cleanup_old_threads():
    """Remove threads not accessed in the last 24 hours."""
    now = time.time()
    expired = [
        ts for ts, last in _thread_last_accessed.items()
        if now - last > THREAD_TTL_SECONDS
    ]
    for ts in expired:
        conversation_store.pop(ts, None)
        _thread_last_accessed.pop(ts, None)
    if expired:
        logger.info(f"Cleaned up {len(expired)} expired threads")


async def handle_team_message(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    user_message: str,
    bot_user_id: str,
    is_crosstalk: bool = False,
    source_member: str = "",
    use_chat_hub: bool = True,
) -> None:
    """
    Handle a message directed at team members.

    In Anchovies hybrid mode:
    - Work requests are routed through Chat Hub and spawn persona tabs
    - Quick chat goes to Chat Hub (Marcus) or specific personas

    Args:
        client: Slack WebClient
        channel_id: Channel where message was posted
        thread_ts: Thread timestamp (for threading replies)
        user_message: The user's message text
        bot_user_id: The bot's Slack user ID
        is_crosstalk: True if this is triggered by another team member
        source_member: Name of the team member who triggered this (for crosstalk)
        use_chat_hub: If True, route through Chat Hub first (default: True)
    """
    # Initialize or increment chain depth for this thread
    if not is_crosstalk:
        chain_depth[thread_ts] = 0
    else:
        chain_depth[thread_ts] = chain_depth.get(thread_ts, 0) + 1
        if chain_depth[thread_ts] > MAX_CHAIN_DEPTH:
            logger.warning(f"Chain depth limit reached in thread {thread_ts}")
            return

    # Remove bot mention from message
    cleaned_message = extract_bot_mention(user_message, bot_user_id)

    # Check for help request
    if cleaned_message.lower().strip() in ("help", "?", ""):
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=messages.build_help_message(),
            text="BI Team Forum Help",
        )
        return

    # Check if this is a work request (file edits, code changes, etc.)
    # Work requests should go through Chat Hub regardless of target persona
    if use_chat_hub and not is_crosstalk:
        work_info = detect_work_request(cleaned_message)
        if work_info["is_work_request"]:
            # Route through Chat Hub for work requests
            handled = await handle_chat_hub_message(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_message=user_message,
                bot_user_id=bot_user_id,
            )
            if handled:
                return

    # Route the message to team member(s)
    routing = route_message(cleaned_message)

    # If no specific member mentioned OR Marcus is the only target, route through Chat Hub
    # Marcus IS the Chat Hub, so messages to him should go through Chat Hub
    if use_chat_hub and not is_crosstalk:
        should_use_chat_hub = (
            not routing.members or  # No one mentioned
            routing.members == [config.CHAT_HUB_PERSONA] or  # Only Marcus mentioned
            config.CHAT_HUB_PERSONA in routing.members  # Marcus among targets
        )
        if should_use_chat_hub and not routing.is_broadcast:
            handled = await handle_chat_hub_message(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_message=user_message,
                bot_user_id=bot_user_id,
            )
            if handled:
                return

    # Don't let a member trigger themselves
    if is_crosstalk and source_member.lower() in routing.members:
        routing.members.remove(source_member.lower())

    if not routing.members:
        return

    logger.info(f"Routed to members: {routing.members}, broadcast: {routing.is_broadcast}, crosstalk: {is_crosstalk}")

    # Add message to conversation history
    if is_crosstalk:
        add_to_conversation(thread_ts, "assistant", routing.cleaned_message, source_member)
    else:
        add_to_conversation(thread_ts, "user", routing.cleaned_message)

    # Process each targeted member
    for member_name in routing.members:
        response = await process_member_response(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            member_name=member_name,
            user_message=routing.cleaned_message,
            bot_user_id=bot_user_id,
        )

        # Small delay between multiple members
        if len(routing.members) > 1:
            await asyncio.sleep(0.5)


def detect_mentions_in_response(response: str) -> list[str]:
    """
    Detect @mentions of team members in a response.

    Args:
        response: The response text to scan

    Returns:
        List of mentioned team member names (lowercase)
    """
    mentioned = []
    all_names = config.TEAM_MEMBERS + list(config.MEMBER_ALIASES.keys())
    pattern = r"@(" + "|".join(re.escape(name) for name in all_names) + r")\b"

    matches = re.findall(pattern, response, re.IGNORECASE)
    for match in matches:
        member = config.get_member_name(match)
        if member and member not in mentioned:
            mentioned.append(member)

    return mentioned


async def process_member_response(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    member_name: str,
    user_message: str,
    bot_user_id: str = "",
) -> str | None:
    """
    Generate and post a response from a specific team member.

    Args:
        client: Slack WebClient
        channel_id: Channel to post in
        thread_ts: Thread timestamp
        member_name: The team member's name
        user_message: The user's message
        bot_user_id: Bot's user ID (for crosstalk handling)

    Returns:
        The response text, or None if failed
    """
    # Load member context (profile only for efficiency)
    context = load_member_context(member_name)
    profile = context.profile

    # Post "thinking" message
    thinking_response = client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        blocks=messages.build_thinking_message(
            profile.name,
            profile.avatar_emoji
        ),
        text=f"{profile.name} is thinking...",
    )
    thinking_ts = thinking_response["ts"]

    try:
        # Build system prompt (profile + job description + status)
        system_prompt = context.build_system_prompt()

        # Get conversation history from thread memory
        history = get_conversation_history(thread_ts)

        # Generate response using Claude CLI
        response = await generate_team_member_response(
            member_name=profile.name,
            system_prompt=system_prompt,
            user_message=user_message,
            conversation_history=history,
        )

        # Update the thinking message with the response
        client.chat_update(
            channel=channel_id,
            ts=thinking_ts,
            blocks=messages.build_response_message(
                profile.name,
                response,
                profile.avatar_emoji
            ),
            text=f"{profile.name}: {response[:100]}...",
        )

        # Add response to conversation history
        add_to_conversation(thread_ts, "assistant", response, member_name)

        # Check for cross-talk: did this member @mention another team member?
        mentioned_members = detect_mentions_in_response(response)
        if mentioned_members:
            logger.info(f"{profile.name} mentioned: {mentioned_members}")
            # Trigger responses from mentioned members
            await asyncio.sleep(0.5)  # Brief pause before crosstalk
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
        client.chat_update(
            channel=channel_id,
            ts=thinking_ts,
            blocks=messages.build_error_message(
                f"{profile.name} couldn't respond: {str(e)[:100]}"
            ),
            text=f"Error from {profile.name}",
        )
        return None

    except Exception as e:
        logger.error(f"Unexpected error for {member_name}: {e}")
        client.chat_update(
            channel=channel_id,
            ts=thinking_ts,
            blocks=messages.build_error_message(
                f"{profile.name} encountered an error. Please try again."
            ),
            text=f"Error from {profile.name}",
        )
        return None
