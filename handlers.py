"""
Slack event handlers for Anchovies Hybrid Chat System.

Handles incoming Slack messages and routes them through the Chat Hub:
- Quick chat: Marcus responds directly via Claude CLI
- Work requests: Spawns a persona tab in tmux for file editing tasks
"""

import asyncio
import logging
import os
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
from .rate_limit import get_rate_limiter
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

# Track summon chain depth per thread to prevent runaway cross-talk costs.
# Configurable via MAX_SUMMON_DEPTH env var (Casey's default: 3).
chain_depth: dict[str, int] = {}
MAX_CHAIN_DEPTH = int(os.getenv("MAX_SUMMON_DEPTH", "3"))

# Pause flag — when True, the bot refuses new work requests (chat still works)
_paused: bool = False

# Pending routing suggestions — thread_ts -> suggestion details
# When Marcus suggests a persona, the suggestion is stored here until
# Casey confirms ("yes") or names a different persona.
import time as _time

_pending_suggestions: dict[str, dict] = {}
SUGGESTION_TIMEOUT_SECONDS = 300  # 5 minutes


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
    project: str | None = None,
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
    logger.info(f"[ChatHub] Processing: '{cleaned_message[:80]}' project={project}")

    # Scan for prompt injection attempts (logs to audit, does NOT block)
    log_if_suspicious(cleaned_message, source=f"slack:{channel_id}:{thread_ts}")

    # Post "Marcus is thinking..." immediately so the user knows the bot is working.
    thinking_response = await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=":hourglass: *Marcus* is thinking...",
    )
    thinking_ts = thinking_response["ts"]
    logger.info("[ChatHub] Posted thinking indicator, calling Claude CLI...")

    # Check if this is a work request
    hub = get_chat_hub()
    history = get_conversation_history(thread_ts)
    result = await hub.process_message(cleaned_message, thread_ts=thread_ts, conversation_history=history, project=project)
    logger.info(
        f"[ChatHub] Hub returned type={result['type']} "
        f"target={result.get('target_persona')} "
        f"explicit={result.get('persona_explicit')} "
        f"project={result.get('project')}"
    )

    if result["type"] == "work_request":
        # Delete the thinking message — work request flow posts its own messages
        try:
            await client.chat_delete(channel=channel_id, ts=thinking_ts)
        except Exception:
            pass

        # Work request detected - spawn persona tab
        member = result["target_persona"]
        task_prompt = result["task_prompt"]
        logger.info(f"[ChatHub] Work request for {member}, explicit={result.get('persona_explicit')}")

        logger.info(f"[ChatHub] Budget check...")
        # Budget gate: refuse to spawn new work sessions when daily cap is hit.
        # (Cheap chat responses are still allowed below.)
        from .cost_tracking import is_budget_exceeded, get_today_spend, DAILY_BUDGET_USD
        if is_budget_exceeded():
            spend, _ = get_today_spend()
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=(
                    f":no_entry: Daily Claude budget reached "
                    f"(${spend:.2f} / ${DAILY_BUDGET_USD:.2f}). "
                    f"No new work sessions until midnight."
                ),
            )
            logger.warning(
                f"Budget exceeded — refused work session for {member} "
                f"(${spend:.2f}/${DAILY_BUDGET_USD:.2f})"
            )
            return True

        # Smart routing: if no persona was explicitly named, suggest or fall through to chat
        if not result.get("persona_explicit", True):
            logger.info("[ChatHub] No explicit persona — trying smart routing...")
            from .teams import get_suggested_persona
            suggestion = get_suggested_persona(cleaned_message)
            logger.info(f"[ChatHub] Smart routing suggestion: {suggestion}")
            if suggestion:
                suggested_member, track_display, reason = suggestion
                # Store the pending suggestion
                _pending_suggestions[thread_ts] = {
                    "suggested_member": suggested_member,
                    "track": track_display,
                    "reason": reason,
                    "task_description": cleaned_message,
                    "task_prompt": task_prompt,
                    "files": result.get("files", []),
                    "project": project,
                    "created_at": _time.time(),
                }
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=(
                        f":mag: *Marcus:* This looks like a task for "
                        f"*{suggested_member.title()}* ({reason}).\n"
                        f"Reply *yes* to assign, or name a different persona."
                    ),
                )
                return True
            else:
                # No keyword match — fall through to chat so Marcus can discuss and recommend
                logger.info("[ChatHub] No smart routing match — Marcus will discuss and recommend")
                # Don't spawn. Delete thinking msg and go to chat path.
                try:
                    await client.chat_delete(channel=channel_id, ts=thinking_ts)
                except Exception:
                    pass
                # Jump to the chat response path (result already has Marcus's response)
                # We need to re-enter the chat path — simplest: just return the chat handling
                context = load_member_context(config.CHAT_HUB_PERSONA)
                profile = context.profile
                thinking2 = await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=":hourglass: *Marcus* is thinking...",
                )
                response = result["response"]
                await client.chat_update(
                    channel=channel_id, ts=thinking2["ts"],
                    blocks=messages.build_response_message(profile.name, response, profile.avatar_emoji),
                    text=f"{profile.name}: {response[:100]}...",
                )
                add_to_conversation(thread_ts, "user", cleaned_message)
                add_to_conversation(thread_ts, "assistant", response, config.CHAT_HUB_PERSONA)
                _detect_assignment_in_response(response, thread_ts, cleaned_message, project)
                return True

        # Guard: NEVER spawn Marcus as a worker — show his chat response instead
        if member == config.CHAT_HUB_PERSONA:
            logger.info("[ChatHub] Marcus is the target — responding as Director (not spawning)")
            context = load_member_context(config.CHAT_HUB_PERSONA)
            profile = context.profile
            response = result.get("response", "")
            if response:
                await client.chat_update(
                    channel=channel_id, ts=thinking_ts,
                    blocks=messages.build_response_message(profile.name, response, profile.avatar_emoji),
                    text=f"{profile.name}: {response[:100]}...",
                )
            else:
                await client.chat_delete(channel=channel_id, ts=thinking_ts)
            add_to_conversation(thread_ts, "user", cleaned_message)
            add_to_conversation(thread_ts, "assistant", response, config.CHAT_HUB_PERSONA)
            _detect_assignment_in_response(response, thread_ts, cleaned_message, project)
            return True

        # Post acknowledgment to Slack
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f":hammer_and_wrench: *Marcus:* {result['response']}\n\n_Check the `{member}` tab in tmux for the work session._",
        )

        # Use session manager to spawn and track the session
        session_mgr = get_session_manager()
        tmux = get_tmux_manager()

        if not tmux.session_exists():
            logger.warning("[ChatHub] tmux session not running!")
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=":warning: tmux session not running. Start it with `./scripts/start_anchovies.sh`",
            )
        elif session_mgr.has_session(member):
            logger.info(f"[ChatHub] {member} already has active session, sending to existing tab")
            # Session already exists - send task to existing session
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f":warning: {member.title()} already has an active session. Sending task to existing tab.",
            )
            tmux.send_to_work_pane(member, f"# New task from Slack:\n{cleaned_message}")
            session_mgr.touch_session(member)
        else:
            files_for_task = result.get("files") if isinstance(result, dict) else []

            # Check concurrent session limit — queue if at capacity
            from .task_queue import get_task_queue, QueuedTask, MAX_CONCURRENT_SESSIONS
            queue = get_task_queue()
            active_count = len(session_mgr.active_sessions)
            logger.info(f"[ChatHub] Active sessions: {active_count}/{MAX_CONCURRENT_SESSIONS}")

            if active_count >= MAX_CONCURRENT_SESSIONS:
                logger.info(f"[ChatHub] At capacity — queuing {member}")
                # Queue the task instead of spawning
                position = queue.enqueue(QueuedTask(
                    member=member,
                    task_description=cleaned_message[:100],
                    task_prompt=task_prompt,
                    thread_ts=thread_ts,
                    channel_id=channel_id,
                    files=files_for_task or [],
                    project=result.get("project"),
                ))
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=(
                        f":hourglass: {member.title()} is queued — "
                        f"{active_count} sessions active (max {MAX_CONCURRENT_SESSIONS}). "
                        f"Position: {position}. Will start when a session frees up."
                    ),
                )
            else:
                # Detect file conflicts and warn in Slack BEFORE starting
                if files_for_task:
                    conflicts = session_mgr.detect_file_conflicts(member, files_for_task)
                    for other_member, overlap in conflicts:
                        await client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
                            text=(
                                f":warning: *File conflict warning*: {member.title()} and "
                                f"{other_member.title()} both targeting `{', '.join(overlap)}`. "
                                f"Coordinate to avoid clobbering each other's changes."
                            ),
                        )

                # Start new session
                logger.info(f"[ChatHub] Spawning {member} for '{cleaned_message[:50]}'...")
                success = await session_mgr.start_session(
                    member=member,
                    task_description=cleaned_message[:100],
                    task_prompt=task_prompt,
                    thread_ts=thread_ts,
                    channel_id=channel_id,
                    files=files_for_task or [],
                    project=result.get("project"),
                )
                logger.info(f"[ChatHub] Spawn result for {member}: {'SUCCESS' if success else 'FAILED'}")
                if not success:
                    await client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f":x: Failed to spawn work session for {member.title()}.",
                    )

        return True

    # Quick chat - respond as Marcus directly
    # The early "thinking..." message (thinking_ts) is already visible.
    context = load_member_context(config.CHAT_HUB_PERSONA)
    profile = context.profile

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
        await client.chat_update(
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

        # Check if Marcus's response suggests assigning a persona.
        # If so, create a pending suggestion so Casey can just reply "yes".
        _detect_assignment_in_response(response, thread_ts, cleaned_message, project)

        return True

    except Exception as e:
        logger.error(f"Error in Chat Hub response: {e}")
        await client.chat_update(
            channel=channel_id,
            ts=thinking_ts,
            blocks=messages.build_error_message(
                f"{profile.name} encountered an error. Please try again."
            ),
            text=f"Error from {profile.name}",
        )
        return True


def _detect_assignment_in_response(
    response: str, thread_ts: str, original_message: str, project: str | None,
) -> None:
    """
    Scan Marcus's chat response for persona assignment language.

    If Marcus says something like "I'll have Sofia start" or "Let's assign Elena",
    create a pending suggestion so Casey can confirm with "yes".

    This bridges the gap between Marcus's Director role (proposing assignments
    in natural language) and the system's spawn mechanism (which needs explicit
    confirmation).
    """
    # Look for assignment patterns mentioning a team member
    assignment_patterns = [
        r"(?:I'll have|Let's have|I'll assign|Let's assign|start with|I recommend)\s+(\w+)",
        r"(\w+)\s+(?:should start|can start|will start|can handle|should handle|can take|should take)",
        r"(?:assign|send|give)\s+(?:this|it|the task)\s+to\s+(\w+)",
    ]

    for pattern in assignment_patterns:
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            name = match.group(1).lower()
            member = config.get_member_name(name)
            if member and member != "marcus":  # Don't suggest Marcus assigning to himself
                # Create a pending suggestion
                _pending_suggestions[thread_ts] = {
                    "suggested_member": member,
                    "track": "",
                    "reason": "recommended by Marcus",
                    "task_description": original_message,
                    "task_prompt": "",  # Will be built at spawn time
                    "files": [],
                    "project": project,
                    "created_at": _time.time(),
                    "needs_prompt_build": True,  # Flag to build prompt at confirmation
                }
                logger.info(
                    f"[ChatHub] Marcus recommended {member} in response — "
                    f"created pending suggestion (Casey can reply 'yes')"
                )
                return  # Only create one suggestion


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
    user_id: str = "anonymous",
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
    logger.info(f"[Handler] Message from {user_id}: '{user_message[:60]}' channel={channel_id} crosstalk={is_crosstalk}")

    # Rate limiting (skip for crosstalk — that's bot-to-bot, not user-driven)
    if not is_crosstalk:
        limiter = get_rate_limiter()
        if not limiter.allow(user_id=user_id):
            logger.warning(f"Rate limit hit for user {user_id} in {channel_id}")
            try:
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=(
                        ":hourglass_flowing_sand: I'm at capacity right now — "
                        "please try again in a moment."
                    ),
                )
            except Exception as e:
                logger.error(f"Failed to post rate-limit reply: {e}")
            return

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

    # Extract [project] tag early so it's available for all code paths
    from .router import extract_project_tag
    message_project, cleaned_message = extract_project_tag(cleaned_message)
    logger.info(f"[Handler] Project tag: {message_project}, cleaned: '{cleaned_message[:60]}'")

    # If no tag, check for default project from registry
    if message_project is None:
        try:
            from .project_registry import get_project_registry
            registry = get_project_registry()
            default = registry.get_default()
            if default:
                message_project = default.name
        except Exception:
            pass

    # Check for pending routing suggestion confirmation
    suggestion_handled = await _check_pending_suggestion(
        client, channel_id, thread_ts, cleaned_message
    )
    if suggestion_handled:
        return

    # Check for help request
    if cleaned_message.lower().strip() in ("help", "?", ""):
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=messages.build_help_message(),
            text="BI Team Forum Help",
        )
        return

    # Check for control commands (kill switch, pause, resume, daily summary)
    control_result = await _handle_control_command(client, channel_id, thread_ts, cleaned_message)
    if control_result:
        return

    # If paused, reject work requests but allow chat
    global _paused
    if _paused and not is_crosstalk:
        # Check if this looks like a work request — if so, reject
        work_info = detect_work_request(cleaned_message)
        if work_info["is_work_request"]:
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=":pause_button: Bot is paused — not accepting new work requests. Use `resume` to re-enable.",
            )
            return
        # Non-work chat still goes through

    # Check for project management commands (before work request detection)
    project_cmd = parse_project_command(cleaned_message)
    if project_cmd:
        await handle_project_command(client, channel_id, thread_ts, project_cmd)
        return

    # Check if this is a work request (file edits, code changes, etc.)
    # Work requests should go through Chat Hub regardless of target persona
    if use_chat_hub and not is_crosstalk:
        work_info = detect_work_request(cleaned_message)
        logger.info(
            f"[Handler] Work detection: is_work={work_info['is_work_request']} "
            f"confidence={work_info.get('confidence', '?')} "
            f"target={work_info.get('target_persona')} "
            f"explicit={work_info.get('persona_explicit')}"
        )
        if work_info["is_work_request"]:
            # Route through Chat Hub for work requests
            handled = await handle_chat_hub_message(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_message=user_message,
                bot_user_id=bot_user_id,
                project=message_project,
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
                project=message_project or routing.project,
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


async def _check_pending_suggestion(
    client, channel_id: str, thread_ts: str, message: str,
) -> bool:
    """
    Check if there's a pending routing suggestion for this thread and
    handle confirmation/redirection.

    Returns True if the message was a confirmation (handled), False otherwise.
    """
    # Clean up expired suggestions
    now = _time.time()
    expired = [ts for ts, s in _pending_suggestions.items()
               if now - s["created_at"] > SUGGESTION_TIMEOUT_SECONDS]
    for ts in expired:
        del _pending_suggestions[ts]

    # Check if this thread has a pending suggestion
    suggestion = _pending_suggestions.get(thread_ts)
    if not suggestion:
        return False

    msg = message.strip().lower()

    # "yes", "go", "do it", "ok" = confirm the suggested persona
    if msg in ("yes", "go", "do it", "ok", "sure", "go ahead", "y"):
        del _pending_suggestions[thread_ts]
        member = suggestion["suggested_member"]
        await _spawn_from_suggestion(client, channel_id, thread_ts, suggestion, member)
        return True

    # A persona name = redirect to that persona instead
    resolved = config.get_member_name(msg)
    if resolved:
        del _pending_suggestions[thread_ts]
        # Rebuild prompt for the new persona
        suggestion["suggested_member"] = resolved
        await _spawn_from_suggestion(client, channel_id, thread_ts, suggestion, resolved)
        return True

    # "no" or "cancel" = discard the suggestion
    if msg in ("no", "cancel", "nevermind", "nah"):
        del _pending_suggestions[thread_ts]
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=":x: Suggestion cancelled.",
        )
        return True

    # Any other message — not a confirmation, fall through to normal routing
    # (the suggestion stays pending)
    return False


