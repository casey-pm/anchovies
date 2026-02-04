"""
Prompt Builder - Builds task prompts for work sessions.

Responsibilities:
1. Detect if a message is a work request
2. Extract task details (target persona, files, description)
3. Build structured task prompts for work sessions
"""

import re
from pathlib import Path

from .. import config
from ..context import load_member_context
from .skill_mapper import get_skills_for_task, detect_task_type

# Patterns that indicate a work request (file edits, code changes, etc.)
WORK_PATTERNS = [
    r"\b(fix|debug|resolve)\s+(the\s+)?(bug|error|issue|problem)",
    r"\b(edit|modify|change|update)\s+.{0,20}\b(file|code|function|class|module|status)\b",  # More flexible
    r"\b(create|add|write|implement)\s+(a\s+)?(new\s+)?(file|function|class|module|feature|test)",
    r"\b(delete|remove)\s+(the\s+)?(file|function|code|line)",
    r"\b(refactor|restructure|reorganize)",
    r"\bcommit\b",
    r"\b(review|check)\s+(the\s+)?(code|pr|pull\s*request|changes)",
    r"\b(run|execute)\s+(the\s+)?(tests?|script)",
    r"\b(install|setup|configure)",
    r"\bupdate\s+.{0,10}\bstatus\b",  # "update your status", "update the status", etc.
    r"\.py\b",  # Mentions a .py file
    r"\.js\b",  # Mentions a .js file
    r"\.ts\b",  # Mentions a .ts file
    r"\.css\b",  # Mentions a .css file
    r"\.md\b",  # Mentions a .md file
    r"\.yaml\b",  # Mentions a .yaml file
    r"\.json\b",  # Mentions a .json file
]

# Compile patterns for efficiency
WORK_REGEX = [re.compile(p, re.IGNORECASE) for p in WORK_PATTERNS]


def detect_work_request(message: str) -> dict:
    """
    Detect if a message is a work request.

    Args:
        message: The message text

    Returns:
        dict with keys:
            - is_work_request: bool
            - target_persona: str or None
            - task_description: str
            - files: list of mentioned files
    """
    # Check for work patterns
    is_work = any(pattern.search(message) for pattern in WORK_REGEX)

    # Extract target persona (look for @mentions or name references)
    target_persona = extract_target_persona(message)

    # Extract mentioned files
    files = extract_files(message)

    # If files are mentioned, it's likely a work request
    if files and not is_work:
        is_work = True

    # Extract task description
    task_description = extract_task_description(message)

    return {
        "is_work_request": is_work,
        "target_persona": target_persona or config.DEFAULT_MEMBER,
        "task_description": task_description,
        "files": files,
    }


def extract_target_persona(message: str) -> str | None:
    """Extract the target persona from a message."""
    message_lower = message.lower()

    # Check for @mentions first
    mention_pattern = r"@(\w+)"
    mentions = re.findall(mention_pattern, message_lower)

    for mention in mentions:
        # Check if it's a team member
        if mention in config.TEAM_MEMBERS:
            return mention
        # Check aliases
        if mention in config.MEMBER_ALIASES:
            return config.MEMBER_ALIASES[mention]

    # Check for name references (e.g., "Sofia, can you..." or "hey sofia")
    for member in config.TEAM_MEMBERS:
        # Look for name at word boundary
        if re.search(rf"\b{member}\b", message_lower):
            return member

    # Check aliases
    for alias, member in config.MEMBER_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", message_lower):
            return member

    return None


def extract_files(message: str) -> list[str]:
    """Extract file paths/names mentioned in the message."""
    files = []

    # Pattern for file paths (with extensions)
    file_pattern = r"[\w\-./]+\.(py|js|ts|css|html|md|yaml|yml|json|txt|sh|sql)"
    matches = re.findall(file_pattern, message, re.IGNORECASE)

    # Reconstruct full matches
    for match in re.finditer(file_pattern, message, re.IGNORECASE):
        files.append(match.group(0))

    return list(set(files))  # Remove duplicates


