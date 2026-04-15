"""Tests for git safety: branch isolation, dangerous command detection, PR creation."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anchovies.git_safety import (
    branch_exists,
    branch_name,
    create_feature_branch,
    create_pr,
    current_branch,
    DANGEROUS_COMMAND_PATTERNS,
    is_dangerous_command,
    list_changed_files,
    PROTECTED_BRANCHES,
    PRResult,
    slugify,
)


# ---------------------------------------------------------------------------
# Slug + branch naming
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic_text(self):
        assert slugify("fix the null bug") == "fix-the-null-bug"

    def test_strips_punctuation(self):
        assert slugify("update API: handle errors!") == "update-api-handle-errors"

    def test_collapses_whitespace(self):
        assert slugify("hello   world\t\n  test") == "hello-world-test"

    def test_lowercases(self):
        assert slugify("Fix Bug") == "fix-bug"

    def test_strips_leading_trailing_hyphens(self):
        assert slugify("  --hello--  ") == "hello"

    def test_truncates_long_input(self):
        long_text = "x" * 100
        result = slugify(long_text, max_length=20)
        assert len(result) <= 20

    def test_truncate_does_not_end_with_hyphen(self):
        # Construct a string where truncation would leave a trailing hyphen
        result = slugify("aaaaa-bbbbb-ccccc-ddddd-eeeee", max_length=11)
        assert not result.endswith("-")

    def test_fallback_when_all_symbols(self):
        assert slugify("!!!") == "task"
        assert slugify("") == "task"

    def test_filename_extensions_become_hyphens(self):
        assert slugify("fix bug in app.py") == "fix-bug-in-app-py"

    def test_unicode_replaced(self):
        # Unicode letters get replaced with hyphens; this is fine.
        result = slugify("café update")
        # No specific assertion on the exact output, just that it's a valid slug
        assert all(c.isalnum() or c == "-" for c in result)
        assert result  # not empty


class TestBranchName:
    def test_format(self):
        assert branch_name("sofia", "fix null processor") == "sofia/fix-null-processor"

    def test_persona_lowercased(self):
        assert branch_name("Sofia", "Fix Bug") == "sofia/fix-bug"

    def test_long_task_truncated(self):
        long_task = "this is a very long task description that will need to be truncated for safety reasons because git branches have limits"
        result = branch_name("sofia", long_task)
        # Just verify it has the persona prefix and a non-empty slug
        assert result.startswith("sofia/")
        assert len(result) < 80


# ---------------------------------------------------------------------------
# Dangerous command detection
# ---------------------------------------------------------------------------


class TestIsDangerousCommand:
    def test_safe_commands_pass(self):
        safe_commands = [
            "git status",
            "git add .",
            "git commit -m 'fix'",
            "git push origin sofia/fix-bug",
            "git checkout -b sofia/new-task",
            "git log --oneline",
            "git diff",
            "ls -la",
            "pytest tests/",
        ]
        for cmd in safe_commands:
            result = is_dangerous_command(cmd)
            assert result.safe is True, f"Expected safe: {cmd}"

    def test_force_push_blocked(self):
        for cmd in [
            "git push --force",
            "git push origin --force",
            "git push -f origin sofia/x",
            "git push origin +sofia/x",
        ]:
            result = is_dangerous_command(cmd)
            assert result.safe is False, f"Expected dangerous: {cmd}"
            assert len(result.reasons) >= 1

    def test_push_to_main_blocked(self):
        for cmd in [
            "git push origin main",
            "git push origin master",
            "git push main",
        ]:
            result = is_dangerous_command(cmd)
            assert result.safe is False, f"Expected dangerous: {cmd}"

    def test_merge_blocked(self):
        """All merges are blocked inside sessions; merges happen via PR."""
        result = is_dangerous_command("git merge sofia/fix-bug")
        assert result.safe is False

    def test_hard_reset_blocked(self):
        result = is_dangerous_command("git reset --hard HEAD~1")
        assert result.safe is False

    def test_main_branch_deletion_blocked(self):
        for cmd in [
            "git branch -D main",
            "git branch -d master",
        ]:
            result = is_dangerous_command(cmd)
            assert result.safe is False, f"Expected dangerous: {cmd}"

    def test_filter_branch_blocked(self):
        result = is_dangerous_command("git filter-branch --tree-filter 'rm secret.txt'")
        assert result.safe is False

    def test_protected_branches_list_complete(self):
        """Make sure we cover the main protected branches."""
        assert "main" in PROTECTED_BRANCHES
        assert "master" in PROTECTED_BRANCHES
        assert "production" in PROTECTED_BRANCHES


# ---------------------------------------------------------------------------
# Git operations against a real temp repo
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    """Create a real temp git repo with a main branch and one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Test repo\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    return repo


