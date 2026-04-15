"""
Main Slack Bolt application for Team Forum Bot.

This bot enables real-time chat with AI team members in Slack.
Each team member has their own personality profile and responds in character.

Run with: python -m team_forum_bot.app
"""

import asyncio
import logging
import os
import signal
import sys

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from . import config
from . import messages
from .handlers import handle_team_message, conversation_store
from .storage import get_storage
from .work_sessions import get_session_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> AsyncApp:
    """Create and configure the async Slack Bolt app."""
    # Validate configuration
    is_valid, errors = config.validate_config()
    if not is_valid:
        logger.error("Configuration errors:")
        for error in errors:
            logger.error(f"  - {error}")
        logger.error("\nPlease check your .env file and ensure all required variables are set.")
        sys.exit(1)

    # Create the async app
    app = AsyncApp(
        token=config.SLACK_BOT_TOKEN,
        signing_secret=config.SLACK_SIGNING_SECRET,
    )

    # Bot user ID is resolved at startup in main()
    # and stored on the app instance for handlers to access
    app._bot_user_id = None

    # Register app mention handler
    @app.event("app_mention")
    async def handle_app_mention(event, say, client):
        """Handle when the bot is @mentioned in a channel."""
        channel_id = event["channel"]
        user_message = event.get("text", "")
        thread_ts = event.get("thread_ts") or event["ts"]
        channel_type = event.get("channel_type")  # Usually "channel" for @mentions

        # Enforce channel allowlist (public channels only — DMs always pass)
        if not config.is_channel_allowed(channel_id, channel_type):
            logger.info(f"Ignoring mention in non-allowed channel {channel_id}")
            return

        logger.info(f"Received mention in {channel_id}: {user_message[:50]}...")

        await handle_team_message(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_message=user_message,
            bot_user_id=app._bot_user_id,
            user_id=event.get("user", "anonymous"),
        )

    # Handle messages (DMs, private channels, and group DMs)
    @app.event("message")
    async def handle_message(event, say, client):
        """Handle messages in DMs and private channels without needing @mention."""
        # Ignore bot messages and message edits/deletes
        if event.get("bot_id") or event.get("subtype"):
            return

        channel_type = event.get("channel_type")

        # Respond without @mention in:
        # - im: Direct messages
        # - mpim: Multi-person DMs (group DMs)
        # - group: Private channels
        # - private_channel: Private channels (alternative name)
        if channel_type not in ("im", "mpim", "group", "private_channel"):
            # For public channels, only respond to @mentions (handled by app_mention)
            return

        channel_id = event["channel"]

        # DMs/group DMs/private channels bypass the allowlist, but we still
        # run the check here in case channel_type is unexpected.
        if not config.is_channel_allowed(channel_id, channel_type):
            logger.info(f"Ignoring message in non-allowed channel {channel_id}")
            return

        user_message = event.get("text", "")
        thread_ts = event.get("thread_ts") or event["ts"]

        logger.info(f"Received message in {channel_type}: {user_message[:50]}...")

        await handle_team_message(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_message=user_message,
            bot_user_id=app._bot_user_id,
            user_id=event.get("user", "anonymous"),
        )

    # Handle app home opened
    @app.event("app_home_opened")
    async def handle_app_home_opened(client, event):
        """Update the App Home tab when a user opens it."""
        user_id = event["user"]

        try:
            await client.views_publish(
                user_id=user_id,
                view={
                    "type": "home",
                    "blocks": messages.build_help_message()
                }
            )
        except Exception as e:
            logger.error(f"Failed to publish home tab: {e}")

    return app


async def flush_state_to_storage():
    """
    Persist all in-memory state to SQLite. Called on graceful shutdown.

    Saves: conversation_store (all threads), active sessions, bot_stopped audit event.
    Returns a summary dict for logging.
    """
    storage = get_storage()
    summary = {"conversations": 0, "sessions": 0}

    # Flush conversation store
    for thread_ts, messages in conversation_store.items():
        try:
            storage.save_conversation(thread_ts, messages)
            summary["conversations"] += 1
        except Exception as e:
            logger.error(f"Failed to persist conversation {thread_ts}: {e}")

    # Session manager already writes through on every change, but re-save
    # all active sessions just in case.
    try:
        mgr = get_session_manager()
        for member, session in mgr.active_sessions.items():
            mgr._persist(session)
            summary["sessions"] += 1
    except Exception as e:
        logger.error(f"Failed to persist sessions: {e}")

    # Log the shutdown event itself
    try:
        storage.log_event(
            "bot_stopped",
            details={
                "conversations_saved": summary["conversations"],
                "sessions_saved": summary["sessions"],
            },
        )
    except Exception as e:
        logger.error(f"Failed to log shutdown event: {e}")

    return summary


