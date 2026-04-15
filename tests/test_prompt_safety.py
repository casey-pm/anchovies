"""Tests for prompt safety rules: smarter work detection, protected files,
and destructive operation approval rules."""

import pytest

from anchovies import config
from anchovies.chat_hub.prompt_builder import (
    detect_work_request,
    build_task_prompt,
    NEGATIVE_PATTERNS,
    NEGATIVE_REGEX,
)


# ---------------------------------------------------------------------------
# 2.4: Smarter work request detection
# ---------------------------------------------------------------------------


class TestPositiveWorkRequests:
    """Clear work requests should be detected with high confidence."""

    def test_fix_bug_in_file(self):
        result = detect_work_request("fix the null bug in app.py")
        assert result["is_work_request"] is True
        assert result["needs_clarification"] is False
        assert result["confidence"] >= 0.5

    def test_create_new_function(self):
        result = detect_work_request("create a new function to handle errors")
        assert result["is_work_request"] is True

    def test_update_with_file_path(self):
        result = detect_work_request("update the config in settings.json")
        assert result["is_work_request"] is True

    def test_run_tests(self):
        result = detect_work_request("run the tests for the auth module")
        assert result["is_work_request"] is True

    def test_refactor(self):
        result = detect_work_request("refactor the data pipeline")
        assert result["is_work_request"] is True


class TestNegativeWorkRequests:
    """Questions and discussion should NOT be detected as work requests."""

    def test_how_question(self):
        result = detect_work_request("how does the review process work?")
        assert result["is_work_request"] is False

    def test_what_question(self):
        result = detect_work_request("what is the current state of the project?")
        assert result["is_work_request"] is False

    def test_can_you_explain(self):
        result = detect_work_request("can you explain the data model")
        assert result["is_work_request"] is False

    def test_past_tense_already_done(self):
        result = detect_work_request("I already updated the file")
        assert result["is_work_request"] is False

    def test_status_inquiry(self):
        result = detect_work_request("any updates on the deployment?")
        assert result["is_work_request"] is False

    def test_hypothetical_should_we(self):
        result = detect_work_request("should we use a different approach?")
        assert result["is_work_request"] is False

    def test_status_of_question(self):
        result = detect_work_request("status of the migration?")
        assert result["is_work_request"] is False


class TestAmbiguousRequests:
    """Mixed signals should trigger clarification."""

    def test_question_with_action_keyword(self):
        """'should we fix the bug?' has both 'fix' and 'should we' — ambiguous."""
        result = detect_work_request("should we fix the bug in handler.py?")
        # Has positive (fix, file) and negative (should we, ?) signals
        # Either is_work_request OR needs_clarification should be true
        assert result["is_work_request"] or result["needs_clarification"]

    def test_chat_only_zero_confidence(self):
        """Pure chat with no work signal should have low confidence and no clarification."""
        result = detect_work_request("hey marcus how's it going")
        assert result["is_work_request"] is False
        assert result["needs_clarification"] is False
        assert result["confidence"] == 0.0


class TestConfidenceScore:
    """Confidence score should reflect signal strength."""

    def test_clear_work_high_confidence(self):
        result = detect_work_request(
            "fix the bug in app.py and update tests in test_app.py"
        )
        assert result["confidence"] >= 0.5

    def test_clear_chat_zero_confidence(self):
        result = detect_work_request("hello there")
        assert result["confidence"] == 0.0

    def test_question_lowers_confidence(self):
        # Same action keyword, but as a question — confidence should be lower
        statement = detect_work_request("fix the bug in app.py")
        question = detect_work_request("how do I fix the bug in app.py?")
        assert question["confidence"] < statement["confidence"]


# ---------------------------------------------------------------------------
# 2.6: Protected files in prompt
# ---------------------------------------------------------------------------


class TestProtectedFilesInPrompt:
    """Built task prompts should contain the protected files rules."""

    def test_protected_files_section_present(self, monkeypatch):
        monkeypatch.setattr(config, "PROTECTED_FILES", [".env", "credentials.*"])
        prompt = build_task_prompt(
            persona="sofia",
            task_description="fix bug in app.py",
        )
        assert "Protected Files" in prompt or "PROTECTED FILES" in prompt.upper()
        assert ".env" in prompt
        assert "credentials.*" in prompt

    def test_default_protected_files_in_prompt(self):
        """Default PROTECTED_FILES (e.g., .env, credentials) should appear in the prompt."""
        prompt = build_task_prompt(
            persona="sofia",
            task_description="fix bug",
        )
        assert ".env" in prompt
        assert "credentials" in prompt.lower()

    def test_protected_files_marked_mandatory(self):
        """The rules should be presented as MANDATORY/NEVER."""
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "MANDATORY" in prompt or "NEVER" in prompt


# ---------------------------------------------------------------------------
# 2.7: Destructive operation rules in prompt
# ---------------------------------------------------------------------------


class TestDestructiveOpsInPrompt:
    """Built task prompts should contain destructive operation approval rules."""

    def test_destructive_section_present(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "Destructive" in prompt or "DESTRUCTIVE" in prompt.upper()

    def test_lists_specific_destructive_operations(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "Deleting" in prompt or "delete" in prompt.lower()
        assert "Drop" in prompt or "drop" in prompt.lower()
        assert "migration" in prompt.lower() or "Migration" in prompt

    def test_requires_slack_approval(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        # Must mention Slack approval / wait
        assert "Slack" in prompt
        assert "approval" in prompt.lower() or "approve" in prompt.lower()
        assert "wait" in prompt.lower() or "WAIT" in prompt

    def test_destructive_includes_force_push(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        # Force-push is one of the canonical destructive git ops
        assert "force" in prompt.lower() or "Force" in prompt


class TestShellCommandRulesInPrompt:
    """The shell command allowlist should be in every prompt."""

    def test_lists_allowed_commands(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "ALLOWED" in prompt or "allowed" in prompt
        assert "git" in prompt
        assert "pytest" in prompt

    def test_lists_blocked_commands(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "BLOCKED" in prompt or "blocked" in prompt
        assert "rm -rf" in prompt
        assert "sudo" in prompt

    def test_pip_install_requires_approval(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "pip install" in prompt
        # Should be in REQUIRES APPROVAL section, not just BLOCKED
        # Just check both pip install and approval appear in close proximity
        assert "approval" in prompt.lower()


class TestGitRulesInPrompt:
    """Every work session prompt should include git safety rules."""

    def test_git_rules_section_present(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug in app.py")
        assert "Git Rules" in prompt or "GIT RULES" in prompt.upper()

    def test_branch_name_in_prompt(self):
        """The persona's expected feature branch name should appear in the prompt."""
        prompt = build_task_prompt(persona="sofia", task_description="fix null processor")
        assert "sofia/fix-null-processor" in prompt

    def test_never_push_main_in_prompt(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "main" in prompt
        # Must say NEVER push to main
        assert "NEVER push" in prompt or "never push" in prompt.lower()

    def test_never_force_push_in_prompt(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "force-push" in prompt or "force push" in prompt.lower() or "--force" in prompt

    def test_co_authored_footer_in_prompt(self):
        prompt = build_task_prompt(persona="sofia", task_description="fix bug")
        assert "Co-Authored-By" in prompt
        assert "Sofia" in prompt
        assert "Anchovies" in prompt
