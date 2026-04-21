"""
Watchdog — background task that monitors sessions and posts alerts to Slack.

Runs every WATCHDOG_INTERVAL_SECONDS (default 120 = 2 minutes) and checks:
  - Sessions exceeding soft timeout (idle warning)
  - Sessions exceeding hard timeout (force-close)
  - Crashed Claude CLI processes (dead pane)
  - Budget warnings at 80% threshold
  - Queue status (if tasks are waiting)

Posts notifications to SLACK_STATUS_CHANNEL (or SLACK_CHANNEL_ID fallback).
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

WATCHDOG_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "120"))


async def watchdog_loop(
    slack_client,
    shutdown_event: asyncio.Event,
    interval: int = WATCHDOG_INTERVAL,
) -> None:
    """
    Background watchdog loop. Runs until shutdown_event is set.

    Args:
        slack_client: Async Slack WebClient for posting notifications
        shutdown_event: Set this to stop the loop
        interval: Seconds between checks
    """
    from . import config

    status_channel = os.getenv("SLACK_STATUS_CHANNEL") or config.SLACK_CHANNEL_ID
    if not status_channel:
        logger.warning("No SLACK_STATUS_CHANNEL or SLACK_CHANNEL_ID — watchdog disabled")
        return

    logger.info(f"Watchdog started (interval={interval}s, channel={status_channel})")

    while not shutdown_event.is_set():
        try:
            await asyncio.sleep(interval)
            if shutdown_event.is_set():
                break

            alerts = await _run_checks()

            for alert in alerts:
                try:
                    await slack_client.chat_postMessage(
                        channel=status_channel,
                        text=alert,
                    )
                except Exception as e:
                    logger.error(f"Watchdog failed to post alert: {e}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Watchdog error: {e}")
            # Don't crash the loop on unexpected errors
            await asyncio.sleep(30)

    logger.info("Watchdog stopped")


async def _run_checks() -> list[str]:
    """
    Run all watchdog checks and return a list of alert messages.
    Empty list = everything is fine.
    """
    alerts: list[str] = []

    try:
        from .work_sessions import get_session_manager
        session_mgr = get_session_manager()

        # Run the timeout/crash sweep
        closed = session_mgr.auto_close_timed_out()
        for member in closed:
            alerts.append(
                f":warning: Watchdog: closed session for *{member.title()}* "
                f"(timeout or crash detected)"
            )

        # Check for idle/completed sessions
        for member, session in session_mgr.active_sessions.items():
            soft_threshold = session_mgr.TIMEOUT_MINUTES
            if session.can_auto_close:
                # Session is done — tell Casey it can be closed
                alerts.append(
                    f":white_check_mark: *{member.title()}*'s session is complete and can be closed. "
                    f"Use `Ctrl+b &` in tmux or `stop {member}` in Slack."
                )
            elif session.inactive_minutes > soft_threshold * 0.7:
                alerts.append(
                    f":hourglass: *{member.title()}* has been idle for "
                    f"{session.inactive_minutes:.0f} min "
                    f"(soft timeout at {soft_threshold} min)"
                )

    except Exception as e:
        logger.error(f"Watchdog session check error: {e}")

    # Budget warning
    try:
        from .cost_tracking import is_budget_warning, get_today_spend, DAILY_BUDGET_USD
        if is_budget_warning():
            spend, calls = get_today_spend()
            alerts.append(
                f":moneybag: Daily budget at *${spend:.2f}* / ${DAILY_BUDGET_USD:.2f} "
                f"({calls} API calls) — approaching limit"
            )
    except Exception as e:
        logger.error(f"Watchdog budget check error: {e}")

    # Queue status
    try:
        from .task_queue import get_task_queue
        queue = get_task_queue()
        if queue.size > 0:
            oldest = queue.peek()
            if oldest and oldest.wait_seconds > 300:  # Waiting > 5 min
                alerts.append(
                    f":hourglass_flowing_sand: {queue.size} task(s) queued — "
                    f"oldest waiting {oldest.wait_seconds / 60:.0f} min "
                    f"({oldest.member.title()})"
                )
    except Exception as e:
        logger.error(f"Watchdog queue check error: {e}")

    # Drain queue if slots are available
    try:
        from .work_sessions import get_session_manager
        session_mgr = get_session_manager()
        spawned = await session_mgr.drain_queue()
        for member in spawned:
            alerts.append(
                f":arrow_forward: Queued task started for *{member.title()}* "
                f"(slot freed by session closure)"
            )
    except Exception as e:
        logger.error(f"Watchdog queue drain error: {e}")

    if alerts:
        logger.info(f"Watchdog generated {len(alerts)} alert(s)")

    return alerts
