"""
Reflection system for Anchovies.

Captures learnings after each work session:
  - Auto-reflect: triggered on session completion (lightweight, haiku)
  - Manual /reflect: triggered by command (deeper analysis)

Reflections are appended to existing memory files (memory/<persona>.md)
and loaded into future session prompts via context.py.

Inspired by BlackTeam's /reflect command that captures skills
demonstrated, insights, and corrections per persona per session.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger(__name__)


def _build_auto_reflect_prompt(
    member: str,
    task_description: str,
    project: Optional[str] = None,
) -> str:
    """Build the lightweight auto-reflection prompt."""
    project_tag = f" on [{project}]" if project else ""
    return f"""You just completed a work session as {member.title()}{project_tag}.

Task: {task_description}

Reflect briefly (3-5 bullet points):
- What you accomplished
- What you learned (new technique, gotcha, useful pattern)
- What was harder than expected
- What you'd do differently next time

Be specific and concise. Each bullet should be one sentence."""


def _build_manual_reflect_prompt(
    member: str,
    project: Optional[str] = None,
    recent_memory: str = "",
) -> str:
    """Build the deeper manual reflection prompt."""
    project_tag = f" on [{project}]" if project else ""
    memory_section = ""
    if recent_memory:
        memory_section = f"\n\nYour recent memory/learnings:\n{recent_memory[:1000]}"

    return f"""You are {member.title()}, reflecting deeply on your recent work{project_tag}.{memory_section}

Think about:
1. **Skills**: What skills did you demonstrate or develop? Any new capabilities?
2. **Patterns**: Did you notice any recurring problems or useful patterns?
3. **Collaboration**: How did you interact with the team? Could coordination be better?
4. **Quality**: Were your outputs good enough? What's your quality bar?
5. **Process**: What worked well in how you approached tasks? What didn't?

Provide a structured reflection with specific examples. This will be saved
to your memory for future sessions."""


def format_reflection_entry(
    member: str,
    task_description: str,
    reflection_text: str,
    project: Optional[str] = None,
    deep: bool = False,
) -> str:
    """Format a reflection entry for appending to a memory file."""
    date = datetime.now().strftime("%Y-%m-%d")
    tag = "[Deep Reflection]" if deep else ""
    project_tag = f" [{project}]" if project else ""

    return f"""
## Reflection: {date} — {task_description[:60]}{project_tag} {tag}
{reflection_text.strip()}
"""


async def auto_reflect(
    member: str,
    task_description: str,
    project: Optional[str] = None,
) -> Optional[str]:
    """
    Run a lightweight auto-reflection after session completion.

    Uses haiku model (cheap). Appends result to the persona's memory file.
    Returns the reflection text, or None on failure.
    """
    prompt = _build_auto_reflect_prompt(member, task_description, project)

    try:
        from .cli_runner import run_claude_cli
        reflection = await run_claude_cli(prompt, model=config.CHAT_MODEL)

        # Append to memory file
        entry = format_reflection_entry(member, task_description, reflection, project)
        _append_to_memory(member, entry)

        # Audit log
        try:
            from .storage import get_storage
            get_storage().log_event(
                "reflection",
                member=member,
                details={
                    "type": "auto",
                    "task": task_description[:100],
                    "project": project,
                },
            )
        except Exception:
            pass

        logger.info(f"Auto-reflection saved for {member}")
        return reflection

    except Exception as e:
        logger.error(f"Auto-reflection failed for {member}: {e}")
        return None


async def manual_reflect(
    member: str,
    project: Optional[str] = None,
) -> Optional[str]:
    """
    Run a deeper manual reflection (triggered by /reflect command).

    Uses haiku model. Reads current memory for context.
    Returns the reflection text, or None on failure.
    """
    # Load current memory for context
    memory_path = config.BOT_DIR / "memory" / f"memory_{member}.md"
    recent_memory = ""
    if memory_path.exists():
        content = memory_path.read_text()
        # Take the last ~1000 chars of memory for context
        recent_memory = content[-1000:] if len(content) > 1000 else content

    prompt = _build_manual_reflect_prompt(member, project, recent_memory)

    try:
        from .cli_runner import run_claude_cli
        reflection = await run_claude_cli(prompt, model=config.CHAT_MODEL)

        entry = format_reflection_entry(
            member, "deep reflection", reflection, project, deep=True
        )
        _append_to_memory(member, entry)

        try:
            from .storage import get_storage
            get_storage().log_event(
                "reflection",
                member=member,
                details={"type": "manual", "project": project},
            )
        except Exception:
            pass

        logger.info(f"Manual reflection saved for {member}")
        return reflection

    except Exception as e:
        logger.error(f"Manual reflection failed for {member}: {e}")
        return None


def _append_to_memory(member: str, entry: str) -> None:
    """Append a reflection entry to the persona's memory file."""
    memory_dir = config.BOT_DIR / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_path = memory_dir / f"memory_{member}.md"

    try:
        with open(memory_path, "a", encoding="utf-8") as f:
            f.write(entry)
        logger.debug(f"Appended reflection to {memory_path}")
    except Exception as e:
        logger.error(f"Failed to append to memory for {member}: {e}")
