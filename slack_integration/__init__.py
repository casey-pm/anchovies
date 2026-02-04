"""Slack Integration module - tools for posting to Slack from work sessions."""

from .poster import post_to_slack, post_completion_message

__all__ = [
    "post_to_slack",
    "post_completion_message",
]