async def _spawn_from_suggestion(
    client, channel_id: str, thread_ts: str, suggestion: dict, member: str,
) -> None:
    """Spawn a work session from a confirmed routing suggestion."""
    from .chat_hub.prompt_builder import build_task_prompt

    task_description = suggestion["task_description"]
    project = suggestion.get("project")
    files = suggestion.get("files", [])

    task_prompt = build_task_prompt(
        persona=member,
        task_description=task_description,
        files=files,
        context=task_description,
        thread_ts=thread_ts,
        project=project,
    )

    session_mgr = get_session_manager()

    from .task_queue import get_task_queue, QueuedTask, MAX_CONCURRENT_SESSIONS
    queue = get_task_queue()
    active_count = len(session_mgr.active_sessions)

    if active_count >= MAX_CONCURRENT_SESSIONS:
        position = queue.enqueue(QueuedTask(
            member=member, task_description=task_description[:100],
            task_prompt=task_prompt, thread_ts=thread_ts,
            channel_id=channel_id, files=files, project=project,
        ))
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":hourglass: {member.title()} queued (position {position}).",
        )
        return

    await client.chat_postMessage(
        channel=channel_id, thread_ts=thread_ts,
        text=f":hammer_and_wrench: Spawning {member.title()} for this task...",
    )

    success = await session_mgr.start_session(
        member=member,
        task_description=task_description[:100],
        task_prompt=task_prompt,
        thread_ts=thread_ts,
        channel_id=channel_id,
        files=files,
        project=project,
    )
    if not success:
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":x: Failed to spawn {member.title()}.",
        )


