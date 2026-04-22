"""
Control commands for Anchovies.

Handles: stop all, stop <name>, pause, resume, assign, brief, consult,
reflect, daily summary, and project management commands.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional

from .. import config
from ..work_sessions import get_session_manager, get_tmux_manager

logger = logging.getLogger(__name__)

# Pause flag — when True, the bot refuses new work requests (chat still works)
_paused: bool = False


def is_paused() -> bool:
    return _paused


async def handle_control_command(client, channel_id: str, thread_ts: str, message: str) -> bool:
    """
    Handle bot control commands.
    Returns True if the message was a control command (handled), False otherwise.
    """
    global _paused
    msg = message.strip().lower()

    # --- stop all ---
    if msg in ("stop all", "stop everything", "kill all"):
        session_mgr = get_session_manager()
        tmux = get_tmux_manager()
        closed = []
        for member in list(session_mgr.active_sessions.keys()):
            tmux.close_persona_tab(member)
            session_mgr.storage.mark_session_status(member, "killed")
            session_mgr.storage.log_event("session_killed", member=member, details={"reason": "stop all"})
            del session_mgr.active_sessions[member]
            closed.append(member)

        from ..task_queue import get_task_queue
        queue = get_task_queue()
        queued_count = queue.clear()

        text = f":octagonal_sign: All sessions stopped."
        if closed:
            text += f"\nKilled: {', '.join(m.title() for m in closed)}"
        if queued_count:
            text += f"\nCleared {queued_count} queued task(s)."
        await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)
        return True

    # --- stop <name> ---
    stop_match = re.match(r"stop\s+(\w+)", msg)
    if stop_match:
        name = stop_match.group(1).lower()
        if name in ("all", "everything"):
            return False
        member = config.get_member_name(name)
        if not member:
            return False

        session_mgr = get_session_manager()
        if member in session_mgr.active_sessions:
            tmux = get_tmux_manager()
            tmux.close_persona_tab(member)
            session_mgr.storage.mark_session_status(member, "killed")
            session_mgr.storage.log_event("session_killed", member=member, details={"reason": "stop command"})
            del session_mgr.active_sessions[member]
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=f":octagonal_sign: {member.title()}'s session stopped.",
            )
        else:
            from ..task_queue import get_task_queue
            removed = get_task_queue().remove_member(member)
            if removed:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f":octagonal_sign: Removed {member.title()} from the queue.",
                )
            else:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f":shrug: {member.title()} has no active session or queued task.",
                )
        return True

    # --- pause ---
    if msg == "pause":
        _paused = True
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=":pause_button: Bot paused — no new work sessions will be accepted. Active sessions continue. Use `resume` to re-enable.",
        )
        return True

    # --- resume ---
    if msg == "resume":
        _paused = False
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=":arrow_forward: Bot resumed — accepting work requests again.",
        )
        return True

    # --- assign <persona> <task> ---
    assign_match = re.match(r"assign\s+(\w+)\s+(.+)", message.strip(), re.IGNORECASE)
    if assign_match:
        name = assign_match.group(1).lower()
        task_desc = assign_match.group(2).strip()
        member = config.get_member_name(name)
        if member:
            logger.info(f"[Control] Assign command: {member} -> '{task_desc[:50]}'")
            from ..router import extract_project_tag
            assign_project, assign_task = extract_project_tag(task_desc)

            from ..chat_hub.prompt_builder import build_task_prompt
            task_prompt = build_task_prompt(
                persona=member,
                task_description=assign_task,
                context=assign_task,
                thread_ts=thread_ts,
                project=assign_project,
            )

            from .spawner import spawn_session_for_task
            await spawn_session_for_task(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                member=member,
                task_description=assign_task[:100],
                task_prompt=task_prompt,
                project=assign_project,
            )
            return True
        else:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=f":warning: Unknown team member: `{name}`.",
            )
            return True

    # --- brief ---
    if msg.startswith("brief ") or msg == "brief":
        from ..director import create_project_brief
        from ..router import extract_project_tag
        brief_project, brief_text = extract_project_tag(message.strip())
        brief_text = re.sub(r"^brief\s*", "", brief_text, flags=re.IGNORECASE).strip()
        if not brief_text:
            brief_text = "General project overview"
        brief = create_project_brief(task_description=brief_text, project=brief_project)
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":clipboard: Marcus:\n\n{brief}",
        )
        return True

    # --- consult ---
    if msg.startswith("consult ") or msg == "consult":
        from ..director import compile_consultation
        from ..teams import get_relevant_personas, get_track_display_name
        from ..router import extract_project_tag
        from ..cli_runner import run_claude_cli

        consult_project, consult_text = extract_project_tag(message.strip())
        consult_text = re.sub(r"^consult\s*", "", consult_text, flags=re.IGNORECASE).strip()
        if not consult_text:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=":warning: Usage: `consult [project] <task description>`",
            )
            return True

        relevant = get_relevant_personas(consult_text, n=5)
        if not relevant:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=":mag: No relevant personas found for this task.",
            )
            return True

        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":speech_balloon: Consulting {len(relevant)} persona(s): {', '.join(r[0].title() for r in relevant)}...",
        )

        persona_inputs = {}

        async def _get_persona_input(member_name, role_track):
            prompt = (
                f"You are {member_name.title()}, on the {role_track} track. "
                f"You've been asked for your perspective on:\n"
                f"{consult_text}\n"
                f"In 2-3 sentences, share your key concern, suggestion, or insight "
                f"from your area of expertise. Be specific and actionable."
            )
            try:
                response = await run_claude_cli(prompt, model=config.CHAT_MODEL)
                persona_inputs[member_name] = response
            except Exception as e:
                persona_inputs[member_name] = f"_(consultation failed: {e})_"

        tasks = [
            _get_persona_input(member, get_track_display_name(track))
            for member, track, _ in relevant
        ]
        await asyncio.gather(*tasks)

        compiled = compile_consultation(consult_text, persona_inputs, consult_project)
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text=f":clipboard: Team Consultation:\n\n{compiled}",
        )
        return True

    # --- reflect ---
    reflect_match = re.match(r"reflect\s*(\w*)", msg)
    if reflect_match:
        member_name = reflect_match.group(1) or None
        if member_name and member_name in config.TEAM_MEMBERS:
            from ..reflection import manual_reflect
            reflection = await manual_reflect(member_name, project=None)
            if reflection:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f":mirror: {member_name.title()}'s Reflection:\n\n{reflection}",
                )
            else:
                await client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f":warning: Reflection failed for {member_name.title()}.",
                )
        else:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=":mirror: Usage: `reflect <persona_name>` — e.g., `reflect sofia`",
            )
        return True

    # --- daily summary ---
    if msg in ("daily summary", "summary", "status summary"):
        from ..cost_tracking import get_today_spend, DAILY_BUDGET_USD
        from ..task_queue import get_task_queue

        session_mgr = get_session_manager()
        storage = session_mgr.storage
        spend, calls = get_today_spend()
        queue = get_task_queue()

        today_start = time.time() - (time.time() % 86400)
        completed = storage.query_audit(since=today_start, event_type="session_completed")
        started = storage.query_audit(since=today_start, event_type="session_started")

        active = session_mgr.list_sessions()
        active_lines = []
        for s in active:
            project_tag = f" [{s.project}]" if s.project else ""
            active_lines.append(f"  - {s.member.title()}{project_tag}: {s.task_description[:40]}... ({s.total_minutes:.0f}m)")

        lines = [
            f":bar_chart: Daily Summary",
            f"",
            f"Sessions: {len(started)} started, {len(completed)} completed, {len(active)} active",
        ]
        if active_lines:
            lines.append("Active now:")
            lines.extend(active_lines)

        queue_size = queue.size
        if queue_size:
            lines.append(f"Queued: {queue_size} task(s) waiting")

        lines.append(f"")
        lines.append(f"Cost: ${spend:.2f} / ${DAILY_BUDGET_USD:.2f} ({calls} API calls)")

        paused_tag = " :pause_button: PAUSED" if _paused else ""
        lines.append(f"Status: {'Paused' if _paused else 'Running'}{paused_tag}")

        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            text="\n".join(lines),
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Project management commands
# ---------------------------------------------------------------------------


def parse_project_command(message: str) -> dict | None:
    """Detect if a message is a project management command."""
    msg = message.strip().lower()

    if msg in ("projects", "list projects"):
        return {"command": "list", "args": ""}

    add_match = re.match(
        r"add\s+project\s+(\S+)\s+--context\s+(\S+)(.*)",
        message.strip(), re.IGNORECASE,
    )
    if add_match:
        name = add_match.group(1).lower()
        context = add_match.group(2)
        rest = add_match.group(3)
        working_dir = context
        description = ""
        wd_match = re.search(r"--working-dir\s+(\S+)", rest)
        if wd_match:
            working_dir = wd_match.group(1)
        desc_match = (
            re.search(r'--desc\s+"([^"]+)"', rest) or
            re.search(r"--desc\s+'([^']+)'", rest) or
            re.search(r"--desc\s+(\S+)", rest)
        )
        if desc_match:
            description = desc_match.group(1)
        return {
            "command": "add",
            "args": {"name": name, "context": context, "working_dir": working_dir, "description": description},
        }

    remove_match = re.match(r"remove\s+project\s+(\S+)", msg)
    if remove_match:
        return {"command": "remove", "args": remove_match.group(1)}

    set_match = re.match(r"set\s+default\s+project\s+(\S+)", msg)
    if set_match:
        return {"command": "set_default", "args": set_match.group(1)}

    if msg in ("clear default project", "clear default"):
        return {"command": "clear_default", "args": ""}

    info_match = re.match(r"project\s+info\s+(\S+)", msg)
    if info_match:
        return {"command": "info", "args": info_match.group(1)}

    return None


async def handle_project_command(client, channel_id: str, thread_ts: str, cmd: dict) -> None:
    """Execute a project management command and post the result to Slack."""
    from ..project_registry import Project, get_project_registry, ensure_project_dirs

    registry = get_project_registry()
    registry.reload_if_changed()
    command = cmd["command"]
    args = cmd["args"]

    if command == "list":
        projects = registry.list_projects(active_only=False)
        if not projects:
            text = ":file_folder: No projects registered.\nUse `add project <name> --context <path>` to add one."
        else:
            lines = [":file_folder: Registered Projects:"]
            default_name = registry.get_default_name()
            for p in projects:
                status = ":white_check_mark:" if p.active else ":no_entry_sign:"
                default_tag = " (default)" if p.name == default_name else ""
                lines.append(f"  {status} {p.display_name} (`{p.name}`){default_tag}")
                if p.description:
                    lines.append(f"      {p.description}")
                lines.append(f"      Context: `{p.context_base}`")
                if str(p.working_dir) != str(p.context_base):
                    lines.append(f"      Working dir: `{p.working_dir}`")
            text = "\n".join(lines)

    elif command == "add":
        name = args["name"]
        project = Project(
            name=name,
            display_name=name.replace("-", " ").replace("_", " ").title(),
            context_base=Path(args["context"]).expanduser(),
            working_dir=Path(args["working_dir"]).expanduser(),
            description=args.get("description", ""),
        )
        registry.register(project)
        ensure_project_dirs(project)
        registry.save_to_yaml()
        text = f":white_check_mark: Project {project.display_name} (`{name}`) registered.\nContext: `{project.context_base}`\nWorking dir: `{project.working_dir}`"

    elif command == "remove":
        name = args
        if registry.unregister(name):
            registry.save_to_yaml()
            text = f":wastebasket: Project `{name}` removed."
        else:
            text = f":warning: Project `{name}` not found."

    elif command == "set_default":
        name = args
        if registry.get(name):
            registry.set_default(name)
            registry.save_to_yaml()
            proj = registry.get(name)
            text = f":pushpin: Default project set to {proj.display_name} (`{name}`).\nMessages without a `[project]` tag will use this project."
        else:
            text = f":warning: Project `{name}` not found."

    elif command == "clear_default":
        registry.set_default(None)
        registry.save_to_yaml()
        text = ":pushpin: Default project cleared."

    elif command == "info":
        name = args
        proj = registry.get(name)
        if proj:
            default_tag = " (default)" if registry.get_default_name() == name else ""
            text = (
                f":file_folder: {proj.display_name} (`{proj.name}`){default_tag}\n"
                f"Description: {proj.description or '(none)'}\n"
                f"Context: `{proj.context_base}`\n"
                f"Working dir: `{proj.working_dir}`\n"
                f"Default branch: `{proj.default_branch}`\n"
                f"Active: {'Yes' if proj.active else 'No'}"
            )
        else:
            text = f":warning: Project `{name}` not found."

    else:
        text = f":warning: Unknown project command: `{command}`"

    await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)
