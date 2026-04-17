"""
Director module — Light Director role for Marcus.

Provides:
  - Project briefs: structured task overview before work begins
  - Consultation compilation: merge multiple persona inputs into one digest
  - Session summary aggregation: compile all session activity for a project

Triggered via:
  @bot brief [project] <task description>
  @bot consult [project] <task description>
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from . import config
from .teams import get_relevant_personas, get_track_display_name, TRACKS

logger = logging.getLogger(__name__)


def create_project_brief(
    task_description: str,
    project: Optional[str] = None,
    files: list[str] | None = None,
) -> str:
    """
    Generate a structured project brief for a task.

    Args:
        task_description: What needs to be done
        project: Optional project slug
        files: Optional list of mentioned files

    Returns:
        Formatted markdown brief
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Resolve project details
    project_name = config.PROJECT_NAME
    project_desc = ""
    authority_section = ""
    if project:
        try:
            from .project_registry import get_project_registry
            proj = get_project_registry().get(project)
            if proj:
                project_name = proj.display_name
                project_desc = proj.description or ""
                if proj.authority:
                    auto = proj.authority.get("autonomous", [])
                    esc = proj.authority.get("escalate", [])
                    authority_section = "\n## Decision Authority\n"
                    if auto:
                        authority_section += "**Autonomous:** " + ", ".join(auto) + "\n"
                    if esc:
                        authority_section += "**Escalate:** " + ", ".join(esc) + "\n"
        except Exception:
            pass

    # Find relevant personas
    relevant = get_relevant_personas(task_description, n=5)
    team_lines = []
    for member, track, score in relevant:
        track_display = get_track_display_name(track)
        team_lines.append(f"  - **{member.title()}** ({track_display})")

    # Find relevant tracks
    relevant_tracks = list({track for _, track, _ in relevant})
    track_displays = [get_track_display_name(t) for t in relevant_tracks]

    # Build the brief
    parts = [
        f"# Project Brief",
        f"",
        f"**Project:** {project_name}",
    ]
    if project_desc:
        parts.append(f"**Description:** {project_desc}")
    parts.extend([
        f"**Requested:** {now}",
        f"**Objective:** {task_description}",
        f"",
        f"## Relevant Tracks",
        ", ".join(track_displays) if track_displays else "_(no specific track match)_",
        f"",
        f"## Suggested Team",
    ])
    if team_lines:
        parts.extend(team_lines)
    else:
        parts.append("  _(no specific team match — Marcus will coordinate)_")

    if files:
        parts.extend([
            f"",
            f"## Files Mentioned",
        ])
        for f in files:
            parts.append(f"  - `{f}`")

    if authority_section:
        parts.append(authority_section)

    return "\n".join(parts)


def compile_consultation(
    task_description: str,
    persona_inputs: dict[str, str],
    project: Optional[str] = None,
) -> str:
    """
    Compile multiple persona inputs into a single consultation digest.

    Args:
        task_description: The task being consulted on
        persona_inputs: {member_name: their_input_text}
        project: Optional project slug

    Returns:
        Formatted markdown consultation summary
    """
    project_tag = f" [{project}]" if project else ""

    parts = [
        f"# Team Consultation{project_tag}",
        f"",
        f"**Task:** {task_description}",
        f"**Consulted:** {len(persona_inputs)} persona(s)",
        f"",
    ]

    for member, input_text in persona_inputs.items():
        track = ""
        try:
            from .teams import get_track, get_track_display_name
            t = get_track(member)
            if t:
                track = f" ({get_track_display_name(t)})"
        except Exception:
            pass
        parts.extend([
            f"### {member.title()}{track}",
            input_text.strip(),
            "",
        ])

    parts.extend([
        "---",
        f"*Consultation compiled by Marcus on {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
    ])

    return "\n".join(parts)


def aggregate_session_summary(project: Optional[str] = None) -> str:
    """
    Compile a summary of all active and recently completed sessions.

    Args:
        project: If set, filter to this project only

    Returns:
        Formatted summary string
    """
    try:
        from .work_sessions import get_session_manager
        from .storage import get_storage

        session_mgr = get_session_manager()
        storage = get_storage()

        # Active sessions
        active = session_mgr.list_sessions()
        if project:
            active = [s for s in active if getattr(s, "project", None) == project]

        # Recent completions from audit
        import time
        today_start = time.time() - 86400  # last 24h
        completed = storage.query_audit(
            since=today_start,
            event_type="session_completed",
            limit=20,
        )
        if project:
            completed = [e for e in completed if e.details.get("project") == project]

        project_tag = f" [{project}]" if project else ""
        parts = [f"# Session Summary{project_tag}", ""]

        if active:
            parts.append(f"## Active Sessions ({len(active)})")
            for s in active:
                p_tag = f" [{s.project}]" if s.project else ""
                parts.append(
                    f"  - **{s.member.title()}**{p_tag}: {s.task_description[:50]}... "
                    f"({s.total_minutes:.0f}m)"
                )
            parts.append("")

        if completed:
            parts.append(f"## Completed Today ({len(completed)})")
            for e in completed:
                task = e.details.get("task", "unknown")
                parts.append(f"  - **{(e.member or 'unknown').title()}**: {task[:50]}")
            parts.append("")

        if not active and not completed:
            parts.append("No active or recent sessions.")

        return "\n".join(parts)

    except Exception as e:
        logger.error(f"Failed to aggregate session summary: {e}")
        return "Unable to generate session summary."
