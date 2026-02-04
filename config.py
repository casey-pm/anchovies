"""
Configuration for Anchovies - Hybrid Chat System.

Architecture:
- Chat Hub: Marcus as coordinator (single persistent Claude session)
- Work Sessions: On-demand persona tabs via tmux
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# Slack credentials
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

# Paths
BOT_DIR = Path(__file__).parent  # anchovies/
PROJECT_ROOT = BOT_DIR.parent  # paradise_brain/
CONTEXT_BASE = Path(os.getenv(
    "CONTEXT_BASE",
    str(PROJECT_ROOT / "Domain_360_report_agent_Papaya" / "Domain_360_report_enhancements_1")
))
# Profiles are now in the same folder as the bot
PROFILES_DIR = Path(os.getenv(
    "PROFILES_DIR",
    str(BOT_DIR / "profiles")
))
JOB_DESCRIPTIONS_DIR = Path(os.getenv(
    "JOB_DESCRIPTIONS_DIR",
    str(PROJECT_ROOT / "my libraries" / "job_descriptions")
))

# Claude CLI
CLAUDE_CLI_PATH = os.getenv("CLAUDE_CLI_PATH", "claude")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "sonnet")

# tmux Configuration
TMUX_SESSION_NAME = os.getenv("TMUX_SESSION_NAME", "anchovies")
CHAT_PANE_WIDTH = int(os.getenv("CHAT_PANE_WIDTH", "35"))  # Percentage

# Session Management
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "10"))
CHAT_HUB_PERSONA = os.getenv("CHAT_HUB_PERSONA", "marcus")

# Defaults
DEFAULT_MEMBER = os.getenv("DEFAULT_MEMBER", "marcus")

# Team members registry
TEAM_MEMBERS = [
    "marcus", "sofia", "raj", "leo", "natalie", "mike",
    "anna", "tom", "james", "priya", "elena", "julia",
    "olivia", "nina", "victor", "kai"
]

# Aliases for team members (nicknames and variations)
MEMBER_ALIASES = {
    "boss": "marcus",
    "dbt queen": "sofia",
    "the prophet": "raj",
    "padawan": "leo",
    "nat": "natalie",
    "dashboard mike": "mike",
    "the auditor": "anna",
    "numbers": "tom",
    "jo": "james",
    "p": "priya",
    "pipes": "elena",
    "glue": "julia",
    "scribe": "olivia",
    "pixel": "nina",
    "blueprint": "victor",
    "the optimizer": "kai",
}


def validate_config() -> tuple[bool, list[str]]:
    """
    Validate that all required configuration is present.

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    if not SLACK_BOT_TOKEN:
        errors.append("SLACK_BOT_TOKEN is not set")
    elif not SLACK_BOT_TOKEN.startswith("xoxb-"):
        errors.append("SLACK_BOT_TOKEN should start with 'xoxb-'")

    if not SLACK_APP_TOKEN:
        errors.append("SLACK_APP_TOKEN is not set (required for Socket Mode)")
    elif not SLACK_APP_TOKEN.startswith("xapp-"):
        errors.append("SLACK_APP_TOKEN should start with 'xapp-'")

    if not SLACK_SIGNING_SECRET:
        errors.append("SLACK_SIGNING_SECRET is not set")

    if not CONTEXT_BASE.exists():
        errors.append(f"CONTEXT_BASE directory does not exist: {CONTEXT_BASE}")

    if not PROFILES_DIR.exists():
        errors.append(f"PROFILES_DIR directory does not exist: {PROFILES_DIR}")

    return len(errors) == 0, errors


def get_member_name(name_or_alias: str) -> str | None:
    """
    Convert a name or alias to the canonical team member name.

    Args:
        name_or_alias: Team member name or nickname

    Returns:
        Canonical name or None if not found
    """
    name_lower = name_or_alias.lower().strip()

    # Direct match
    if name_lower in TEAM_MEMBERS:
        return name_lower

    # Alias match
    if name_lower in MEMBER_ALIASES:
        return MEMBER_ALIASES[name_lower]

    return None
