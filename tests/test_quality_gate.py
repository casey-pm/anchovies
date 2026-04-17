"""Tests for quality gate (Kai auto-review)."""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anchovies.quality_gate import (
    MAX_ITERATIONS,
    PASS_THRESHOLD,
    ReviewResult,
    build_review_prompt,
    format_review_for_slack,
    get_diff,
    is_code_task,
    parse_review_response,
    run_review,
)
from anchovies.storage import Storage, reset_storage


# ---------------------------------------------------------------------------
# parse_review_response
# ---------------------------------------------------------------------------


SAMPLE_PASS = """SCORE: 85
VERDICT: PASS
ISSUES:
- None
SUGGESTIONS:
- Consider adding a docstring to the new function"""

SAMPLE_FAIL = """SCORE: 55
VERDICT: FAIL
ISSUES:
- Missing zero-division check in divide()
- No test coverage for the new multiply fix
SUGGESTIONS:
- Add pytest test for multiply(3, 4) == 12
- Add try/except in divide()"""

SAMPLE_MINIMAL = """SCORE: 90
VERDICT: PASS
ISSUES: None
SUGGESTIONS: None"""


class TestParseReviewResponse:
    def test_parse_pass(self):
        result = parse_review_response(SAMPLE_PASS)
        assert result.score == 85
        assert result.verdict == "PASS"
        assert result.issues == []
        assert len(result.suggestions) == 1

    def test_parse_fail(self):
        result = parse_review_response(SAMPLE_FAIL)
        assert result.score == 55
        assert result.verdict == "FAIL"
        assert len(result.issues) == 2
        assert "zero-division" in result.issues[0]
        assert len(result.suggestions) == 2

    def test_parse_minimal(self):
        result = parse_review_response(SAMPLE_MINIMAL)
        assert result.score == 90
        assert result.verdict == "PASS"
        assert result.issues == []
        assert result.suggestions == []

    def test_parse_no_score_defaults_zero(self):
        result = parse_review_response("Some text without proper format")
        assert result.score == 0

    def test_verdict_inferred_from_score(self):
        """If VERDICT line is missing, infer from score."""
        result = parse_review_response("SCORE: 90\nISSUES: None\nSUGGESTIONS: None")
        assert result.verdict == "PASS"

        result = parse_review_response("SCORE: 50\nISSUES: None\nSUGGESTIONS: None")
        assert result.verdict == "FAIL"

    def test_iteration_preserved(self):
        result = parse_review_response(SAMPLE_PASS, iteration=3)
        assert result.iteration == 3


# ---------------------------------------------------------------------------
# build_review_prompt
# ---------------------------------------------------------------------------


class TestBuildReviewPrompt:
    def test_includes_diff(self):
        prompt = build_review_prompt("+ new line\n- old line", "fix bug")
        assert "+ new line" in prompt
        assert "- old line" in prompt

    def test_includes_task(self):
        prompt = build_review_prompt("diff", "fix the multiply function")
        assert "fix the multiply function" in prompt

    def test_includes_kai_identity(self):
        prompt = build_review_prompt("diff", "task")
        assert "Kai" in prompt
        assert "Optimizer" in prompt

    def test_includes_scoring_criteria(self):
        prompt = build_review_prompt("diff", "task")
        assert "Correctness" in prompt
        assert "Safety" in prompt

    def test_includes_previous_review_on_iteration_2(self):
        prompt = build_review_prompt("diff", "task", iteration=2, previous_review="Fix the null check")
        assert "Previous Review" in prompt
        assert "Fix the null check" in prompt

    def test_truncates_large_diff(self):
        large_diff = "x" * 20000
        prompt = build_review_prompt(large_diff, "task")
        assert "truncated" in prompt
        assert len(prompt) < 15000


# ---------------------------------------------------------------------------
# run_review (mocked CLI)
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_storage(tmp_path, monkeypatch):
    reset_storage()
    storage = Storage(tmp_path / "test.db")
    import anchovies.storage as sm
    monkeypatch.setattr(sm, "_storage", storage)
    yield storage
    storage.close()
    reset_storage()


