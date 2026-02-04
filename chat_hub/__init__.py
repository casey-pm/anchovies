"""Chat Hub module - Marcus as coordinator for quick chat and task prompt building."""

from .hub import ChatHub, create_chat_hub
from .prompt_builder import build_task_prompt, detect_work_request
from .skill_mapper import detect_task_type, get_skills_for_task

__all__ = [
    "ChatHub",
    "create_chat_hub",
    "build_task_prompt",
    "detect_work_request",
    "detect_task_type",
    "get_skills_for_task",
]
