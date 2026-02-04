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

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from . import config
from . import messages
from .handlers import handle_team_message

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> App:
    """Create and configure the Slack Bolt app."""
    # Validate configuration
    is_valid, errors = config.validate_config()
    if not is_valid:
        logger.error("Configuration errors:")
        for error in errors:
            logger.error(f"  - {error}")
        logger.error("\nPlease check your .env file and ensure all required variables are set.")
        sys.exit(1)

    # Create the app
    app = App(
        token=config.SLACK_BOT_TOKEN,
        signing_secret=config.SLACK_SIGNING_SECRET,
    )

    # Store bot user ID for mention detection
    bot_info = app.client.auth_test()
    bot_user_id = bot_info["user_id"]
    logger.info(f"Bot user ID: {bot_user_id}")

    # Register app mention handler
    @app.event("app_mention")
    def handle_app_mention(event, say, client):
        """Handle when the bot is @mentioned in a channel."""
        channel_id = event["channel"]
        user_message = event.get("text", "")
        thread_ts = event.get("thread_ts") or event["ts"]

        logger.info(f"Received mention in {channel_id}: {user_message[:50]}...")

        # Run async handler
        asyncio.run(handle_team_message(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_message=user_message,
            bot_user_id=bot_user_id,
        ))

    # Handle messages (DMs, private channels, and group DMs)
    @app.event("message")
    def handle_message(event, say, client):
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

        # Run async handler
        asyncio.run(handle_team_message(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_message=user_message,
            bot_user_id=bot_user_id,
        ))

    # Handle app home opened
    @app.event("app_home_opened")
    def handle_app_home_opened(client, event):
        """Update the App Home tab when a user opens it."""
        user_id = event["user"]

        try:
            client.views_publish(
                user_id=user_id,
                view={
                    "type": "home",
                    "blocks": messages.build_help_message()
                }
            )
        except Exception as e:
            logger.error(f"Failed to publish home tab: {e}")

    return app


def main():
    """Run the Slack bot in Socket Mode."""
    print("\n" + "=" * 60)
    print("STARTING BI TEAM FORUM BOT")
    print("=" * 60)
    print(f"Bot Token: {config.SLACK_BOT_TOKEN[:20]}..." if config.SLACK_BOT_TOKEN else "Bot Token: MISSING!")
    print(f"App Token: {config.SLACK_APP_TOKEN[:20]}..." if config.SLACK_APP_TOKEN else "App Token: MISSING!")
    print(f"Context Base: {config.CONTEXT_BASE}")
    print(f"Profiles Dir: {config.PROFILES_DIR}")
    print("=" * 60 + "\n")

    # Create the app
    app = create_app()

    # Start Socket Mode handler
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)

    print("Connecting to Slack via Socket Mode...")
    print("Bot is running! Press Ctrl+C to stop.")
    print("Waiting for messages...\n")

    handler.start()


if __name__ == "__main__":
    main()
