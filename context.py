"""
Context file loader for Team Forum Bot.

Loads profile, status, questions, and instructions for team members.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config

logger = logging.getLogger(__name__)


@dataclass
class MemberProfile:
    """Team member profile data."""
    name: str
    nickname: str = ""
    role: str = ""
    avatar_emoji: str = ":bust_in_silhouette:"
    personality: dict = field(default_factory=dict)
    traits: list[str] = field(default_factory=list)
    speech_patterns: list[str] = field(default_factory=list)
    expertise: list[str] = field(default_factory=list)
    handles_topics: list[str] = field(default_factory=list)
    relationships: dict = field(default_factory=dict)
    job_summary: str = ""  # Condensed job description with key skills
    team_roster: dict = field(default_factory=dict)  # Full team roster (for managers)
    model_override: str | None = None  # Per-persona model (e.g., "opus") — overrides CHAT_MODEL/WORK_MODEL


@dataclass
class MemberContext:
    """Complete context for a team member."""
    profile: MemberProfile
    memory_content: str = ""  # Lessons learned and persistent memory
    status_content: str = ""
    questions_content: str = ""
    instructions_content: str = ""

    def build_system_prompt(self) -> str:
        """
        Build the system prompt for this team member.

        Default loads: profile (with job_summary) + memory + status
        (Optimized for token efficiency while maintaining team context)
        """
        parts = [
            f"You are {self.profile.name}",
        ]

        if self.profile.nickname:
            parts[0] += f' ("{self.profile.nickname}")'

        if self.profile.role:
            parts[0] += f", {self.profile.role} on the Domain 360 project team."
        else:
            parts[0] += " on the Domain 360 project team."

        # Personality
        if self.profile.personality:
            parts.append("")
            parts.append("## Your Personality")
            style = self.profile.personality.get("communication_style", "professional")
            formality = self.profile.personality.get("formality", "professional-casual")
            verbosity = self.profile.personality.get("verbosity", "moderate")
            parts.append(f"- Communication style: {style}")
            parts.append(f"- Formality: {formality}")
            parts.append(f"- Verbosity: {verbosity}")

        # Traits
        if self.profile.traits:
            parts.append("")
            parts.append("## Your Character Traits")
            for trait in self.profile.traits:
                parts.append(f"- {trait}")

        # Speech patterns
        if self.profile.speech_patterns:
            parts.append("")
            parts.append("## Phrases You Often Use")
            for pattern in self.profile.speech_patterns:
                parts.append(f'- "{pattern}"')

        # Expertise
        if self.profile.expertise:
            parts.append("")
            parts.append("## Your Expertise")
            for skill in self.profile.expertise:
                parts.append(f"- {skill}")

        # Job summary (compact version with skills)
        if self.profile.job_summary:
            parts.append("")
            parts.append("## Your Role & Key Skills")
            parts.append(self.profile.job_summary.strip())

        # Memory (lessons learned, persistent knowledge)
        if self.memory_content:
            parts.append("")
            parts.append("## Your Memory (Lessons Learned)")
            memory = self.memory_content[:2000]
            if len(self.memory_content) > 2000:
                memory += "\n... (see full memory file for more)"
            parts.append(memory)

        # Status (loaded by default)
        if self.status_content:
            parts.append("")
            parts.append("## Your Current Work Status")
            status = self.status_content[:2500]
            if len(self.status_content) > 2500:
                status += "\n... (truncated)"
            parts.append(status)

        # Team roster (for managers like Marcus)
        if self.profile.team_roster:
            parts.append("")
            parts.append("## Your Team (Full Roster)")
            for pillar, members in self.profile.team_roster.items():
                pillar_name = pillar.replace("_", " ").title()
                parts.append(f"\n### {pillar_name}")
                for member in members:
                    name = member.get("name", "")
                    nickname = member.get("nickname", "")
                    role = member.get("role", "")
                    parts.append(f"- **{name}** ({nickname}) - {role}")

        # Guidelines
        parts.append("")
        parts.append("## Response Guidelines")
        parts.append("- Respond as this team member would, maintaining their personality")
        parts.append("- Keep responses conversational but professional")
        parts.append("- Reference your current work and status when relevant")
        parts.append("- Apply lessons from your memory when relevant")
        parts.append("- If asked about something outside your expertise, @mention a teammate who can help")
        parts.append("- You can @mention teammates (e.g., @sofia, @raj) to bring them into the conversation")
        parts.append("- Be helpful and collaborative")

        return "\n".join(parts)


def load_profile(member_name: str) -> MemberProfile:
    """
    Load a team member's profile from YAML.

    Args:
        member_name: Lowercase team member name

    Returns:
        MemberProfile instance
    """
    profile_path = config.PROFILES_DIR / f"profile_{member_name}.yaml"

    if not profile_path.exists():
        logger.warning(f"Profile not found for {member_name}: {profile_path}")
        return MemberProfile(name=member_name.title())

    try:
        with open(profile_path, "r") as f:
            data = yaml.safe_load(f) or {}

        return MemberProfile(
            name=data.get("name", member_name.title()),
            nickname=data.get("nickname", ""),
            role=data.get("role", ""),
            avatar_emoji=data.get("avatar_emoji", ":bust_in_silhouette:"),
            personality=data.get("personality", {}),
            traits=data.get("traits", []),
            speech_patterns=data.get("speech_patterns", []),
            expertise=data.get("expertise", []),
            handles_topics=data.get("handles_topics", []),
            relationships=data.get("relationships", {}),
            job_summary=data.get("job_summary", ""),
            team_roster=data.get("team_roster", {}),
            model_override=data.get("model_override"),
        )
    except Exception as e:
        logger.error(f"Error loading profile for {member_name}: {e}")
        return MemberProfile(name=member_name.title())


def load_file_content(path: Path) -> str:
    """Load content from a file, returning empty string if not found."""
    if not path.exists():
        return ""

    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Error reading {path}: {e}")
        return ""


def load_member_context(member_name: str) -> MemberContext:
    """
    Load complete context for a team member.

    Default loads: profile (with job_summary) + memory + status
    (Questions and instructions not loaded by default to save tokens)

    Args:
        member_name: Lowercase team member name

    Returns:
        MemberContext with profile and context files
    """
    profile = load_profile(member_name)

    # Load memory file (lessons learned, persistent knowledge)
    memory_path = config.BOT_DIR / "memory" / f"memory_{member_name}.md"
    memory_content = load_file_content(memory_path)

    # Load status file (loaded by default)
    status_path = config.CONTEXT_BASE / "status" / f"status_{member_name}.md"

    # Questions and instructions not loaded by default (can add later if needed)
    questions_path = config.CONTEXT_BASE / "questions" / f"questions_for_{member_name}.md"
    instructions_path = config.CONTEXT_BASE / "instructions" / f"instructions_{member_name}.md"

    return MemberContext(
        profile=profile,
        memory_content=memory_content,
        status_content=load_file_content(status_path),
        questions_content="",  # Not loaded by default
        instructions_content="",  # Not loaded by default
    )