async def _handle_control_command(client, channel_id: str, thread_ts: str, message: str) -> bool:
    """
    Handle bot control commands: stop all, stop <name>, pause, resume, daily summary.
    Returns True if the message was a control command (handled), False otherwise.
    """
    global _paused
    msg = message.strip().lower()

    # --- stop all ---
    if msg in ("stop all", "stop everything", "kill all"):
        session_mgr = get_session_manager()
        tmux = get_tmux_manager()
        closed = []
        for member in list(session_mgr.active_sessions.keys()):
            tmux.close_persona_tab(member)
            session_mgr.storage.mark_session_status(member, "killed")
            session_mgr.storage.log_event("session_killed", member=member, details={"reason": "stop all"})
            del session_mgr.active_sessions[member]
            closed.append(member)

        # Clear the queue too
        from .task_queue import get_task_queue
        queue = get_task_queue()
        queued_count = queue.clear()

        text = f":octagonal_sign: All sessions stopped."
        if closed:
            text += f"\nKilled: {', '.join(m.title() for m in closed)}"
        if queued_count:
            text += f"\nCleared {queued_count} queued task(s)."
        await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)
        return True

    # --- stop <name> ---
    stop_match = re.match(r"stop\s+(\w+)", msg)
    if stop_match:
        name = stop_match.group(1).lower()
        if name in ("all", "everything"):
            return False  # handled above
        member = config.get_member_name(name)
        if not member:
            return False  # not a member name, fall through to normal routing

        session_mgr = get_session_manager()
        if member in session_mgr.active_sessions:
            tmux = get_tmux_manager()
            tmux.close_persona_tab(member)
            session_mgr.storage.mark_session_status(member, "killed")
            session_mgr.storage.log_event("session_killed", member=member, details={"reason": "stop command"})
            del session_mgr.active_sessions[member]
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=f":octagonal_sign: {member.title()}'s session stopped.",
            )
        else:
            # Also remove from queue if queued
            from .task_queue import get_task_queue
            removed = get_task_queue().remove_member(member)
            if removed:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f":octagonal_sign: Removed {member.title()} from the queue.",
                )
            else:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f":shrug: {member.title()} has no active session or queued task.",
                )
        return True

    # --- pause ---
    if msg == "pause":
        _paused = True
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=":pause_button: Bot paused — no new work sessions will be accepted. Active sessions continue. Use `resume` to re-enable.",
        )
        return True

    # --- resume ---
    if msg == "resume":
        _paused = False
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=":arrow_forward: Bot resumed — accepting work requests again.",
        )
        return True

    # --- assign <persona> <task> ---
    assign_match = re.match(r"assign\s+(\w+)\s+(.+)", message.strip(), re.IGNORECASE)
    if assign_match:
        name = assign_match.group(1).lower()
        task_desc = assign_match.group(2).strip()
        member = config.get_member_name(name)
        if member:
            logger.info(f"[Control] Assign command: {member} -> '{task_desc[:50]}'")
            # Build the suggestion and auto-confirm it
            _pending_suggestions[thread_ts] = {
                "suggested_member": member,
                "track": "",
                "reason": "direct assignment",
                "task_description": task_desc,
                "task_prompt": "",
                "files": [],
                "project": None,  # Will use message_project from the calling context
                "created_at": _time.time(),
                "needs_prompt_build": True,
            }
            # Extract project from the task description
            from .router import extract_project_tag
            assign_project, assign_task = extract_project_tag(task_desc)
            if assign_project:
                _pending_suggestions[thread_ts]["project"] = assign_project
                _pending_suggestions[thread_ts]["task_description"] = assign_task

            # Auto-spawn (no need for confirmation — assign is explicit)
            suggestion = _pending_suggestions.pop(thread_ts)
            await _spawn_from_suggestion(client, channel_id, thread_ts, suggestion, member)
            return True
        else:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=f":warning: Unknown team member: `{name}`. Use `@bot projects` to see available personas.",
            )
            return True

    # --- brief ---
    if msg.startswith("brief ") or msg == "brief":
        from .director import create_project_brief
        from .router import extract_project_tag
        brief_project, brief_text = extract_project_tag(message.strip())
        # Strip "brief" prefix
        brief_text = re.sub(r"^brief\s*", "", brief_text, flags=re.IGNORECASE).strip()
        if not brief_text:
            brief_text = "General project overview"
        brief = create_project_brief(
            task_description=brief_text,
            project=brief_project,
        )
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":clipboard: *Marcus:*\n\n{brief}",
        )
        return True

    # --- consult ---
    if msg.startswith("consult ") or msg == "consult":
        from .director import create_project_brief, compile_consultation
        from .teams import get_relevant_personas
        from .router import extract_project_tag
        from .cli_runner import run_claude_cli

        consult_project, consult_text = extract_project_tag(message.strip())
        consult_text = re.sub(r"^consult\s*", "", consult_text, flags=re.IGNORECASE).strip()
        if not consult_text:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=":warning: Usage: `consult [project] <task description>`",
            )
            return True

        # Find relevant personas to consult
        relevant = get_relevant_personas(consult_text, n=5)
        if not relevant:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=":mag: No relevant personas found for this task. Try being more specific.",
            )
            return True

        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":speech_balloon: Consulting {len(relevant)} persona(s): {', '.join(r[0].title() for r in relevant)}...",
        )

        # Gather input from each persona concurrently
        import asyncio as _asyncio
        persona_inputs = {}

        async def _get_persona_input(member_name, role_track):
            prompt = (
                f"You are {member_name.title()}, on the {role_track} track. "
                f"You've been asked for your perspective on:\n"
                f"{consult_text}\n"
                f"In 2-3 sentences, share your key concern, suggestion, or insight "
                f"from your area of expertise. Be specific and actionable."
            )
            try:
                response = await run_claude_cli(prompt, model=config.CHAT_MODEL)
                persona_inputs[member_name] = response
            except Exception as e:
                persona_inputs[member_name] = f"_(consultation failed: {e})_"

        tasks = [
            _get_persona_input(member, get_track_display_name(track))
            for member, track, _ in relevant
        ]
        from .teams import get_track_display_name
        await _asyncio.gather(*tasks)

        # Compile the consultation
        compiled = compile_consultation(consult_text, persona_inputs, consult_project)
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":clipboard: *Team Consultation:*\n\n{compiled}",
        )
        return True

    # --- reflect ---
    reflect_match = re.match(r"reflect\s*(\w*)", msg)
    if reflect_match:
        member_name = reflect_match.group(1) or None
        if member_name and member_name in config.TEAM_MEMBERS:
            from .reflection import manual_reflect
            reflection = await manual_reflect(member_name, project=None)
            if reflection:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f":mirror: *{member_name.title()}'s Reflection:*\n\n{reflection}",
                )
            else:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f":warning: Reflection failed for {member_name.title()}.",
                )
        else:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=":mirror: Usage: `reflect <persona_name>` — e.g., `reflect sofia`",
            )
        return True

    # --- daily summary ---
    if msg in ("daily summary", "summary", "status summary"):
        from .cost_tracking import get_today_spend, DAILY_BUDGET_USD
        from .task_queue import get_task_queue

        session_mgr = get_session_manager()
        storage = session_mgr.storage
        spend, calls = get_today_spend()
        queue = get_task_queue()

        # Count today's completed sessions from audit
        today_start = time.time() - (time.time() % 86400)  # midnight UTC approx
        completed = storage.query_audit(since=today_start, event_type="session_completed")
        started = storage.query_audit(since=today_start, event_type="session_started")

        active = session_mgr.list_sessions()
        active_lines = []
        for s in active:
            project_tag = f" [{s.project}]" if s.project else ""
            active_lines.append(f"  - {s.member.title()}{project_tag}: {s.task_description[:40]}... ({s.total_minutes:.0f}m)")

        lines = [
            f":bar_chart: *Daily Summary*",
            f"",
            f"*Sessions:* {len(started)} started, {len(completed)} completed, {len(active)} active",
        ]
        if active_lines:
            lines.append("*Active now:*")
            lines.extend(active_lines)

        queue_size = queue.size
        if queue_size:
            lines.append(f"*Queued:* {queue_size} task(s) waiting")

        lines.append(f"")
        lines.append(f"*Cost:* ${spend:.2f} / ${DAILY_BUDGET_USD:.2f} ({calls} API calls)")

        paused_tag = " :pause_button: *PAUSED*" if _paused else ""
        lines.append(f"*Status:* {'Paused' if _paused else 'Running'}{paused_tag}")

        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text="\n".join(lines),
        )
        return True

    return False


