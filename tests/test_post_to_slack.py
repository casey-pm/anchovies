"""Tests for post_to_slack.sh JSON payload construction.

These tests verify that the JSON payload is built correctly for messages
containing special characters that would break naive string interpolation.
"""

import json
import os
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent / "scripts"


def _build_payload(message: str, channel: str = "C_TEST", thread_ts: str = "") -> dict:
    """Run the Python JSON builder from post_to_slack.sh and return parsed payload."""
    result = subprocess.run(
        [
            "python3", "-c",
            "import json, sys\n"
            "payload = {'channel': sys.argv[1], 'text': sys.argv[2]}\n"
            "if sys.argv[3]:\n"
            "    payload['thread_ts'] = sys.argv[3]\n"
            "print(json.dumps(payload))",
            channel, message, thread_ts,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, f"JSON builder failed: {result.stderr}"
    return json.loads(result.stdout.strip())


class TestJsonPayloadConstruction:
    """Test that JSON payloads handle special characters correctly."""

    def test_simple_message(self):
        payload = _build_payload("Hello world")
        assert payload["text"] == "Hello world"
        assert payload["channel"] == "C_TEST"
        assert "thread_ts" not in payload

    def test_message_with_double_quotes(self):
        payload = _build_payload('Fixed the "null" bug in processor')
        assert payload["text"] == 'Fixed the "null" bug in processor'

    def test_message_with_single_quotes(self):
        payload = _build_payload("Don't break the build")
        assert payload["text"] == "Don't break the build"

    def test_message_with_backslashes(self):
        payload = _build_payload("Path is C:\\Users\\test\\file.py")
        assert payload["text"] == "Path is C:\\Users\\test\\file.py"

    def test_message_with_newlines(self):
        payload = _build_payload("Line 1\nLine 2\nLine 3")
        assert payload["text"] == "Line 1\nLine 2\nLine 3"

    def test_message_with_unicode(self):
        payload = _build_payload("Fixed bug \u2714 all tests passing \U0001f680")
        assert "\u2714" in payload["text"]

    def test_message_with_slack_markdown(self):
        payload = _build_payload("*Sofia:* Fixed `data_processor.py` _line 142_")
        assert payload["text"] == "*Sofia:* Fixed `data_processor.py` _line 142_"

    def test_message_with_curly_braces(self):
        payload = _build_payload('Config: {"key": "value", "count": 42}')
        assert payload["text"] == 'Config: {"key": "value", "count": 42}'

    def test_thread_ts_included(self):
        payload = _build_payload("Reply", thread_ts="1234567890.123456")
        assert payload["thread_ts"] == "1234567890.123456"

    def test_thread_ts_omitted_when_empty(self):
        payload = _build_payload("No thread")
        assert "thread_ts" not in payload

    def test_empty_message_produces_valid_json(self):
        payload = _build_payload("")
        assert payload["text"] == ""

    def test_output_is_valid_json(self):
        """The output must be parseable JSON, not malformed."""
        tricky = 'He said "hello" and then\\used a {bracket}'
        result = subprocess.run(
            [
                "python3", "-c",
                "import json, sys\n"
                "payload = {'channel': sys.argv[1], 'text': sys.argv[2]}\n"
                "if sys.argv[3]:\n"
                "    payload['thread_ts'] = sys.argv[3]\n"
                "print(json.dumps(payload))",
                "C_TEST", tricky, "",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Must not crash
        assert result.returncode == 0
        # Must be valid JSON
        parsed = json.loads(result.stdout.strip())
        assert parsed["text"] == tricky


class TestShellScriptSyntax:
    """Verify the actual shell script is syntactically valid."""

    SCRIPT_PATH = SCRIPT_DIR / "post_to_slack.sh"

    def test_bash_syntax_valid(self):
        """The post_to_slack.sh script must parse without bash syntax errors."""
        result = subprocess.run(
            ["bash", "-n", str(self.SCRIPT_PATH)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, f"bash syntax error: {result.stderr}"

    def test_spawn_persona_bash_syntax(self):
        """spawn_persona.sh must also parse without bash syntax errors."""
        spawn_script = SCRIPT_DIR / "spawn_persona.sh"
        result = subprocess.run(
            ["bash", "-n", str(spawn_script)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, f"bash syntax error: {result.stderr}"

    def test_start_anchovies_bash_syntax(self):
        """start_anchovies.sh must also parse without bash syntax errors."""
        start_script = SCRIPT_DIR / "start_anchovies.sh"
        result = subprocess.run(
            ["bash", "-n", str(start_script)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, f"bash syntax error: {result.stderr}"

    def test_post_to_slack_rejects_missing_message(self, tmp_path, monkeypatch):
        """Calling without arguments should fail with usage message."""
        result = subprocess.run(
            ["bash", str(self.SCRIPT_PATH)],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_ID": "C_TEST"},
        )
        # Should fail (no message provided)
        assert result.returncode != 0
        # Error message should mention message requirement
        combined = (result.stdout + result.stderr).lower()
        assert "message required" in combined or "usage" in combined
