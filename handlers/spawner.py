"""
Unified session spawning for Anchovies.

Single code path for all session spawning — whether triggered by
explicit assignment, Director auto-spawn, or direct @mention.
Handles: capacity check, queuing, file conflict detection, spawn, failure.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .. import config
from ..work_sessions import get_session_manager, get_tmux_manager

logger = logging.getLogger(__name__)


async def spawn_session_for_task(
    client,
    channel_id: str,
    thread_ts: str,
    member: str,
    task_description: str,
    task_prompt: str,
    files: list[str] | None = None,
    project: str | None = None,
    announce: bool = True,
) -> bool:
    """
    Unified session spawning. All spawn paths call this.

    Handles:
      1. Capacity check — queue if at MAX_CONCURRENT_SESSIONS
      2. File conflict detection — warn (don't block) if overlapping files
      3. Session spawn via tmux
      4. Success/failure Slack notification

    Args:
        client: Async Slack WebClient
        channel_id: Slack channel
        thread_ts: Slack thread
        member: Persona to spawn (lowercase)
        task_description: Brief task description (used for logging + branch name)
        task_prompt: Full prompt for the Claude CLI session
        files: Optional files this session will work on
        project: Optional project slug
        announce: If True, post spawn/queue notification to Slack

    Returns:
        True if spawned (or queued) successfully
    """
    files = files or []
    session_mgr = get_session_manager()

    # Already has an active session?
    if session_mgr.has_session(member):
        logger.info(f"[Spawner] {member} already has active session, sending to existing tab")
        tmux = get_tmux_manager()
        tmux.send_to_work_pane(member, f"# New task:\n{task_description}")
        session_mgr.touch_session(member)
        if announce:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=f":warning: {member.title()} already has an active session. Sent task to existing tab.",
            )
        return True

    # Capacity check
    from ..task_queue import get_task_queue, QueuedTask, MAX_CONCURRENT_SESSIONS
    queue = get_task_queue()
    active_count = len(session_mgr.active_sessions)
    logger.info(f"[Spawner] Active sessions: {active_count}/{MAX_CONCURRENT_SESSIONS}")

    if active_count >= MAX_CONCURRENT_SESSIONS:
        logger.info(f"[Spawner] At capacity — queuing {member}")
        position = queue.enqueue(QueuedTask(
            member=member,
            task_description=task_description[:100],
            task_prompt=task_prompt,
            thread_ts=thread_ts,
            channel_id=channel_id,
            files=files,
            project=project,
        ))
        if announce:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=(
                    f":hourglass: {member.title()} is queued — "
                    f"{active_count} sessions active (max {MAX_CONCURRENT_SESSIONS}). "
                    f"Position: {position}."
                ),
            )
        return True  # Queued successfully

    # File conflict detection
    if files:
        conflicts = session_mgr.detect_file_conflicts(member, files)
        for other_member, overlap in conflicts:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=(
                    f":warning: File conflict: {member.title()} and "
                    f"{other_member.title()} both targeting {', '.join(overlap)}. "
                    f"Coordinate to avoid clobbering each other's changes."
                ),
            )

    # Spawn
    if announce:
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":rocket: Spawning {member.title()}...",
        )

    logger.info(f"[Spawner] Spawning {member} for '{task_description[:50]}'...")
    success = await session_mgr.start_session(
        member=member,
        task_description=task_description[:100],
        task_prompt=task_prompt,
        thread_ts=thread_ts,
        channel_id=channel_id,
        files=files,
        project=project,
    )
    logger.info(f"[Spawner] Spawn result for {member}: {'SUCCESS' if success else 'FAILED'}")

    if not success:
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":x: Failed to spawn {member.title()}.",
        )

    return success


async def auto_spawn_from_director(
    client,
    channel_id: str,
    thread_ts: str,
    director_response: str,
    original_message: str,
    project: str | None,
) -> list[str]:
    """
    Scan Marcus's Director response for assignments and auto-spawn personas.

    Returns list of member names that were spawned.
    """
    from ..chat_hub.prompt_builder import build_task_prompt

    _name = r"\*{0,2}(\w+)\*{0,2}"
    assignment_patterns = [
        rf"(?:I'll have|Let's have|I'll assign|Let's assign|start with)\s+{_name}\s+(.*?)(?:\.|$)",
        rf"(?:I recommend|recommend)\s+(?:starting with|assigning|having)?\s*{_name}\s+(.*?)(?:\.|$)",
        rf"{_name}\s+(?:should start|can start|will start|can handle|should handle|should work on|can work on)\s+(.*?)(?:\.|$)",
        rf"(?:Let's start with|begin with|kick off with)\s+{_name}\s+(.*?)(?:\.|$)",
    ]

    spawned = []

    for pattern in assignment_patterns:
        for match in re.finditer(pattern, director_response, re.IGNORECASE | re.MULTILINE):
            name = match.group(1).lower()
            member = config.get_member_name(name)
            if not member or member == "marcus" or member in spawned:
                continue

            # Extract task from Marcus's sentence
            task_text = match.group(2).strip() if match.lastindex >= 2 else ""
            task_text = re.sub(r"^(?:on|to|with|for|by)\s+", "", task_text, flags=re.IGNORECASE).strip()
            if not task_text or len(task_text) < 5:
                task_text = original_message

            # Build context with Director's full plan
            context = (
                f"Director's Plan (from Marcus):\n{director_response}\n\n"
                f"Your specific assignment: {task_text}"
            )

            task_prompt = build_task_prompt(
                persona=member,
                task_description=task_text,
                context=context,
                thread_ts=thread_ts,
                project=project,
            )

            logger.info(f"[Director] Auto-spawning {member} for: {task_text[:50]}")
            success = await spawn_session_for_task(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                member=member,
                task_description=task_text[:100],
                task_prompt=task_prompt,
                project=project,
            )
            if success:
                spawned.append(member)

        if spawned:
            break  # Don't match multiple patterns for the same assignments

    if spawned:
        logger.info(f"[Director] Auto-spawned from Marcus's plan: {spawned}")

    return spawned


def save_project_spec(project: str, director_response: str, original_request: str) -> None:
    """Save Marcus's Director plan as SPEC.md in the project directory."""
    try:
        from ..project_registry import get_project_registry
        proj = get_project_registry().get(project)
        if not proj:
            return

        from datetime import datetime
        spec_path = proj.working_dir / "SPEC.md"
        spec_content = f"""# Project Spec — {proj.display_name}

*Generated by Marcus (Director) on {datetime.now().strftime('%Y-%m-%d %H:%M')}*

## Request

{original_request}

## Plan

{director_response}
"""
        spec_path.write_text(spec_content)
        logger.info(f"[Spawner] Saved project spec to {spec_path}")

        try:
            from ..storage import get_storage
            get_storage().log_event(
                "spec_created",
                member="marcus",
                details={"project": project, "path": str(spec_path)},
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[Spawner] Failed to save project spec: {e}")