def parse_project_command(message: str) -> dict | None:
    """
    Detect if a message is a project management command.

    Returns a dict with 'command' and 'args' keys, or None if not a command.
    """
    msg = message.strip().lower()

    # "projects" or "list projects"
    if msg in ("projects", "list projects"):
        return {"command": "list", "args": ""}

    # "add project <name> --context <path> [--working-dir <path>] [--desc '...']"
    add_match = re.match(
        r"add\s+project\s+(\S+)\s+--context\s+(\S+)(.*)",
        message.strip(),
        re.IGNORECASE,
    )
    if add_match:
        name = add_match.group(1).lower()
        context = add_match.group(2)
        rest = add_match.group(3)
        working_dir = context  # default
        description = ""
        wd_match = re.search(r"--working-dir\s+(\S+)", rest)
        if wd_match:
            working_dir = wd_match.group(1)
        desc_match = re.search(r'--desc\s+"([^"]+)"', rest) or re.search(r"--desc\s+'([^']+)'", rest) or re.search(r"--desc\s+(\S+)", rest)
        if desc_match:
            description = desc_match.group(1)
        return {
            "command": "add",
            "args": {"name": name, "context": context, "working_dir": working_dir, "description": description},
        }

    # "remove project <name>"
    remove_match = re.match(r"remove\s+project\s+(\S+)", msg)
    if remove_match:
        return {"command": "remove", "args": remove_match.group(1)}

    # "set default project <name>"
    set_match = re.match(r"set\s+default\s+project\s+(\S+)", msg)
    if set_match:
        return {"command": "set_default", "args": set_match.group(1)}

    # "clear default project"
    if msg in ("clear default project", "clear default"):
        return {"command": "clear_default", "args": ""}

    # "project info <name>"
    info_match = re.match(r"project\s+info\s+(\S+)", msg)
    if info_match:
        return {"command": "info", "args": info_match.group(1)}

    return None


