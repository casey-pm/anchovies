"""
Shared test fixtures for the Anchovies test suite.

Provides mocks for external dependencies so tests run without
Slack, tmux, Claude CLI, or network access.
"""

import os
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Ensure anchovies package is importable
ANCHOVIES_DIR = Path(__file__).parent.parent
if str(ANCHOVIES_DIR.parent) not in sys.path:
    sys.path.insert(0, str(ANCHOVIES_DIR.parent))


# ---------------------------------------------------------------------------
# Sample profile data
# ---------------------------------------------------------------------------

SAMPLE_PROFILES = {
    "marcus": {
        "name": "Marcus",
        "nickname": "Boss",
        "role": "BI Manager",
        "avatar_emoji": ":bust_in_silhouette:",
        "job_summary": "Lead the BI team.\n\nKey Skills: SQL, Python, leadership",
        "personality": {
            "communication_style": "direct",
            "formality": "professional-casual",
            "verbosity": "concise",
        },
        "traits": ["Decisive", "Action-oriented"],
        "speech_patterns": ["What's the blocker?", "Let's keep this moving"],
        "expertise": ["Project management", "Stakeholder communication"],
        "handles_topics": ["project", "priority", "blocker"],
        "relationships": {"reports_to": "VP/Director"},
    },
    "sofia": {
        "name": "Sofia",
        "nickname": "dbt Queen",
        "role": "Analytics Engineer",
        "avatar_emoji": ":woman-running:",
        "job_summary": "Build and maintain dbt transformation layer.\n\nKey Skills: dbt, SQL, data modeling",
        "personality": {
            "communication_style": "collaborative",
            "formality": "professional-casual",
            "verbosity": "moderate",
        },
        "traits": ["Methodical", "Patient teacher"],
        "speech_patterns": ["Let me check the dbt model..."],
        "expertise": ["dbt", "SQL optimization", "Data modeling"],
        "handles_topics": ["dbt", "sql", "transformation"],
        "relationships": {"reports_to": "Marcus"},
    },
    "kai": {
        "name": "Kai",
        "nickname": "The Optimizer",
        "role": "Code Quality Engineer",
        "avatar_emoji": ":zap:",
        "job_summary": "Code quality, reviews, and standards.\n\nKey Skills: Python, testing, CI/CD",
        "personality": {
            "communication_style": "precise",
            "formality": "professional",
            "verbosity": "moderate",
        },
        "traits": ["Detail-oriented", "Standards-driven"],
        "speech_patterns": ["Let me review that..."],
        "expertise": ["Code review", "Testing", "Python best practices"],
        "handles_topics": ["code quality", "review", "testing"],
        "relationships": {"reports_to": "Marcus"},
    },
}

