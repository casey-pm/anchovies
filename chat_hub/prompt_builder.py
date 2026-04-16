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
from ..git_safety import branch_name
from .skill_mapper import get_skills_for_task, detect_task_type

# Patterns that indicate a work request (file edits, code changes, etc.)
WORK_PATTERNS = [
    r"\b(fix|debug|resolve)\s+(the\s+)?(bug|error|issue|problem)",
    r"\b(edit|modify|change|update)\s+.{0,20}\b(file|code|function|class|module|status)\b",
    r"\b(create|add|write|implement)\s+.{0,20}\b(file|function|class|module|feature|test)",  # More flexible
    r"\b(delete|remove)\s+(the\s+)?(file|function|code|line)",
    r"\b(refactor|restructure|reorganize)",
    r"\bcommit\b",
    r"\b(review|check)\s+(the\s+)?(code|pr|pull\s*request|changes)",
    r"\b(run|execute)\s+(the\s+)?(tests?|script)",
    r"\b(install|setup|configure)",
    r"\bupdate\s+.{0,10}\bstatus\b",
    # File operations
    r"\bread\s+.{0,20}\b(file|files|folder|directory)",  # "read the files in folder"
    r"\bsummar(y|ise|ize)\b",  # Any summarization task
    r"\bwrite\s+.{0,15}\b(to|in|into)\b",  # "write to file", "write in a file"
    r"\bsave\s+.{0,10}\b(to|as|in)\b",  # "save to file", "save as"
    r"\bgenerate\s+.{0,10}\b(file|report|summary)",  # "generate a file"
    # File extensions
    r"\.py\b",
    r"\.js\b",
    r"\.ts\b",
    r"\.css\b",
    r"\.md\b",
    r"\.yaml\b",
    r"\.json\b",
    r"\.txt\b",
    r"\.sql\b",
]

# Compile patterns for efficiency
WORK_REGEX = [re.compile(p, re.IGNORECASE) for p in WORK_PATTERNS]

# Negative patterns that suggest the message is a QUESTION or DISCUSSION,
# not a work request. These suppress false positives like "how does the
# review process work?" triggering on "review".
NEGATIVE_PATTERNS = [
    # Questions
    r"^\s*(how|what|why|when|where|who|which)\s",
    r"\?$",
    r"\bcan you (explain|tell|describe|help me understand)\b",
    r"\bdo you (know|think|remember)\b",
    # Past tense / already done
    r"\bi\s+(already|just)\s+\w+ed\b",
    r"\b(has|have|had)\s+been\s+\w+ed\b",
    r"\b(we|i)\s+fixed\b",
    r"\b(we|i)\s+already\b",
    # Hypotheticals
    r"\bwhat if\b",
    r"\bshould we\b",
    r"\bwould it\b",
    r"\bcould we\b",
    # Status inquiries
    r"\bstatus\s+(of|on|update)\b",
    r"\bany (news|updates?|progress)\b",
]
NEGATIVE_REGEX = [re.compile(p, re.IGNORECASE) for p in NEGATIVE_PATTERNS]

# Minimum confidence required to classify as work request without asking
MIN_WORK_CONFIDENCE = 0.5