async def handle_project_command(
    client,
    channel_id: str,
    thread_ts: str,
    cmd: dict,
) -> None:
    """Execute a project management command and post the result to Slack."""
    from .project_registry import Project, get_project_registry, ensure_project_dirs

    registry = get_project_registry()
    registry.reload_if_changed()
    command = cmd["command"]
    args = cmd["args"]

    if command == "list":
        projects = registry.list_projects(active_only=False)
        if not projects:
            text = ":file_folder: No projects registered.\nUse `add project <name> --context <path>` to add one."
        else:
            lines = [":file_folder: *Registered Projects:*"]
            default_name = registry.get_default_name()
            for p in projects:
                status = ":white_check_mark:" if p.active else ":no_entry_sign:"
                default_tag = " _(default)_" if p.name == default_name else ""
                lines.append(
                    f"  {status} *{p.display_name}* (`{p.name}`){default_tag}"
                )
                if p.description:
                    lines.append(f"      {p.description}")
                lines.append(f"      Context: `{p.context_base}`")
                if str(p.working_dir) != str(p.context_base):
                    lines.append(f"      Working dir: `{p.working_dir}`")
            text = "\n".join(lines)

    elif command == "add":
        from pathlib import Path
        name = args["name"]
        project = Project(
            name=name,
            display_name=name.replace("-", " ").replace("_", " ").title(),
            context_base=Path(args["context"]).expanduser(),
            working_dir=Path(args["working_dir"]).expanduser(),
            description=args.get("description", ""),
        )
        registry.register(project)
        ensure_project_dirs(project)
        registry.save_to_yaml()
        text = f":white_check_mark: Project *{project.display_name}* (`{name}`) registered.\nContext: `{project.context_base}`\nWorking dir: `{project.working_dir}`"

    elif command == "remove":
        name = args
        if registry.unregister(name):
            registry.save_to_yaml()
            text = f":wastebasket: Project `{name}` removed."
        else:
            text = f":warning: Project `{name}` not found."

    elif command == "set_default":
        name = args
        if registry.get(name):
            registry.set_default(name)
            registry.save_to_yaml()
            proj = registry.get(name)
            text = f":pushpin: Default project set to *{proj.display_name}* (`{name}`).\nMessages without a `[project]` tag will use this project."
        else:
            text = f":warning: Project `{name}` not found. Use `projects` to see available projects."

    elif command == "clear_default":
        registry.set_default(None)
        registry.save_to_yaml()
        text = ":pushpin: Default project cleared. Messages without a `[project]` tag will use the generic team context."

    elif command == "info":
        name = args
        proj = registry.get(name)
        if proj:
            default_tag = " _(default)_" if registry.get_default_name() == name else ""
            text = (
                f":file_folder: *{proj.display_name}* (`{proj.name}`){default_tag}\n"
                f"Description: {proj.description or '(none)'}\n"
                f"Context: `{proj.context_base}`\n"
                f"Working dir: `{proj.working_dir}`\n"
                f"Default branch: `{proj.default_branch}`\n"
                f"Active: {'Yes' if proj.active else 'No'}"
            )
        else:
            text = f":warning: Project `{name}` not found."

    else:
        text = f":warning: Unknown project command: `{command}`"

    await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)


