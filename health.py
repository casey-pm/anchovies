"""
Health Check CLI — verify Anchovies dependencies and configuration.

Usage:
    python -m anchovies.health

Checks: tmux, claude CLI, git, gh CLI, python3, SQLite DB, profiles,
Slack token format (not validity — that requires network).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def check_binary(name: str, description: str) -> tuple[bool, str]:
    """Check if a binary is on PATH."""
    path = shutil.which(name)
    if path:
        return True, f"{description}: {path}"
    return False, f"{description}: NOT FOUND"


def check_path_exists(path: Path, description: str) -> tuple[bool, str]:
    """Check if a path exists."""
    if path.exists():
        return True, f"{description}: {path}"
    return False, f"{description}: MISSING ({path})"


def check_path_has_files(path: Path, pattern: str, description: str) -> tuple[bool, str]:
    """Check if a directory contains files matching a pattern."""
    if not path.exists():
        return False, f"{description}: directory missing ({path})"
    files = list(path.glob(pattern))
    if files:
        return True, f"{description}: {len(files)} file(s) in {path}"
    return False, f"{description}: no {pattern} files in {path}"


def run_checks() -> list[tuple[bool, str, str]]:
    """
    Run all health checks.

    Returns list of (passed, category, message) tuples.
    """
    results: list[tuple[bool, str, str]] = []

    # --- External binaries ---
    for binary, desc in [
        ("tmux", "tmux (session manager)"),
        ("claude", "Claude CLI"),
        ("git", "git"),
        ("gh", "GitHub CLI (for PRs)"),
        ("python3", "python3"),
    ]:
        ok, msg = check_binary(binary, desc)
        results.append((ok, "Binary", msg))

    # --- Configuration ---
    try:
        from anchovies import config
        # Slack tokens (format check only)
        if config.SLACK_BOT_TOKEN and config.SLACK_BOT_TOKEN.startswith("xoxb-"):
            results.append((True, "Config", "SLACK_BOT_TOKEN: set (xoxb-...)"))
        elif config.SLACK_BOT_TOKEN:
            results.append((False, "Config", "SLACK_BOT_TOKEN: set but wrong prefix"))
        else:
            results.append((False, "Config", "SLACK_BOT_TOKEN: NOT SET"))

        if config.SLACK_APP_TOKEN and config.SLACK_APP_TOKEN.startswith("xapp-"):
            results.append((True, "Config", "SLACK_APP_TOKEN: set (xapp-...)"))
        elif config.SLACK_APP_TOKEN:
            results.append((False, "Config", "SLACK_APP_TOKEN: set but wrong prefix"))
        else:
            results.append((False, "Config", "SLACK_APP_TOKEN: NOT SET"))

        # Paths
        ok, msg = check_path_exists(config.PROFILES_DIR, "Profiles directory")
        results.append((ok, "Config", msg))
        if ok:
            ok2, msg2 = check_path_has_files(config.PROFILES_DIR, "profile_*.yaml", "Profile files")
            results.append((ok2, "Config", msg2))

        ok, msg = check_path_exists(config.CONTEXT_BASE, "Context base")
        results.append((ok, "Config", msg))
    except Exception as e:
        results.append((False, "Config", f"Failed to load config: {e}"))

    # --- SQLite database ---
    try:
        from anchovies.storage import get_storage
        storage = get_storage()
        # Quick read test
        storage.get_budget()
        db_path = storage.db_path
        results.append((True, "Storage", f"SQLite database: {db_path}"))
    except Exception as e:
        results.append((False, "Storage", f"SQLite database: {e}"))

    # --- tmux session ---
    import subprocess
    tmux_check = subprocess.run(
        ["tmux", "has-session", "-t", "anchovies"],
        capture_output=True,
    )
    if tmux_check.returncode == 0:
        results.append((True, "Runtime", "tmux session 'anchovies': running"))
    else:
        results.append((False, "Runtime", "tmux session 'anchovies': NOT running"))

    # --- Projects ---
    try:
        from anchovies.project_registry import get_project_registry
        registry = get_project_registry()
        projects = registry.list_projects()
        if projects:
            names = ", ".join(p.name for p in projects)
            results.append((True, "Projects", f"Registered: {names}"))
        else:
            results.append((True, "Projects", "No projects registered (legacy mode)"))
    except Exception as e:
        results.append((False, "Projects", f"Registry error: {e}"))

    return results


def main() -> int:
    """Run health checks and print results."""
    print("Anchovies Health Check")
    print("=" * 60)

    results = run_checks()
    passed = sum(1 for ok, _, _ in results if ok)
    failed = sum(1 for ok, _, _ in results if not ok)

    current_category = ""
    for ok, category, message in results:
        if category != current_category:
            print(f"\n  [{category}]")
            current_category = category
        icon = "OK" if ok else "FAIL"
        print(f"    [{icon:>4}] {message}")

    print()
    print(f"Results: {passed} passed, {failed} failed")

    if failed:
        print("\nFix the FAIL items above before starting the bot.")
        return 1

    print("\nAll checks passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
