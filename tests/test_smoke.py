"""Minimal smoke test to verify the test framework works."""


def test_framework_works():
    """pytest can discover and run tests."""
    assert True


def test_fixtures_load(sample_profiles_dir):
    """Sample profile fixtures create YAML files."""
    profiles = list(sample_profiles_dir.glob("*.yaml"))
    assert len(profiles) == 3
    names = {p.stem.replace("profile_", "") for p in profiles}
    assert names == {"marcus", "sofia", "kai"}


def test_mock_slack_client(mock_slack_client):
    """Mock Slack client captures posted messages."""
    mock_slack_client.chat_postMessage(channel="C123", text="hello")
    assert len(mock_slack_client.posted_messages) == 1
    assert mock_slack_client.posted_messages[0]["text"] == "hello"


def test_mock_tmux(mock_tmux):
    """Mock tmux manager tracks tabs."""
    assert mock_tmux.session_exists()
    assert mock_tmux.spawn_persona_tab("sofia", "test prompt")
    assert mock_tmux.persona_tab_exists("sofia")
    assert mock_tmux.list_active_tabs() == ["sofia"]
    assert mock_tmux.close_persona_tab("sofia")
    assert mock_tmux.list_active_tabs() == []