async def notify_shutdown(slack_client, summary: dict) -> None:
    """Post a shutdown notification to Slack. Best-effort — failure is non-fatal."""
    status_channel = os.getenv("SLACK_STATUS_CHANNEL") or config.SLACK_CHANNEL_ID
    if not status_channel:
        logger.warning("No SLACK_STATUS_CHANNEL or SLACK_CHANNEL_ID set — skipping Slack shutdown notification")
        return

    try:
        await slack_client.chat_postMessage(
            channel=status_channel,
            text=(
                f":zzz: *Anchovies bot going offline.* "
                f"Preserved {summary['sessions']} active session(s) and "
                f"{summary['conversations']} conversation(s). "
                f"tmux work sessions remain running for recovery on restart."
            ),
        )
    except Exception as e:
        logger.error(f"Failed to post shutdown notification: {e}")


async def graceful_shutdown(handler: AsyncSocketModeHandler, slack_client) -> None:
    """
    Graceful shutdown sequence:
      1. Stop accepting new Slack events
      2. Flush in-memory state to SQLite
      3. Post shutdown notification to Slack
      4. (Do NOT kill tmux sessions — they should survive bot restarts)
    """
    logger.info("Graceful shutdown initiated...")

    # Step 1: stop accepting events
    try:
        await handler.close_async()
    except Exception as e:
        logger.error(f"Error closing Socket Mode handler: {e}")

    # Step 2: flush state
    summary = await flush_state_to_storage()
    logger.info(
        f"Shutdown state flushed: {summary['conversations']} conversations, "
        f"{summary['sessions']} sessions"
    )

    # Step 3: notify Slack
    await notify_shutdown(slack_client, summary)

    logger.info("Graceful shutdown complete")


async def async_main():
    """Run the Slack bot in Socket Mode (async)."""
    print("\n" + "=" * 60)
    print("STARTING BI TEAM FORUM BOT (ASYNC)")
    print("=" * 60)
    print(f"Bot Token: {config.SLACK_BOT_TOKEN[:20]}..." if config.SLACK_BOT_TOKEN else "Bot Token: MISSING!")
    print(f"App Token: {config.SLACK_APP_TOKEN[:20]}..." if config.SLACK_APP_TOKEN else "App Token: MISSING!")
    print(f"Context Base: {config.CONTEXT_BASE}")
    print(f"Profiles Dir: {config.PROFILES_DIR}")
    print("=" * 60 + "\n")

    # Create the async app
    app = create_app()

    # Resolve bot user ID
    from slack_sdk.web.async_client import AsyncWebClient
    async_client = AsyncWebClient(token=config.SLACK_BOT_TOKEN)
    bot_info = await async_client.auth_test()
    app._bot_user_id = bot_info["user_id"]
    logger.info(f"Bot user ID: {app._bot_user_id}")

    # Log startup and recover any sessions from previous runs
    storage = get_storage()
    storage.log_event("bot_started", details={"bot_user_id": app._bot_user_id})

    try:
        mgr = get_session_manager()
        stats = mgr.recover_from_storage()
        if stats["restored"] or stats["crashed"]:
            logger.info(
                f"Session recovery: {stats['restored']} restored, "
                f"{stats['crashed']} crashed, {stats['orphaned_tabs']} orphaned tabs"
            )
            storage.log_event("session_recovery", details=stats)
    except Exception as e:
        logger.error(f"Session recovery failed (continuing anyway): {e}")

    # Start async Socket Mode handler
    handler = AsyncSocketModeHandler(app, config.SLACK_APP_TOKEN)

    # Register signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def request_shutdown(signum, frame=None):
        logger.info(f"Received signal {signum}, requesting shutdown...")
        shutdown_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, lambda: shutdown_event.set())
        loop.add_signal_handler(signal.SIGTERM, lambda: shutdown_event.set())
    except NotImplementedError:
        # Windows doesn't support add_signal_handler — fall back to signal.signal
        signal.signal(signal.SIGINT, request_shutdown)
        signal.signal(signal.SIGTERM, request_shutdown)

    print("Connecting to Slack via Socket Mode...")
    print("Bot is running! Press Ctrl+C to stop gracefully.")
    print("Waiting for messages...\n")

    # Run handler and wait for shutdown signal concurrently
    handler_task = asyncio.create_task(handler.start_async())
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    done, pending = await asyncio.wait(
        {handler_task, shutdown_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel whichever didn't complete
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Run graceful shutdown sequence
    await graceful_shutdown(handler, async_client)


def main():
    """Entry point — runs the async bot."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        # If asyncio.run catches KeyboardInterrupt before our signal handler,
        # we still want a clean exit code.
        logger.info("Interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