def extract_task_description(message: str) -> str:
    """Extract a clean task description from the message."""
    # Remove @mentions
    description = re.sub(r"@\w+\s*", "", message)

    # Remove common prefixes
    prefixes_to_remove = [
        r"^(hey|hi|hello)\s+\w+[,\s]*",
        r"^(can you|could you|please|pls)\s+",
    ]

    for prefix in prefixes_to_remove:
        description = re.sub(prefix, "", description, flags=re.IGNORECASE)

    return description.strip()


def build_task_prompt(
    persona: str,
    task_description: str,
    files: list[str] | None = None,
    context: str = "",
    thread_history: str = "",
    thread_ts: str = "",
) -> str:
    """
    Build a complete task prompt for a work session.

    Args:
        persona: Target team member (lowercase)
        task_description: What needs to be done
        files: List of relevant file paths
        context: Additional context
        thread_history: Slack thread history if available
        thread_ts: Slack thread timestamp for replying to the task thread

    Returns:
        Formatted task prompt string
    """
    # Load persona context
    member_context = load_member_context(persona)
    profile = member_context.profile

    # Detect task type and get relevant skills
    task_type = detect_task_type(task_description)
    skills = get_skills_for_task(task_type, task_description)

    # Build the prompt
    parts = [
        f"# Work Session: {profile.name}",
        "",
        "## Your Identity",
        f'You are {profile.name} ("{profile.nickname}"), {profile.role} on the Domain 360 project team.',
    ]

    # Add brief personality
    if profile.personality:
        style = profile.personality.get("communication_style", "professional")
        parts.append(f"Communication style: {style}")

    # Add job summary if available
    if profile.job_summary:
        parts.append("")
        parts.append("## Your Skills")
        parts.append(profile.job_summary.strip())

    # Task section
    parts.extend([
        "",
        "## Task",
        task_description,
    ])

    # Files section
    if files:
        parts.extend([
            "",
            "## Relevant Files",
        ])
        for f in files:
            parts.append(f"- {f}")

    # Context section
    if context and context != task_description:
        parts.extend([
            "",
            "## Context",
            context[:500],  # Limit context length
        ])

    # Thread history if available
    if thread_history:
        parts.extend([
            "",
            "## Thread History",
            thread_history[:1000],  # Limit history length
        ])

    # Skills section
    if skills:
        parts.extend([
            "",
            "## Skills & Commands Available",
        ])
        for skill in skills:
            parts.append(f"- {skill}")

    # Completion instructions - use paths from config
    status_path = config.CONTEXT_BASE / "status" / f"status_{persona}.md"

    parts.extend([
        "",
        "## When Complete",
        "",
        "1. **Update your status file:**",
        f"   Edit `{status_path}` with a summary of what you did.",
        "",
        "2. **Post summary to Slack:**",
        "   ```bash",
        f'   ~/paradise_brain/anchovies/scripts/slack "Brief summary of what you accomplished" --member {persona}' + (f' --thread {thread_ts}' if thread_ts else ''),
        "   ```",
        "",
        "3. **Signal completion:**",
        "   Tell the user you're done so they can close this session with `Ctrl+b &` or switch back to chat with `Ctrl+b 0`.",
        "",
        "## Important",
        "- Focus on the task at hand",
        "- Ask clarifying questions if needed",
        "- Test your changes before marking complete",
        "- Keep responses concise - you're in a work session, not a chat",
    ])

    return "\n".join(parts)


# Test function
if __name__ == "__main__":
    # Test detection
    test_messages = [
        "@sofia fix the bug in data_processor.py",
        "Hey Leo, can you write tests for the new module?",
        "What's the status of the project?",
        "Sofia, update the CSS in style.css line 142",
        "Can someone help with the report?",
        "Marcus, who's working on the API integration?",
    ]

    print("Testing work request detection:")
    print("=" * 60)

    for msg in test_messages:
        result = detect_work_request(msg)
        print(f"\nMessage: {msg}")
        print(f"  Is work request: {result['is_work_request']}")
        print(f"  Target: {result['target_persona']}")
        print(f"  Files: {result['files']}")
        print(f"  Task: {result['task_description']}")
