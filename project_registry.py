"""
Project Registry — manages multi-project configuration for Anchovies.

Projects can be registered via:
  1. projects.yaml (hot-reloaded on file change, no restart needed)
  2. Slack commands (@bot add project ...)
  3. Direct API: get_project_registry().register(Project(...))

When no projects are registered, the system falls back to legacy
single-project mode using config.PROJECT_NAME and config.CONTEXT_BASE.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from . import config

logger = logging.getLogger(__name__)

PROJECTS_YAML_PATH = config.BOT_DIR / "projects.yaml"


@dataclass
class Project:
    """A registered project the team can work on."""
    name: str              # lowercase slug, e.g. "calculator"
    display_name: str      # human-readable, e.g. "Calculator App"
    context_base: Path     # directory with status/, questions/, instructions/
    working_dir: Path      # git repo / code directory for tmux sessions
    description: str = ""
    default_branch: str = "main"
    active: bool = True


def ensure_project_dirs(project: Project) -> None:
    """Create the status/, questions/, instructions/ dirs under context_base if missing."""
    for subdir in ("status", "questions", "instructions"):
        (project.context_base / subdir).mkdir(parents=True, exist_ok=True)


class ProjectRegistry:
    """
    Thread-safe registry of projects.

    Loads from projects.yaml on init and supports hot-reload via mtime check.
    Falls back to legacy single-project mode when empty.
    """

    def __init__(self, yaml_path: Path = PROJECTS_YAML_PATH):
        self._projects: dict[str, Project] = {}
        self._default_project: str | None = None
        self._lock = threading.Lock()
        self._yaml_path = yaml_path
        self._last_mtime: float = 0.0
        self._load_from_yaml()

    # --- Query methods ---

    def get(self, name: str) -> Optional[Project]:
        """Get a project by name (case-insensitive)."""
        with self._lock:
            return self._projects.get(name.lower())

    def list_projects(self, active_only: bool = True) -> list[Project]:
        """List all registered projects."""
        with self._lock:
            projects = list(self._projects.values())
        if active_only:
            projects = [p for p in projects if p.active]
        return projects

    def get_default(self) -> Optional[Project]:
        """Get the default project, if set."""
        with self._lock:
            if self._default_project:
                return self._projects.get(self._default_project)
        return None

    def is_empty(self) -> bool:
        """True when no projects are registered (legacy mode)."""
        with self._lock:
            return len(self._projects) == 0

    # --- Mutation methods ---

    def register(self, project: Project) -> None:
        """Register or update a project."""
        with self._lock:
            self._projects[project.name.lower()] = project
        logger.info(f"Registered project: {project.name}")

    def unregister(self, name: str) -> bool:
        """Remove a project. Returns True if it existed."""
        with self._lock:
            removed = self._projects.pop(name.lower(), None)
        if removed:
            logger.info(f"Unregistered project: {name}")
        return removed is not None

    def set_default(self, name: str | None) -> None:
        """Set the default project (or None to clear)."""
        with self._lock:
            self._default_project = name.lower() if name else None

    def get_default_name(self) -> str | None:
        """Get the default project name, if set."""
        with self._lock:
            return self._default_project

    # --- YAML loading and hot-reload ---

    def _load_from_yaml(self) -> None:
        """Load projects from the YAML file if it exists."""
        if not self._yaml_path.exists():
            return
        try:
            mtime = self._yaml_path.stat().st_mtime
            raw = self._yaml_path.read_text()
            data = yaml.safe_load(raw) or {}
            with self._lock:
                self._last_mtime = mtime
                self._projects.clear()
                for name, pdata in (data.get("projects") or {}).items():
                    if not isinstance(pdata, dict):
                        continue
                    ctx = pdata.get("context_base", "")
                    self._projects[name.lower()] = Project(
                        name=name.lower(),
                        display_name=pdata.get("display_name", name.title()),
                        context_base=Path(ctx),
                        working_dir=Path(pdata.get("working_dir", ctx)),
                        description=pdata.get("description", ""),
                        default_branch=pdata.get("default_branch", "main"),
                        active=pdata.get("active", True),
                    )
                self._default_project = data.get("default_project")
                if self._default_project:
                    self._default_project = self._default_project.lower()
            logger.info(f"Loaded {len(self._projects)} project(s) from {self._yaml_path}")
        except Exception as e:
            logger.error(f"Failed to load projects.yaml: {e}")

    def reload_if_changed(self) -> bool:
        """Check file mtime and reload if changed. Returns True if reloaded."""
        if not self._yaml_path.exists():
            return False
        try:
            mtime = self._yaml_path.stat().st_mtime
            if mtime > self._last_mtime:
                self._load_from_yaml()
                return True
        except Exception as e:
            logger.error(f"Error checking projects.yaml: {e}")
        return False

    def save_to_yaml(self) -> None:
        """Persist current registry state to YAML (after Slack command mutations)."""
        data: dict = {"projects": {}, "default_project": None}
        with self._lock:
            data["default_project"] = self._default_project
            for name, p in self._projects.items():
                data["projects"][name] = {
                    "display_name": p.display_name,
                    "context_base": str(p.context_base),
                    "working_dir": str(p.working_dir),
                    "description": p.description,
                    "default_branch": p.default_branch,
                    "active": p.active,
                }
        self._yaml_path.parent.mkdir(parents=True, exist_ok=True)
        self._yaml_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False)
        )
        self._last_mtime = self._yaml_path.stat().st_mtime
        logger.info(f"Saved {len(data['projects'])} project(s) to {self._yaml_path}")


# --- Singleton ---

_registry: Optional[ProjectRegistry] = None
_registry_lock = threading.Lock()


def get_project_registry(yaml_path: Optional[Path] = None) -> ProjectRegistry:
    """Get the singleton ProjectRegistry instance."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = ProjectRegistry(yaml_path or PROJECTS_YAML_PATH)
    return _registry


def reset_registry():
    """Reset the singleton (for tests)."""
    global _registry
    with _registry_lock:
        _registry = None