class TestRunReview:
    @pytest.mark.anyio
    async def test_pass_review(self, fresh_storage):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = SAMPLE_PASS
            result = await run_review("+ new code", "fix bug")

        assert result.score == 85
        assert result.verdict == "PASS"

    @pytest.mark.anyio
    async def test_fail_review(self, fresh_storage):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = SAMPLE_FAIL
            result = await run_review("+ bad code", "fix bug")

        assert result.score == 55
        assert result.verdict == "FAIL"

    @pytest.mark.anyio
    async def test_empty_diff_auto_passes(self, fresh_storage):
        result = await run_review("", "no changes")
        assert result.verdict == "PASS"
        assert result.score == 100

    @pytest.mark.anyio
    async def test_review_logged_to_audit(self, fresh_storage):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = SAMPLE_PASS
            await run_review("+ code", "fix bug")

        events = fresh_storage.query_audit(event_type="code_review")
        assert len(events) == 1
        assert events[0].member == "kai"
        assert events[0].details["score"] == 85

    @pytest.mark.anyio
    async def test_cli_failure_returns_error(self, fresh_storage):
        with patch("anchovies.cli_runner.run_claude_cli", new_callable=AsyncMock) as mock_cli:
            mock_cli.side_effect = Exception("CLI down")
            result = await run_review("+ code", "fix bug")

        assert result.verdict == "ERROR"


# ---------------------------------------------------------------------------
# format_review_for_slack
# ---------------------------------------------------------------------------


class TestFormatForSlack:
    def test_pass_format(self):
        result = ReviewResult(score=85, verdict="PASS", issues=[], suggestions=["Add docstring"],
                              raw_response="", iteration=1)
        text = format_review_for_slack(result)
        assert ":white_check_mark:" in text
        assert "85/100" in text
        assert "PASS" in text

    def test_fail_format_with_respawn(self):
        result = ReviewResult(score=55, verdict="FAIL", issues=["Missing tests"],
                              suggestions=[], raw_response="", iteration=1)
        text = format_review_for_slack(result)
        assert ":x:" in text
        assert "re-spawned" in text
        assert "iteration 2" in text

    def test_fail_at_max_iterations_escalates(self):
        result = ReviewResult(score=40, verdict="FAIL", issues=["Still broken"],
                              suggestions=[], raw_response="", iteration=MAX_ITERATIONS)
        text = format_review_for_slack(result)
        assert "Escalating" in text or "Casey" in text
        assert "manual review" in text.lower()


# ---------------------------------------------------------------------------
# is_code_task
# ---------------------------------------------------------------------------


class TestIsCodeTask:
    def test_code_by_file_extension(self):
        assert is_code_task("update something", files=["app.py"]) is True
        assert is_code_task("update something", files=["handler.js"]) is True
        assert is_code_task("update something", files=["query.sql"]) is True

    def test_not_code_by_extension(self):
        assert is_code_task("update docs", files=["README.md"]) is False
        assert is_code_task("update config", files=["config.yaml"]) is False

    def test_code_by_keyword(self):
        assert is_code_task("fix the bug in the processor") is True
        assert is_code_task("implement the new feature") is True
        assert is_code_task("write test for authentication") is True
        assert is_code_task("refactor the data pipeline") is True

    def test_not_code_by_keyword(self):
        assert is_code_task("update the project documentation") is False
        assert is_code_task("what's the status?") is False

    def test_no_files_no_keywords(self):
        assert is_code_task("hello") is False


# ---------------------------------------------------------------------------
# get_diff (real git repo)
# ---------------------------------------------------------------------------


class TestGetDiff:
    def test_diff_on_clean_branch(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
        (repo / "file.py").write_text("# initial\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

        # Create branch with a change
        subprocess.run(["git", "checkout", "-b", "test/branch"], cwd=repo, check=True)
        (repo / "file.py").write_text("# changed\ndef hello(): pass\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=repo, check=True)

        diff = get_diff(repo)
        assert "def hello" in diff
        assert "+" in diff

    def test_no_diff_on_same_branch(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
        (repo / "file.py").write_text("# initial\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

        diff = get_diff(repo)
        assert diff.strip() == ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_pass_threshold(self):
        assert PASS_THRESHOLD == 80

    def test_max_iterations(self):
        assert MAX_ITERATIONS == 3