class TestRealGitOperations:
    def test_current_branch(self, git_repo):
        assert current_branch(git_repo) == "main"

    def test_branch_exists_main(self, git_repo):
        assert branch_exists("main", git_repo) is True
        assert branch_exists("nonexistent", git_repo) is False

    def test_create_feature_branch_success(self, git_repo):
        success, branch = create_feature_branch(
            "sofia", "fix null bug", git_repo
        )
        assert success is True
        assert branch == "sofia/fix-null-bug"
        assert current_branch(git_repo) == "sofia/fix-null-bug"
        assert branch_exists("sofia/fix-null-bug", git_repo) is True

    def test_create_feature_branch_idempotent(self, git_repo):
        """Creating the same branch twice should checkout the existing one."""
        s1, b1 = create_feature_branch("sofia", "fix bug", git_repo)
        # Switch back to main
        subprocess.run(["git", "checkout", "main", "-q"], cwd=git_repo)
        # Call again — should checkout the existing branch
        s2, b2 = create_feature_branch("sofia", "fix bug", git_repo)
        assert s1 is True
        assert s2 is True
        assert b1 == b2
        assert current_branch(git_repo) == "sofia/fix-bug"

    def test_create_branch_with_missing_base_fails(self, git_repo):
        success, msg = create_feature_branch(
            "sofia", "task", git_repo, base_branch="develop"
        )
        assert success is False
        assert "develop" in msg

    def test_list_changed_files_empty_on_fresh_branch(self, git_repo):
        create_feature_branch("sofia", "task", git_repo)
        assert list_changed_files(git_repo) == []

    def test_list_changed_files_after_commit(self, git_repo):
        create_feature_branch("sofia", "task", git_repo)
        (git_repo / "new_file.py").write_text("# hello\n")
        subprocess.run(["git", "add", "new_file.py"], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add"], cwd=git_repo, check=True)
        assert list_changed_files(git_repo) == ["new_file.py"]


# ---------------------------------------------------------------------------
# PR creation (mocked gh CLI)
# ---------------------------------------------------------------------------


class TestCreatePR:
    def test_pr_creation_calls_gh(self, tmp_path):
        with patch("anchovies.git_safety.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/casey-pm/anchovies/pull/42\n",
                stderr="",
            )
            result = create_pr(
                persona="sofia",
                branch="sofia/fix-bug",
                task_description="fix the null bug",
                summary="Added null check on line 142",
                repo_path=tmp_path,
            )

        assert result.created is True
        assert result.url == "https://github.com/casey-pm/anchovies/pull/42"
        assert result.branch == "sofia/fix-bug"

        # Verify gh was called with the right shape
        call_args = mock_run.call_args[0][0]
        assert call_args[0:3] == ["gh", "pr", "create"]
        assert "--title" in call_args
        assert "--body" in call_args
        assert "--head" in call_args
        assert "sofia/fix-bug" in call_args

    def test_pr_creation_includes_persona_in_title(self, tmp_path):
        with patch("anchovies.git_safety.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="url\n", stderr=""
            )
            create_pr(
                persona="sofia",
                branch="sofia/x",
                task_description="fix bug",
                summary="s",
                repo_path=tmp_path,
            )
            call_args = mock_run.call_args[0][0]
            title_idx = call_args.index("--title")
            assert "Sofia" in call_args[title_idx + 1]

    def test_pr_creation_failure(self, tmp_path):
        with patch("anchovies.git_safety.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="gh: not authenticated",
            )
            result = create_pr(
                persona="sofia",
                branch="sofia/x",
                task_description="fix bug",
                summary="s",
                repo_path=tmp_path,
            )

        assert result.created is False
        assert "not authenticated" in result.error

    def test_pr_body_includes_review_checklist(self, tmp_path):
        with patch("anchovies.git_safety.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="url\n", stderr=""
            )
            create_pr(
                persona="sofia", branch="sofia/x",
                task_description="fix", summary="s",
                repo_path=tmp_path,
            )
            call_args = mock_run.call_args[0][0]
            body_idx = call_args.index("--body")
            body = call_args[body_idx + 1]
            assert "Review checklist" in body
            assert "Rebase" in body or "rebase" in body
            assert "Do not auto-merge" in body or "Do NOT" in body or "not auto-merge" in body
