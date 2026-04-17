"""
Quality Gate — Kai auto-reviews code changes before PR creation.

After a code-changing work session completes:
  1. Extract the diff (git diff main...<branch>)
  2. Kai reviews and scores 0-100
  3. PASS (80+): proceed to PR creation
  4. FAIL (<80): post review to Slack, re-spawn persona with feedback
  5. Max 3 iterations, then escalate to Casey

Inspired by BlackTeam's quality gate system with score thresholds
and iteration caps.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger(__name__)

PASS_THRESHOLD = 80
MAX_ITERATIONS = 3


@dataclass
class ReviewResult:
    """Result of Kai's code review."""
    score: int
    verdict: str  # "PASS" or "FAIL"
    issues: list[str]
    suggestions: list[str]
    raw_response: str
    iteration: int


def get_diff(repo_path: Path | str, base_branch: str = "main") -> str:
    """Get the diff of the current branch vs base branch."""
    result = subprocess.run(
        ["git", "diff", f"{base_branch}..HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f"git diff failed: {result.stderr}")
        return ""
    return result.stdout


def build_review_prompt(
    diff: str,
    task_description: str,
    iteration: int = 1,
    previous_review: Optional[str] = None,
) -> str:
    """Build Kai's review prompt."""
    iteration_note = ""
    if iteration > 1 and previous_review:
        iteration_note = f"""
## Previous Review (iteration {iteration - 1})
The persona was asked to fix these issues:
{previous_review}

Check whether the issues were addressed.
"""

    # Truncate diff if very large (keep first 10K chars)
    if len(diff) > 10000:
        diff = diff[:10000] + "\n\n... (diff truncated, showing first 10K chars)"

    return f"""You are Kai ("The Optimizer"), Code Quality Engineer.

Review this code diff and score it 0-100.
{iteration_note}
## Task Description
{task_description}

## Scoring Criteria
- **Correctness** (0-30): Does it do what the task asked?
- **Code Quality** (0-25): Clean, readable, no obvious bugs?
- **Test Coverage** (0-25): Are changes tested?
- **Safety** (0-20): No secrets, no destructive ops, no security issues?

## The Diff
```diff
{diff}
```

## Required Response Format
Respond with EXACTLY this format:
SCORE: <number 0-100>
VERDICT: <PASS or FAIL>
ISSUES:
- <issue 1>
- <issue 2>
SUGGESTIONS:
- <suggestion 1>
- <suggestion 2>

If there are no issues, write "ISSUES: None".
If there are no suggestions, write "SUGGESTIONS: None"."""


def parse_review_response(response: str, iteration: int = 1) -> ReviewResult:
    """Parse Kai's review response into a structured ReviewResult."""
    # Extract score
    score_match = re.search(r"SCORE:\s*(\d+)", response)
    score = int(score_match.group(1)) if score_match else 0

    # Extract verdict
    verdict_match = re.search(r"VERDICT:\s*(PASS|FAIL)", response, re.IGNORECASE)
    verdict = verdict_match.group(1).upper() if verdict_match else ("PASS" if score >= PASS_THRESHOLD else "FAIL")

    # Extract issues
    issues = []
    issues_match = re.search(r"ISSUES:\s*\n((?:- .+\n?)*)", response)
    if issues_match:
        for line in issues_match.group(1).strip().split("\n"):
            line = line.strip().lstrip("- ").strip()
            if line and line.lower() != "none":
                issues.append(line)

    # Extract suggestions
    suggestions = []
    suggestions_match = re.search(r"SUGGESTIONS:\s*\n((?:- .+\n?)*)", response)
    if suggestions_match:
        for line in suggestions_match.group(1).strip().split("\n"):
            line = line.strip().lstrip("- ").strip()
            if line and line.lower() != "none":
                suggestions.append(line)

    return ReviewResult(
        score=score,
        verdict=verdict,
        issues=issues,
        suggestions=suggestions,
        raw_response=response,
        iteration=iteration,
    )


async def run_review(
    diff: str,
    task_description: str,
    iteration: int = 1,
    previous_review: Optional[str] = None,
) -> ReviewResult:
    """
    Run Kai's code review on a diff.

    Args:
        diff: The git diff to review
        task_description: What the task was supposed to do
        iteration: Current review iteration (1-based)
        previous_review: Previous review feedback (for iteration > 1)

    Returns:
        ReviewResult with score, verdict, issues, suggestions
    """
    if not diff.strip():
        return ReviewResult(
            score=100,
            verdict="PASS",
            issues=[],
            suggestions=["No changes detected — nothing to review"],
            raw_response="No diff",
            iteration=iteration,
        )

    prompt = build_review_prompt(diff, task_description, iteration, previous_review)

    try:
        from .cli_runner import run_claude_cli
        response = await run_claude_cli(prompt, model=config.WORK_MODEL)
        result = parse_review_response(response, iteration)

        # Log the review
        try:
            from .storage import get_storage
            get_storage().log_event(
                "code_review",
                member="kai",
                details={
                    "score": result.score,
                    "verdict": result.verdict,
                    "iteration": iteration,
                    "issues_count": len(result.issues),
                    "task": task_description[:100],
                },
            )
        except Exception:
            pass

        return result

    except Exception as e:
        logger.error(f"Code review failed: {e}")
        # On failure, return a passing result so we don't block the workflow
        return ReviewResult(
            score=0,
            verdict="ERROR",
            issues=[f"Review failed: {e}"],
            suggestions=[],
            raw_response=str(e),
            iteration=iteration,
        )


def format_review_for_slack(result: ReviewResult) -> str:
    """Format a review result for posting to Slack."""
    icon = ":white_check_mark:" if result.verdict == "PASS" else ":x:"
    lines = [
        f"{icon} *Kai's Code Review (iteration {result.iteration})*",
        f"Score: *{result.score}/100* — {result.verdict}",
    ]

    if result.issues:
        lines.append("\n*Issues:*")
        for issue in result.issues:
            lines.append(f"  - {issue}")

    if result.suggestions:
        lines.append("\n*Suggestions:*")
        for suggestion in result.suggestions:
            lines.append(f"  - {suggestion}")

    if result.verdict == "FAIL" and result.iteration < MAX_ITERATIONS:
        lines.append(f"\n_Persona will be re-spawned to address issues (iteration {result.iteration + 1}/{MAX_ITERATIONS})_")
    elif result.verdict == "FAIL" and result.iteration >= MAX_ITERATIONS:
        lines.append(f"\n:warning: *Max iterations ({MAX_ITERATIONS}) reached. Escalating to Casey for manual review.*")

    return "\n".join(lines)


def is_code_task(task_description: str, files: list[str] | None = None) -> bool:
    """
    Determine if a task involves code changes (vs docs/analysis).

    Code tasks get quality-gated. Doc/analysis tasks skip the gate.
    """
    code_extensions = {".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".sql", ".sh"}
    if files:
        for f in files:
            for ext in code_extensions:
                if f.endswith(ext):
                    return True

    code_keywords = [
        "fix", "bug", "implement", "create function", "refactor",
        "add feature", "update code", "write test", "debug",
    ]
    desc_lower = task_description.lower()
    return any(kw in desc_lower for kw in code_keywords)
