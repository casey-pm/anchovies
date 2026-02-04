"""
Slack message builders for Team Forum Bot.

Uses Slack Block Kit for rich message formatting.
"""

from .context import MemberProfile


def build_thinking_message(member_name: str, avatar_emoji: str = ":hourglass:") -> list[dict]:
    """
    Build a "thinking" message shown while waiting for response.

    Args:
        member_name: The team member's name
        avatar_emoji: Emoji to show

    Returns:
        List of Slack blocks
    """
    return [
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"{avatar_emoji} *{member_name}* is thinking..."
                }
            ]
        }
    ]


def build_response_message(
    member_name: str,
    response: str,
    avatar_emoji: str = ":bust_in_silhouette:",
) -> list[dict]:
    """
    Build a response message from a team member.

    Args:
        member_name: The team member's name
        response: The response text
        avatar_emoji: Emoji to use as avatar

    Returns:
        List of Slack blocks
    """
    blocks = [
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"{avatar_emoji} *{member_name}*"
                }
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": response[:3000]  # Slack block text limit
            }
        }
    ]

    # Add continuation blocks if response is long
    if len(response) > 3000:
        remaining = response[3000:]
        while remaining:
            chunk = remaining[:3000]
            remaining = remaining[3000:]
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": chunk
                }
            })

    return blocks


def build_error_message(error: str) -> list[dict]:
    """
    Build an error message.

    Args:
        error: The error message

    Returns:
        List of Slack blocks
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning: *Error:* {error}"
            }
        }
    ]


def build_help_message() -> list[dict]:
    """
    Build the help message showing available commands.

    Returns:
        List of Slack blocks
    """
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "BI Team Forum",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Chat with AI team members! Address them by name and they'll respond in character."
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*How to use:*\n"
                        "- `@TeamForum @marcus what's the project status?`\n"
                        "- `@TeamForum hey sofia, can you help with dbt?`\n"
                        "- `@TeamForum raj, what do you think about the model?`"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Team Members:*\n"
                        ":bust_in_silhouette: Marcus (Boss) - BI Manager\n"
                        ":woman-technologist: Sofia (dbt Queen) - Analytics Engineer\n"
                        ":crystal_ball: Raj (The Prophet) - Data Scientist\n"
                        ":seedling: Leo (Padawan) - Junior Data Scientist\n"
                        ":bar_chart: Natalie (Nat) - Senior BI Analyst\n"
                        ":chart_with_upwards_trend: Mike (Dashboard Mike) - Reporting Analyst\n"
                        ":mag: Anna (The Auditor) - Data Quality Analyst\n"
                        ":1234: Tom (Numbers) - Data Analyst\n"
                        ":wrench: James (JO) - Data Engineer\n"
                        ":star2: Priya (P) - Junior BI Analyst\n"
                        ":gear: Elena (Pipes) - Senior Data Engineer\n"
                        ":link: Julia (Glue) - Analytics Engineer\n"
                        ":memo: Olivia (Scribe) - Documentation Manager\n"
                        ":art: Nina (Pixel) - BI Report Designer\n"
                        ":building_construction: Victor (Blueprint) - Data Architect\n"
                        ":zap: Kai (The Optimizer) - Code Quality Engineer"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": ":bulb: Tip: You can also use nicknames like 'Boss', 'dbt Queen', etc."
                }
            ]
        }
    ]


def build_unknown_member_message(attempted_name: str) -> list[dict]:
    """
    Build a message for when an unknown team member is addressed.

    Args:
        attempted_name: The name that was tried

    Returns:
        List of Slack blocks
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":thinking_face: I don't recognize *{attempted_name}* as a team member.\n\n"
                        "Try addressing one of the team by name, like `@marcus` or `hey sofia`.\n"
                        "Use `@TeamForum help` to see all available team members."
            }
        }
    ]
