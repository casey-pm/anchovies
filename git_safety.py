"""
Git safety module for Anchovies.

Enforces:
  - Each work session operates on its own feature branch (<persona>/<slug>)
  - Personas NEVER push to main/master
  - Personas NEVER merge to main/master
  - NEVER force-push
  - On completion, an auto-PR is created for Casey to review
  - Casey is the only one who can merge (via gh pr merge --rebase)

This module provides:
  - branch_name(persona, task): generate a safe feature branch name
  - is_dangerous_command(cmd): pre-execution safety check
  - create_feature_branch(...): start a new branch for a session
  - create_pr(...): open a PR via the gh CLI
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Branches that are protected — personas MUST NEVER push, merge, or modify these.
PROTECTED_BRANCHES = ("main", "master", "production", "prod", "release")

# Patterns of dangerous git commands. Any of these matched in a shell command
# string should be blocked before execution.
DANGEROUS_COMMAND_PATTERNS = [
    # Force push (any flavour)
    r"\bgit\s+push\s+.*--force\b",
    r"\bgit\s+push\s+.*-f\b",
    r"\bgit\s+push\s+.*\+",  # +branch syntax means force push
    # Push directly to a protected branch
    r"\bgit\s+push\s+\S+\s+(?:HEAD\s*:\s*)?(?:" + "|".join(PROTECTED_BRANCHES) + r")\b",
    r"\bgit\s+push\s+(?:" + "|".join(PROTECTED_BRANCHES) + r")\b",
    # Merge to a protected branch (when on main, merging anything)
    r"\bgit\s+merge\s+",  # We block all 'git merge' inside sessions; merges happen via PR
    # Hard reset
    r"\bgit\s+reset\s+--hard\b",
    # Branch deletion of main
    r"\bgit\s+branch\s+-[Dd]\s+(?:" + "|".join(PROTECTED_BRANCHES) + r")\b",
    # Tag deletion / force tag
    r"\bgit\s+tag\s+-d\s+\w+",
    # Filter-branch / rebase --interactive --root (history rewriting)
    r"\bgit\s+filter-branch\b",
    r"\bgit\s+rebase\s+--root\b",
]

DANGEROUS_REGEXES = [
    re.compile(p, re.IGNORECASE) for p in DANGEROUS_COMMAND_PATTERNS
]


# ---------------------------------------------------------------------------
# Branch naming
# ---------------------------------------------------------------------------


def slugify(text: str, max_length: int = 40) -> str:
    """
    Convert arbitrary text into a git-branch-safe slug.

    Examples:
        "Fix the null bug in app.py" -> "fix-null-bug-app-py"
        "Update API: handle errors" -> "update-api-handle-errors"
    """
    # Lowercase
    s = text.lower()
    # Replace anything that's not a-z, 0-9 with hyphen
    s = re.sub(r"[^a-z0-9]+", "-", s)
    # Strip leading/trailing hyphens
    s = s.strip("-")
    # Collapse multiple hyphens
    s = re.sub(r"-+", "-", s)
    # Truncate
    if len(s) > max_length:
        s = s[:max_length].rstrip("-")
    # Fallback if input was all symbols
    if not s:
        s = "task"
    return s


def branch_name(persona: str, task_description: str) -> str:
    """
    Build the feature branch name for a session.

    Format: <persona>/<slug>
    Example: "sofia/fix-null-processor"

    Casey selected this convention in the makeover Q&A.
    """
    return f"{persona.lower()}/{slugify(task_description)}"


# ---------------------------------------------------------------------------
# Dangerous command detection
# ---------------------------------------------------------------------------


@dataclass
class CommandSafetyResult:
    """Result of checking a shell command for dangerous git operations."""
    command: str
    safe: bool
    reasons: list[str]  # human-readable explanations


def is_dangerous_command(command: str) -> CommandSafetyResult:
    """
    Check whether a shell command contains a dangerous git operation.

    Returns CommandSafetyResult — caller decides whether to block or warn.
    Personas should ALWAYS check this before running git commands.
    """
    reasons: list[str] = []

    for i, pattern in enumerate(DANGEROUS_REGEXES):
        if pattern.search(command):
            reasons.append(DANGEROUS_COMMAND_PATTERNS[i])

    return CommandSafetyResult(
        command=command,
        safe=len(reasons) == 0,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path | str) -> subprocess.CompletedProcess:
    """Run a git command, capturing output."""
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def current_branch(repo_path: Path | str) -> Optional[str]:
    """Get the current git branch name in the given repo, or None on error."""
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def branch_exists(branch: str, repo_path: Path | str) -> bool:
    """Check if a local branch exists."""
    result = _run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo_path)
    return result.returncode == 0


def create_feature_branch(
    persona: str,
    task_description: str,
    repo_path: Path | str,
    base_branch: str = "main",
) -> tuple[bool, str]:
    """
    Create and checkout a feature branch for a work session.

    Args:
        persona: The persona starting the session
        task_description: Brief description (used for slug)
        repo_path: Path to the git repository
        base_branch: Branch to create from (default: main)

    Returns:
        (success, branch_name_or_error_message)
    """
    branch = branch_name(persona, task_description)

    # If branch already exists locally, just checkout
    if branch_exists(branch, repo_path):
        result = _run_git(["checkout", branch], repo_path)
        if result.returncode == 0:
            return (True, branch)
        return (False, f"Failed to checkout existing branch: {result.stderr.strip()}")

    # Otherwise create a new branch from base
    # First make sure base_branch exists
    if not branch_exists(base_branch, repo_path):
        return (False, f"Base branch '{base_branch}' does not exist in {repo_path}")

    result = _run_git(["checkout", "-b", branch, base_branch], repo_path)
    if result.returncode != 0:
        return (False, f"Failed to create branch: {result.stderr.strip()}")

    return (True, branch)


# ---------------------------------------------------------------------------
# Pull request creation
# ---------------------------------------------------------------------------


@dataclass
class PRResult:
    """Result of trying to create a PR."""
    created: bool
    url: Optional[str] = None
    branch: Optional[str] = None
    error: Optional[str] = None


def create_pr(
    persona: str,
    branch: str,
    task_description: str,
    summary: str,
    repo_path: Path | str,
    base: str = "main",
) -> PRResult:
    """
    Create a GitHub PR via the gh CLI from the persona's feature branch.

    The PR is left OPEN — Casey reviews and merges manually.
    Casey enforces rebase-on-main before merging.

    Returns PRResult with the URL if successful.
    """
    title = f"[{persona.title()}] {task_description[:80]}"
    body = (
        f"## Summary\n{summary}\n\n"
        f"## Submitted by\nAnchovies persona: **{persona.title()}**\n\n"
        f"## Branch\n`{branch}`\n\n"
        f"## Review checklist\n"
        f"- [ ] Diff looks correct\n"
        f"- [ ] No protected files touched\n"
        f"- [ ] Tests pass\n"
        f"- [ ] Rebase on main before merge\n\n"
        f"---\n"
        f"_Generated automatically by Anchovies. Do not auto-merge._"
    )

    result = subprocess.run(
        ["gh", "pr", "create",
         "--title", title,
         "--body", body,
         "--base", base,
         "--head", branch],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return PRResult(
            created=False,
            branch=branch,
            error=result.stderr.strip() or "gh pr create failed",
        )

    # gh pr create prints the PR URL on stdout
    url = result.stdout.strip().split("\n")[-1]
    return PRResult(created=True, url=url, branch=branch)


def list_changed_files(repo_path: Path | str, base_branch: str = "main") -> list[str]:
    """List files changed on the current branch vs base_branch."""
    result = _run_git(["diff", "--name-only", f"{base_branch}..HEAD"], repo_path)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.split("\n") if line.strip()]
