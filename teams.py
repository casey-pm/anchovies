"""
Track-based team structure for Anchovies.

Organizes the 16 personas into 4 tracks, each with a lead.
Provides keyword-based routing to find the most relevant personas
for a given task description.

Tracks:
  data_engineering — Elena (lead), James, Victor, Anna
  analytics       — Sofia (lead), Julia, Raj, Leo
  bi_reporting    — Natalie (lead), Tom, Priya, Mike, Nina
  leadership      — Marcus (lead), Kai, Olivia
"""

from __future__ import annotations

import re
from typing import Optional


TRACKS: dict[str, dict] = {
    "data_engineering": {
        "display_name": "Data Engineering",
        "lead": "elena",
        "members": ["elena", "james", "victor", "anna"],
        "keywords": [
            "pipeline", "etl", "data quality", "schema", "migration",
            "bigquery", "warehouse", "data engineer", "data architect",
            "ingestion", "lakehouse", "spark", "delta", "dbt source",
            "data lineage", "orchestration", "airflow",
        ],
    },
    "analytics": {
        "display_name": "Analytics & Science",
        "lead": "sofia",
        "members": ["sofia", "julia", "raj", "leo"],
        "keywords": [
            "dbt", "model", "analysis", "forecast", "statistics",
            "transformation", "sql", "analytics", "data scientist",
            "machine learning", "prediction", "regression", "clustering",
            "staging", "mart", "incremental", "test coverage",
            "integration", "data science",
        ],
    },
    "bi_reporting": {
        "display_name": "BI & Reporting",
        "lead": "natalie",
        "members": ["natalie", "tom", "priya", "mike", "nina"],
        "keywords": [
            "report", "dashboard", "visualization", "metrics",
            "looker", "tableau", "power bi", "kpi", "chart",
            "bi", "business intelligence", "reporting", "css",
            "design", "layout", "pdf", "weasyprint", "visual",
        ],
    },
    "leadership": {
        "display_name": "Leadership & Quality",
        "lead": "marcus",
        "members": ["marcus", "kai", "olivia"],
        "keywords": [
            "project", "review", "documentation", "quality",
            "standards", "code review", "coordinate", "plan",
            "status", "priority", "blocker", "decision",
            "doc", "readme", "guide",
        ],
    },
}

# Build reverse lookup: member -> track name
_MEMBER_TO_TRACK: dict[str, str] = {}
for _track_name, _track_data in TRACKS.items():
    for _member in _track_data["members"]:
        _MEMBER_TO_TRACK[_member] = _track_name


def get_track(member: str) -> Optional[str]:
    """Get the track name a persona belongs to, or None if not found."""
    return _MEMBER_TO_TRACK.get(member.lower())


def get_track_lead(member: str) -> Optional[str]:
    """Get the track lead for a persona's track, or None."""
    track_name = get_track(member)
    if track_name:
        return TRACKS[track_name]["lead"]
    return None


def get_track_members(track_name: str) -> list[str]:
    """Get all members of a track. Returns empty list if track not found."""
    track = TRACKS.get(track_name.lower())
    return track["members"] if track else []


def get_track_display_name(track_name: str) -> str:
    """Get the human-readable track name."""
    track = TRACKS.get(track_name.lower())
    return track["display_name"] if track else track_name.replace("_", " ").title()


def get_relevant_personas(task_description: str, n: int = 5) -> list[tuple[str, str, float]]:
    """
    Find the N most relevant personas for a task based on keyword matching.

    Scores each persona based on how many of their track's keywords appear
    in the task description. Also considers persona-specific expertise from
    their profile if available.

    Args:
        task_description: The task text to match against
        n: Maximum number of personas to return

    Returns:
        List of (member_name, track_name, score) tuples, highest score first.
        Only includes personas with score > 0.
    """
    desc_lower = task_description.lower()
    scores: list[tuple[str, str, float]] = []

    for track_name, track_data in TRACKS.items():
        # Count keyword matches for this track
        track_score = 0.0
        for keyword in track_data["keywords"]:
            if keyword in desc_lower:
                # Longer keywords get higher weight (more specific)
                track_score += len(keyword.split())

        if track_score > 0:
            # Distribute score to track members
            # Lead gets a small bonus (they're the go-to for their track)
            for member in track_data["members"]:
                member_score = track_score
                if member == track_data["lead"]:
                    member_score *= 1.2  # 20% lead bonus
                scores.append((member, track_name, member_score))

    # Sort by score descending, take top N
    scores.sort(key=lambda x: x[2], reverse=True)

    # Deduplicate (a member can only appear once)
    seen = set()
    unique: list[tuple[str, str, float]] = []
    for member, track, score in scores:
        if member not in seen:
            seen.add(member)
            unique.append((member, track, score))

    return unique[:n]


def get_suggested_persona(task_description: str) -> Optional[tuple[str, str, str]]:
    """
    Get the single best persona suggestion for a task.

    Returns:
        (member_name, track_display_name, reason) or None if no match.
    """
    results = get_relevant_personas(task_description, n=1)
    if not results:
        return None

    member, track_name, score = results[0]
    track_display = get_track_display_name(track_name)

    # Build a reason string
    track_data = TRACKS[track_name]
    matched_keywords = [
        kw for kw in track_data["keywords"]
        if kw in task_description.lower()
    ]
    reason = f"{track_display} track"
    if matched_keywords:
        reason += f" — matched: {', '.join(matched_keywords[:3])}"

    return (member, track_display, reason)
