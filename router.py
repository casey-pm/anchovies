"""
Message routing logic for Team Forum Bot.

Parses incoming messages to identify which team member(s) are being addressed.
"""

import re
from dataclasses import dataclass

from . import config


@dataclass
class RoutingResult:
    """Result of message routing."""
    members: list[str]  # List of targeted team member names
    cleaned_message: str  # Message with routing prefixes removed
    is_broadcast: bool  # True if message is for all members


def route_message(text: str) -> RoutingResult:
    """
    Parse a message to identify which team member(s) are being addressed.

    Supported patterns:
    - @marcus, @sofia (direct mentions)
    - Marcus:, Sofia: (name prefix)
    - hey marcus, ask sofia (conversational)
    - @all, @team (broadcast to all)

    Args:
        text: The message text

    Returns:
        RoutingResult with targeted members and cleaned message
    """
    members = []
    cleaned = text
    is_broadcast = False

    # Check for broadcast patterns
    # Note: Only explicit @mentions trigger broadcast, not casual "team" references
    broadcast_patterns = [
        r"@all\b",
        r"@team\b",
        r"@everyone\b",
    ]
    for pattern in broadcast_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            is_broadcast = True
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
            break

    if is_broadcast:
        members = config.TEAM_MEMBERS.copy()
        return RoutingResult(
            members=members,
            cleaned_message=cleaned.strip(),
            is_broadcast=True
        )

    # Build pattern for all member names and aliases
    all_names = config.TEAM_MEMBERS + list(config.MEMBER_ALIASES.keys())
    names_pattern = "|".join(re.escape(name) for name in all_names)

    # Pattern 1: @mentions (e.g., @marcus, @sofia)
    mention_pattern = rf"@({names_pattern})\b"
    matches = re.findall(mention_pattern, text, re.IGNORECASE)
    for match in matches:
        member = config.get_member_name(match)
        if member and member not in members:
            members.append(member)
    cleaned = re.sub(mention_pattern, "", cleaned, flags=re.IGNORECASE)

    # Pattern 2: Name prefix (e.g., "Marcus:", "Sofia,")
    prefix_pattern = rf"^({names_pattern})[,:]?\s+"
    prefix_match = re.match(prefix_pattern, text, re.IGNORECASE)
    if prefix_match:
        member = config.get_member_name(prefix_match.group(1))
        if member and member not in members:
            members.append(member)
        cleaned = re.sub(prefix_pattern, "", cleaned, flags=re.IGNORECASE)

    # Pattern 3: Conversational (e.g., "hey marcus", "ask sofia", "tell raj")
    conversational_pattern = rf"(?:hey|hi|ask|tell|yo)\s+({names_pattern})\b"
    conv_matches = re.findall(conversational_pattern, text, re.IGNORECASE)
    for match in conv_matches:
        member = config.get_member_name(match)
        if member and member not in members:
            members.append(member)
    cleaned = re.sub(conversational_pattern, "", cleaned, flags=re.IGNORECASE)

    # Default to DEFAULT_MEMBER if no one identified
    if not members:
        members = [config.DEFAULT_MEMBER]

    return RoutingResult(
        members=members,
        cleaned_message=cleaned.strip(),
        is_broadcast=False
    )


def extract_bot_mention(text: str, bot_user_id: str) -> str:
    """
    Remove the bot's own @mention from the message.

    Args:
        text: The message text
        bot_user_id: The bot's Slack user ID

    Returns:
        Message with bot mention removed
    """
    # Slack formats mentions as <@U1234567>
    pattern = rf"<@{bot_user_id}>\s*"
    return re.sub(pattern, "", text).strip()
