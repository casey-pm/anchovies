"""
Skill Tracking — personas develop skills over time based on sessions.

After each reflection, skills demonstrated are extracted and appended
to the persona's profile YAML. Skills influence future task routing
(personas with relevant acquired skills rank higher).

Skills format in YAML profiles:
  acquired_skills:
    - skill: "zero-division handling"
      acquired: "2026-04-17"
      project: "calculator"
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from . import config

logger = logging.getLogger(__name__)


def extract_skills_from_reflection(reflection_text: str) -> list[str]:
    """
    Extract skill keywords from a reflection's text.

    Looks for patterns like:
      - "Learned about X"
      - "Used X for the first time"
      - "Demonstrated X"
      - Technical terms (pytest, dbt, SQL patterns, etc.)

    Returns a deduplicated list of skill strings.
    """
    skills: list[str] = []

    # Pattern: "learned about/how to X"
    for match in re.finditer(
        r"(?:learned|discovered|figured out|understood)\s+(?:about|how to)?\s*([^.\n,]{3,40})",
        reflection_text,
        re.IGNORECASE,
    ):
        skills.append(match.group(1).strip().lower())

    # Pattern: "used X" or "applied X"
    for match in re.finditer(
        r"(?:used|applied|implemented|demonstrated)\s+([^.\n,]{3,30})",
        reflection_text,
        re.IGNORECASE,
    ):
        skills.append(match.group(1).strip().lower())

    # Common technical terms found directly
    tech_terms = [
        "pytest", "dbt", "sql", "python", "bigquery", "git", "css",
        "weasyprint", "api", "rest", "json", "yaml", "docker",
        "pandas", "numpy", "asyncio", "sqlite", "postgresql",
        "javascript", "typescript", "react", "html", "markdown",
        "error handling", "zero division", "null check", "edge cases",
        "unit test", "integration test", "refactoring", "code review",
        "data quality", "data pipeline", "etl", "transformation",
    ]
    text_lower = reflection_text.lower()
    for term in tech_terms:
        if term in text_lower:
            skills.append(term)

    # Deduplicate and clean
    seen = set()
    unique = []
    for s in skills:
        s = s.strip().rstrip(".")
        if s and s not in seen and len(s) > 2:
            seen.add(s)
            unique.append(s)

    return unique[:10]  # Cap at 10 skills per reflection


def save_skills_to_profile(
    member: str,
    skills: list[str],
    project: Optional[str] = None,
) -> int:
    """
    Append acquired skills to a persona's profile YAML.

    Only adds skills not already in the acquired_skills list.

    Args:
        member: Persona name (lowercase)
        skills: List of skill strings to add
        project: Optional project slug for context

    Returns:
        Number of new skills added
    """
    profile_path = config.PROFILES_DIR / f"profile_{member}.yaml"
    if not profile_path.exists():
        logger.warning(f"Profile not found for {member}: {profile_path}")
        return 0

    try:
        data = yaml.safe_load(profile_path.read_text()) or {}
    except Exception as e:
        logger.error(f"Failed to read profile for {member}: {e}")
        return 0

    existing = data.get("acquired_skills", [])
    existing_names = {s.get("skill", "").lower() for s in existing if isinstance(s, dict)}

    today = date.today().isoformat()
    added = 0

    for skill in skills:
        if skill.lower() not in existing_names:
            existing.append({
                "skill": skill,
                "acquired": today,
                "project": project,
            })
            existing_names.add(skill.lower())
            added += 1

    if added:
        data["acquired_skills"] = existing
        try:
            profile_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
            logger.info(f"Added {added} skill(s) to {member}'s profile")
        except Exception as e:
            logger.error(f"Failed to write profile for {member}: {e}")
            return 0

    return added


def load_acquired_skills(member: str) -> list[dict]:
    """Load a persona's acquired skills from their profile YAML."""
    profile_path = config.PROFILES_DIR / f"profile_{member}.yaml"
    if not profile_path.exists():
        return []

    try:
        data = yaml.safe_load(profile_path.read_text()) or {}
        return data.get("acquired_skills", [])
    except Exception:
        return []


def get_skill_names(member: str) -> list[str]:
    """Get just the skill name strings for a persona."""
    skills = load_acquired_skills(member)
    return [s.get("skill", "") for s in skills if isinstance(s, dict) and s.get("skill")]


def format_skills_for_prompt(member: str) -> str:
    """Format acquired skills for injection into a session prompt."""
    skills = load_acquired_skills(member)
    if not skills:
        return ""

    lines = ["## Acquired Skills (from previous sessions)"]
    for s in skills[-10:]:  # Show last 10
        skill = s.get("skill", "")
        acquired = s.get("acquired", "")
        project = s.get("project", "")
        project_tag = f" [{project}]" if project else ""
        lines.append(f"- {skill} (acquired {acquired}{project_tag})")

    return "\n".join(lines)