def detect_summons_in_response(response: str) -> list[str]:
    """
    Detect explicit /summon commands in a persona's response.

    Cross-talk is now opt-in (Casey's decision in makeover Q&A).
    A persona must explicitly write `/summon <name>` to bring another
    team member into the conversation. Plain @mentions are NOT enough —
    they're conversational only and don't trigger anything.

    Examples that summon Sofia:
        "Let me check with /summon sofia about this"
        "/summon sofia, can you weigh in?"

    Examples that do NOT summon (plain mentions, no trigger):
        "Sofia and I worked on this last week"
        "Ask @sofia later"

    Args:
        response: The response text to scan

    Returns:
        List of summoned team member names (lowercase, deduplicated, in order)
    """
    summoned = []
    all_names = config.TEAM_MEMBERS + list(config.MEMBER_ALIASES.keys())
    # /summon must be followed by a name (with optional comma/punct after)
    pattern = r"/summon\s+(" + "|".join(re.escape(name) for name in all_names) + r")\b"

    for match in re.finditer(pattern, response, re.IGNORECASE):
        member = config.get_member_name(match.group(1))
        if member and member not in summoned:
            summoned.append(member)

    return summoned


# Backwards-compatible alias — some tests/code may still reference the old name.
# It now requires /summon and only finds explicit summons.
detect_mentions_in_response = detect_summons_in_response


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
    thinking_response = await client.chat_postMessage(
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

        # Resolve model: profile override > WORK_MODEL > default
        model = (
            getattr(profile, "model_override", None)
            or config.WORK_MODEL
        )

        # Generate response using Claude CLI
        response = await generate_team_member_response(
            member_name=profile.name,
            system_prompt=system_prompt,
            user_message=user_message,
            conversation_history=history,
            model=model,
        )

        # Update the thinking message with the response
        await client.chat_update(
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

        # Check for cross-talk: did this member /summon another team member?
        # Cross-talk is opt-in only — plain @mentions don't trigger anymore.
        summoned_members = detect_summons_in_response(response)
        if summoned_members:
            logger.info(f"{profile.name} summoned: {summoned_members}")
            # Trigger responses from summoned members
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
        await client.chat_update(
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
        await client.chat_update(
            channel=channel_id,
            ts=thinking_ts,
            blocks=messages.build_error_message(
                f"{profile.name} encountered an error. Please try again."
            ),
            text=f"Error from {profile.name}",
        )
        return None