def detect_work_request(message: str) -> dict:
    """
    Detect if a message is a work request.

    Uses a confidence-scored approach:
      - Positive patterns (action keywords, file references) increase score
      - Negative patterns (questions, past tense) decrease score
      - Confidence >= MIN_WORK_CONFIDENCE -> is_work_request=True
      - Lower confidence with some positive signal -> needs_clarification=True

    Args:
        message: The message text

    Returns:
        dict with keys:
            - is_work_request: bool (confident work request)
            - needs_clarification: bool (ambiguous, should ask)
            - confidence: float (0.0-1.0)
            - target_persona: str
            - task_description: str
            - files: list of mentioned files
    """
    # Count positive matches
    positive_hits = sum(1 for pattern in WORK_REGEX if pattern.search(message))

    # Count negative matches
    negative_hits = sum(1 for pattern in NEGATIVE_REGEX if pattern.search(message))

    # Extract mentioned files (a strong positive signal)
    files = extract_files(message)

    # Compute confidence
    # Base: 0.5 per positive hit (so 1 clear action keyword = at threshold).
    # Files give a small boost. Negatives subtract.
    positive_score = min(positive_hits * 0.5, 1.0)
    if files and positive_score < 1.0:
        positive_score = min(positive_score + 0.3, 1.0)
    negative_penalty = min(negative_hits * 0.4, 1.0)
    confidence = max(positive_score - negative_penalty, 0.0)

    # Classification
    is_work = confidence >= MIN_WORK_CONFIDENCE
    # Ambiguous: some positive signal but below threshold
    needs_clarification = (
        not is_work
        and positive_hits > 0
        and confidence > 0.15
    )

    # Extract target persona and task description
    target_persona = extract_target_persona(message)
    task_description = extract_task_description(message)

    return {
        "is_work_request": is_work,
        "needs_clarification": needs_clarification,
        "confidence": confidence,
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
    project: str | None = None,
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
        project: Optional project slug for multi-project support

    Returns:
        Formatted task prompt string
    """
    # Load persona context (project-aware: loads project-specific status)
    member_context = load_member_context(persona, project=project)
    profile = member_context.profile

    # Resolve project display name
    project_display = config.PROJECT_NAME
    project_context_base = config.CONTEXT_BASE
    if project:
        try:
            from ..project_registry import get_project_registry
            proj = get_project_registry().get(project)
            if proj:
                project_display = proj.display_name
                project_context_base = proj.context_base
        except Exception:
            pass

    # Detect task type and get relevant skills
    task_type = detect_task_type(task_description)
    skills = get_skills_for_task(task_type, task_description)

    # Build the prompt
    parts = [
        f"# Work Session: {profile.name}",
        "",
        "## Your Identity",
        f'You are {profile.name} ("{profile.nickname}"), {profile.role} on the {project_display} team.',
    ]

    # Add personality
    if profile.personality:
        style = profile.personality.get("communication_style", "professional")
        formality = profile.personality.get("formality", "professional-casual")
        verbosity = profile.personality.get("verbosity", "moderate")
        parts.append(f"Communication style: {style}, {formality}, {verbosity}")

    # Add traits if available
    if profile.traits:
        parts.append("")
        parts.append("## Your Traits")
        for trait in profile.traits:
            parts.append(f"- {trait}")

    # Add job summary if available
    if profile.job_summary:
        parts.append("")
        parts.append("## Your Role & Key Skills")
        parts.append(profile.job_summary.strip())

    # Add expertise (persona-specific technical skills)
    if profile.expertise:
        parts.append("")
        parts.append("## Your Expertise")
        for skill in profile.expertise:
            parts.append(f"- {skill}")

    # Add memory (lessons learned) if available
    if member_context.memory_content:
        parts.append("")
        parts.append("## Your Memory (Lessons Learned)")
        memory = member_context.memory_content[:1500]
        if len(member_context.memory_content) > 1500:
            memory += "\n... (truncated)"
        parts.append(memory)

    # Add team roster for managers
    if profile.team_roster:
        parts.append("")
        parts.append("## Your Team")
        for pillar, members in profile.team_roster.items():
            pillar_name = pillar.replace("_", " ").title()
            parts.append(f"\n### {pillar_name}")
            for member in members:
                name = member.get("name", "")
                nickname = member.get("nickname", "")
                role = member.get("role", "")
                parts.append(f"- **{name}** ({nickname}) - {role}")

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
    status_path = project_context_base / "status" / f"status_{persona}.md"

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
        "## MANDATORY SAFETY RULES",
        "",
        "### Protected Files (NEVER touch)",
        "The following files/patterns must NEVER be edited, deleted, or overwritten:",
    ])
    for pattern in config.PROTECTED_FILES:
        parts.append(f"- `{pattern}`")
    parts.extend([
        "",
        "You MAY read these files to understand configuration, but must NEVER modify them.",
        "If a task seems to require modifying a protected file, STOP and ask Casey in Slack.",
        "",
        "### Project Directory Rule (MANDATORY)",
        "All projects and work MUST be within `/home/casey/paradise_brain/`.",
        "Do NOT create files, directories, or repositories outside of this path.",
        "Do NOT cd to or operate in any directory outside `/home/casey/paradise_brain/`.",
        "",
        "### Destructive Operations (REQUIRE APPROVAL)",
        "Before performing ANY of these operations, you MUST post a message to Slack",
        "describing what you want to do and why, then WAIT for Casey's explicit approval:",
        "- Deleting files",
        "- Dropping database tables",
        "- Overwriting data files",
        "- Running migration scripts",
        "- Force-push, reset --hard, or any other destructive git operation",
        "- Any operation that cannot be easily undone",
        "",
        "Posting format for approval requests:",
        "```bash",
        f'~/paradise_brain/anchovies/scripts/slack "APPROVAL NEEDED: <what and why>" --member {persona}' + (f' --thread {thread_ts}' if thread_ts else ''),
        "```",
        "Wait for Casey to respond with \"approved\" or \"go ahead\" before proceeding.",
        "",
        "### Shell Command Rules",
        "- ALLOWED: git, pytest, python, dbt, ls, cat, head, tail, grep, find, echo, cd, mkdir",
        "- BLOCKED: rm -rf, sudo, shutdown, curl (external URLs), wget",
        "- REQUIRES APPROVAL: pip install, npm install (propose in Slack first)",
        "- If unsure, ASK in Slack before running",
        "",
        "### Git Rules (MANDATORY)",
        f"- You are on branch: `{branch_name(persona, task_description)}`",
        "- NEVER push to `main` or `master`",
        "- NEVER merge to `main` or `master` (Casey merges via PR)",
        "- NEVER force-push (`git push --force`, `-f`, `+branch`)",
        "- NEVER `git reset --hard` on shared branches",
        "- NEVER run `git filter-branch` or rewrite shared history",
        "- Commit only to your feature branch",
        f"- Use commit footer: `Co-Authored-By: {profile.name} (Anchovies) <anchovies@local>`",
        "- When done, a PR is auto-created from your branch for Casey to review",
        "- Casey enforces rebase on main before merging",
        "",
        "## Important",
        "- Focus on the task at hand",
        "- Ask clarifying questions if needed",
        "- Test your changes before marking complete",
        "- Keep responses concise - you're in a work session, not a chat",
        "- When referring to team members, use their name (e.g., 'Sofia') - do NOT use @ mentions (e.g., '@sofia')",
        "",
        "## Delegating to Other Team Members",
        "To spawn another persona's work session with a task, you MUST provide a prompt file:",
        "```bash",
        "~/paradise_brain/anchovies/scripts/spawn_persona.sh <name> <prompt_file>",
        "```",
        "",
        "**Steps to delegate:**",
        "1. Create a prompt file for the team member (e.g., `~/paradise_brain/anchovies/tmp/prompt_<name>.txt`)",
        "2. Write their task description and any relevant context to that file",
        "3. Spawn them with: `~/paradise_brain/anchovies/scripts/spawn_persona.sh <name> ~/paradise_brain/anchovies/tmp/prompt_<name>.txt`",
        "",
        "**Prompt file requirements:**",
        "The prompt file should be a COMPLETE task prompt, not just a brief instruction.",
        "Use the same format as your own prompt - include identity, skills, task, and completion instructions.",
        "You can copy the structure from your own prompt and adapt it for the team member.",
        "",
        "Example:",
        "```bash",
        "# Spawn with a pre-created prompt file",
        "~/paradise_brain/anchovies/scripts/spawn_persona.sh sofia ~/paradise_brain/anchovies/tmp/prompt_sofia.txt",
        "```",
        "",
        "**IMPORTANT:** Do NOT spawn a persona without a prompt file and then send them instructions afterward.",
        "This loads unnecessary context. Always include the task in the prompt file at spawn time.",
        "",
        "Do NOT run `claude --system-prompt` directly - it won't work properly.",
        "",
        "## When All Tasks Are Complete",
        "Once you have finished all your work (including any delegated tasks):",
        "1. **Post a summary to Slack:**",
        "   ```bash",
        f'   ~/paradise_brain/anchovies/scripts/slack "All tasks complete. <brief summary>" --member {persona}' + (f' --thread {thread_ts}' if thread_ts else ''),
        "   ```",
        "2. **Update your status file** with final status",
        "3. **Ask the user to close your session** - say something like: \"All tasks complete. You can close this session with `Ctrl+b &`\"",
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
