#!/usr/bin/env python3
"""
Slack Poster - CLI tool to post messages to Slack from work sessions.

Usage:
    python -m anchovies.slack_integration.poster "Your message here"
    python -m anchovies.slack_integration.poster "Message" --thread THREAD_TS
    python -m anchovies.slack_integration.poster "Message" --member sofia

Or via the shell script:
    ~/paradise_brain/anchovies/scripts/post_to_slack.sh "Your message"
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Add parent to path for imports when run directly
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)


def get_slack_client():
    """Get a configured Slack WebClient."""
    from anchovies import config
    
    if not config.SLACK_BOT_TOKEN:
        raise ValueError("SLACK_BOT_TOKEN not configured")
    
    return WebClient(token=config.SLACK_BOT_TOKEN)


def get_default_channel():
    """Get the default Slack channel from config."""
    from anchovies import config
    return config.SLACK_CHANNEL_ID


def post_to_slack(
    message: str,
    channel: str = None,
    thread_ts: str = None,
    member: str = None,
) -> dict:
    """
    Post a message to Slack.

    Args:
        message: The message text to post
        channel: Channel ID (defaults to SLACK_CHANNEL_ID from config)
        thread_ts: Thread timestamp to reply to (optional)
        member: Team member name to prefix message with (optional)

    Returns:
        dict with 'ok', 'ts', and 'channel' keys
    """
    client = get_slack_client()
    channel = channel or get_default_channel()
    
    if not channel:
        raise ValueError("No channel specified and SLACK_CHANNEL_ID not configured")
    
    # Format message with member emoji + name if provided
    if member:
        try:
            from anchovies.persona_utils import format_persona_message
            formatted_message = format_persona_message(member, message)
        except Exception:
            formatted_message = f"{member.title()}: {message}"
    else:
        formatted_message = message
    
    try:
        response = client.chat_postMessage(
            channel=channel,
            text=formatted_message,
            thread_ts=thread_ts,
        )
        
        return {
            "ok": True,
            "ts": response["ts"],
            "channel": response["channel"],
        }
    
    except SlackApiError as e:
        logger.error(f"Slack API error: {e.response['error']}")
        return {
            "ok": False,
            "error": e.response["error"],
        }


def post_completion_message(
    member: str,
    summary: str,
    channel: str = None,
    thread_ts: str = None,
) -> dict:
    """
    Post a task completion message to Slack.

    Args:
        member: Team member who completed the task
        summary: Summary of what was accomplished
        channel: Channel ID (optional)
        thread_ts: Thread to reply to (optional)

    Returns:
        dict with posting result
    """
    message = f":white_check_mark: {summary}"
    return post_to_slack(
        message=message,
        channel=channel,
        thread_ts=thread_ts,
        member=member,
    )


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Post a message to Slack from work sessions"
    )
    parser.add_argument(
        "message",
        help="The message to post"
    )
    parser.add_argument(
        "--channel", "-c",
        help="Channel ID to post to (defaults to SLACK_CHANNEL_ID)"
    )
    parser.add_argument(
        "--thread", "-t",
        help="Thread timestamp to reply to"
    )
    parser.add_argument(
        "--member", "-m",
        help="Team member name to prefix the message with"
    )
    parser.add_argument(
        "--completion",
        action="store_true",
        help="Format as a completion message (adds checkmark)"
    )
    
    args = parser.parse_args()
    
    try:
        if args.completion and args.member:
            result = post_completion_message(
                member=args.member,
                summary=args.message,
                channel=args.channel,
                thread_ts=args.thread,
            )
        else:
            result = post_to_slack(
                message=args.message,
                channel=args.channel,
                thread_ts=args.thread,
                member=args.member,
            )
        
        if result["ok"]:
            print(f"Posted to Slack (ts: {result['ts']})")
            sys.exit(0)
        else:
            print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
            sys.exit(1)
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