SAMPLE_MEMORY = {
    "marcus": "## Lesson 1: Always verify before acting\nTest changes before marking complete.",
    "sofia": "## Lesson 1: dbt tests are non-negotiable\nAlways add tests for new models.",
    "kai": "",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_profiles_dir(tmp_path):
    """Create a temp directory with sample YAML profile files."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    for name, data in SAMPLE_PROFILES.items():
        profile_path = profiles_dir / f"profile_{name}.yaml"
        profile_path.write_text(yaml.dump(data, default_flow_style=False))
    return profiles_dir


@pytest.fixture
def sample_memory_dir(tmp_path):
    """Create a temp directory with sample memory files."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    for name, content in SAMPLE_MEMORY.items():
        mem_path = memory_dir / f"memory_{name}.md"
        mem_path.write_text(content)
    return memory_dir


@pytest.fixture
def sample_status_dir(tmp_path):
    """Create a temp directory with sample status files."""
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    for name in SAMPLE_PROFILES:
        status_path = status_dir / f"status_{name}.md"
        status_path.write_text(f"# Status: {name.title()}\n\nNo active tasks.")
    return status_dir


@pytest.fixture
def mock_slack_client():
    """
    Mock Slack WebClient that captures posted messages.

    Usage:
        client = mock_slack_client
        client.chat_postMessage(channel="C123", text="hello")
        assert client.posted_messages[-1]["text"] == "hello"
    """
    client = MagicMock()
    client.posted_messages = []
    client.updated_messages = []

    def capture_post(**kwargs):
        client.posted_messages.append(kwargs)
        return {"ok": True, "ts": f"1234567890.{len(client.posted_messages):06d}", "channel": kwargs.get("channel", "C000")}

    def capture_update(**kwargs):
        client.updated_messages.append(kwargs)
        return {"ok": True}

    def capture_auth_test():
        return {"ok": True, "user_id": "U_BOT_TEST"}

    client.chat_postMessage = MagicMock(side_effect=capture_post)
    client.chat_update = MagicMock(side_effect=capture_update)
    client.auth_test = MagicMock(side_effect=capture_auth_test)

    return client


@pytest.fixture
def mock_tmux():
    """
    Mock TmuxManager that simulates tmux operations without actual tmux.

    Tracks spawned tabs, closed tabs, and pane content.
    """
    tmux = MagicMock()
    tmux._active_tabs = {}
    tmux._session_exists = True

    def session_exists():
        return tmux._session_exists

    def spawn_persona_tab(member, task_prompt, working_dir=None):
        if member in tmux._active_tabs:
            return False
        tmux._active_tabs[member] = {
            "prompt": task_prompt,
            "working_dir": working_dir,
            "alive": True,
        }
        return True

    def persona_tab_exists(member):
        return member in tmux._active_tabs

    def close_persona_tab(member):
        if member in tmux._active_tabs:
            del tmux._active_tabs[member]
            return True
        return False

    def list_active_tabs():
        return list(tmux._active_tabs.keys())

    def get_pane_content(pane_target, lines=50):
        return "> "  # Default: Claude is ready

    def get_pane_command(pane_target):
        return "claude"  # Default: Claude is running

    tmux.session_exists = MagicMock(side_effect=session_exists)
    tmux.spawn_persona_tab = MagicMock(side_effect=spawn_persona_tab)
    tmux.persona_tab_exists = MagicMock(side_effect=persona_tab_exists)
    tmux.close_persona_tab = MagicMock(side_effect=close_persona_tab)
    tmux.list_active_tabs = MagicMock(side_effect=list_active_tabs)
    tmux.get_pane_content = MagicMock(side_effect=get_pane_content)
    tmux._get_pane_command = MagicMock(side_effect=get_pane_command)
    tmux.send_to_work_pane = MagicMock(return_value=True)
    tmux.send_to_chat_pane = MagicMock(return_value=True)

    return tmux


@pytest.fixture
def mock_claude_cli():
    """
    Mock for Claude CLI runner that returns canned responses.

    Usage:
        mock_claude_cli.response = "Custom response"
        result = await run_claude_cli("prompt")
        assert result == "Custom response"
    """
    mock = AsyncMock()
    mock.response = "This is a mock Claude response."
    mock.call_log = []

    async def fake_run(prompt, timeout=120.0):
        mock.call_log.append({"prompt": prompt, "timeout": timeout})
        if mock.should_fail:
            from anchovies.cli_runner import ClaudeCliError
            raise ClaudeCliError("Mock CLI failure")
        return mock.response

    mock.should_fail = False
    mock.side_effect = fake_run

    return mock


@pytest.fixture
def clean_conversation_store():
    """
    Reset the conversation store between tests.

    Patches handlers.conversation_store and handlers.chain_depth
    to fresh dicts so tests don't leak state.
    """
    with patch("anchovies.handlers.conversation_store", {}), \
         patch("anchovies.handlers.chain_depth", {}):
        yield


@pytest.fixture
def anchovies_config(tmp_path, sample_profiles_dir, sample_memory_dir, sample_status_dir):
    """
    Patch anchovies.config with test-safe paths and values.

    Points all directory configs to temp dirs with sample data.
    """
    context_base = tmp_path / "context"
    context_base.mkdir()
    # Create status subdir in context_base (where config expects it)
    status_in_context = context_base / "status"
    status_in_context.mkdir()
    for f in sample_status_dir.iterdir():
        (status_in_context / f.name).write_text(f.read_text())

    patches = {
        "anchovies.config.SLACK_BOT_TOKEN": "xoxb-test-token",
        "anchovies.config.SLACK_APP_TOKEN": "xapp-test-token",
        "anchovies.config.SLACK_SIGNING_SECRET": "test-secret",
        "anchovies.config.SLACK_CHANNEL_ID": "C_TEST_CHANNEL",
        "anchovies.config.BOT_DIR": Path(ANCHOVIES_DIR),
        "anchovies.config.PROJECT_ROOT": ANCHOVIES_DIR.parent,
        "anchovies.config.CONTEXT_BASE": context_base,
        "anchovies.config.PROFILES_DIR": sample_profiles_dir,
        "anchovies.config.TEAM_MEMBERS": ["marcus", "sofia", "kai"],
        "anchovies.config.MEMBER_ALIASES": {
            "boss": "marcus",
            "dbt queen": "sofia",
            "the optimizer": "kai",
        },
        "anchovies.config.DEFAULT_MEMBER": "marcus",
        "anchovies.config.CHAT_HUB_PERSONA": "marcus",
    }

    with patch.multiple("anchovies.config", **{k.split(".")[-1]: v for k, v in patches.items()}):
        yield patches
