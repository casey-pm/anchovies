"""
Message routing logic for Team Forum Bot.

Parses incoming messages to identify which team member(s) are being addressed.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass
class RoutingResult:
    """Result of message routing."""
    members: list[str]  # List of targeted team member names
    cleaned_message: str  # Message with routing prefixes removed
    is_broadcast: bool  # True if message is for all members
    project: str | None = None  # Extracted project tag (e.g., "calculator")


def extract_project_tag(text: str) -> tuple[str | None, str]:
    """
    Extract a [project_name] tag from message text.

    Supports:
      - [calculator] anywhere in message -> project="calculator"
      - Case insensitive: [Calculator] -> "calculator"

    Returns:
        (project_name_or_None, cleaned_message_with_tag_removed)
    """
    match = re.search(r"\[([a-zA-Z0-9_-]+)\]", text)
    if match:
        project_name = match.group(1).lower()
        cleaned = text[:match.start()] + text[match.end():]
        return project_name, cleaned.strip()
    return None, text


def infer_project_from_paths(text: str, registry=None) -> str | None:
    """
    Try to infer a project from file paths mentioned in the message.

    Checks each registered project's working_dir against paths in the message.
    Returns the first matching project name, or None.

    Args:
        text: The message text to scan for paths
        registry: Optional ProjectRegistry override (for testing). If None,
                  uses the singleton.
    """
    try:
        if registry is None:
            from .project_registry import get_project_registry
            registry = get_project_registry()
        if registry.is_empty():
            return None

        # Find absolute-looking paths in the text
        # Match strings starting with / or ~/ and continuing until whitespace or punctuation
        path_pattern = re.compile(r"(?:~/|/)[\w./_-]+")
        paths_in_text = path_pattern.findall(text)

        for mentioned_path in paths_in_text:
            # Expand ~ for comparison
            expanded = mentioned_path.replace("~", str(Path.home()))
            for project in registry.list_projects():
                wd = str(project.working_dir)
                if expanded.startswith(wd) or wd.startswith(expanded.rstrip("/")):
                    return project.name
    except Exception:
        pass  # Don't crash routing on inference failure

    return None


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
    # Extract [project] tag first (before any other parsing)
    project, text = extract_project_tag(text)

    # If no explicit tag, try inferring from file paths in the message
    if project is None:
        project = infer_project_from_paths(text)

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
            is_broadcast=True,
            project=project,
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
        is_broadcast=False,
        project=project,
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
