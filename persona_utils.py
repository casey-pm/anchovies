"""
Quick persona utility lookups used across the system.

Avoids loading full profiles when you just need a name or emoji.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from . import config

# Cache of persona emojis (loaded once on first access)
_emoji_cache: dict[str, str] | None = None


def _load_emojis() -> dict[str, str]:
    """Load emoji mapping from all profile YAMLs."""
    global _emoji_cache
    if _emoji_cache is not None:
        return _emoji_cache

    _emoji_cache = {}
    for profile_file in config.PROFILES_DIR.glob("profile_*.yaml"):
        try:
            data = yaml.safe_load(profile_file.read_text()) or {}
            name = profile_file.stem.replace("profile_", "")
            _emoji_cache[name] = data.get("avatar_emoji", ":bust_in_silhouette:")
        except Exception:
            pass

    return _emoji_cache


def get_emoji(member: str) -> str:
    """Get the emoji for a persona. Returns default if not found."""
    emojis = _load_emojis()
    return emojis.get(member.lower(), ":bust_in_silhouette:")


def format_persona_message(member: str, message: str) -> str:
    """Format a Slack message with persona emoji and name. No markdown."""
    emoji = get_emoji(member)
    return f"{emoji} {member.title()}: {message}"
