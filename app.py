"""
Main Slack Bolt application for Team Forum Bot.

This bot enables real-time chat with AI team members in Slack.
Each team member has their own personality profile and responds in character.

Run with: python -m team_forum_bot.app
"""

import asyncio
import logging
import os
import sys

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from . import config
from . import messages
from .handlers import handle_team_message

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

        logger.info(f"Received mention in {channel_id}: {user_message[:50]}...")

        await handle_team_message(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_message=user_message,
            bot_user_id=app._bot_user_id,
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
        user_message = event.get("text", "")
        thread_ts = event.get("thread_ts") or event["ts"]

        logger.info(f"Received message in {channel_type}: {user_message[:50]}...")

        await handle_team_message(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_message=user_message,
            bot_user_id=app._bot_user_id,
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

    # Start async Socket Mode handler
    handler = AsyncSocketModeHandler(app, config.SLACK_APP_TOKEN)

    print("Connecting to Slack via Socket Mode...")
    print("Bot is running! Press Ctrl+C to stop.")
    print("Waiting for messages...\n")

    await handler.start_async()


def main():
    """Entry point — runs the async bot."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
