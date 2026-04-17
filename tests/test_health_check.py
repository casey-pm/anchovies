"""Tests for the health check CLI."""

from pathlib import Path
from unittest.mock import patch

import pytest

from anchovies.health import check_binary, check_path_exists, check_path_has_files, run_checks, main


class TestCheckBinary:
    def test_existing_binary(self):
        ok, msg = check_binary("python3", "Python")
        assert ok is True
        assert "Python" in msg

    def test_missing_binary(self):
        ok, msg = check_binary("nonexistent_binary_xyz", "Test")
        assert ok is False
        assert "NOT FOUND" in msg


class TestCheckPathExists:
    def test_existing_path(self, tmp_path):
        ok, msg = check_path_exists(tmp_path, "Test dir")
        assert ok is True

    def test_missing_path(self, tmp_path):
        ok, msg = check_path_exists(tmp_path / "nope", "Test dir")
        assert ok is False
        assert "MISSING" in msg


class TestCheckPathHasFiles:
    def test_has_matching_files(self, tmp_path):
        (tmp_path / "profile_sofia.yaml").write_text("name: Sofia")
        ok, msg = check_path_has_files(tmp_path, "profile_*.yaml", "Profiles")
        assert ok is True
        assert "1 file" in msg

    def test_no_matching_files(self, tmp_path):
        ok, msg = check_path_has_files(tmp_path, "profile_*.yaml", "Profiles")
        assert ok is False

    def test_missing_directory(self, tmp_path):
        ok, msg = check_path_has_files(tmp_path / "nope", "*.yaml", "Test")
        assert ok is False


class TestRunChecks:
    def test_returns_list_of_tuples(self):
        results = run_checks()
        assert isinstance(results, list)
        assert len(results) > 0
        for item in results:
            assert len(item) == 3
            ok, category, message = item
            assert isinstance(ok, bool)
            assert isinstance(category, str)
            assert isinstance(message, str)

    def test_checks_common_binaries(self):
        results = run_checks()
        messages = [msg for _, _, msg in results]
        combined = " ".join(messages)
        assert "tmux" in combined
        assert "git" in combined
        assert "Claude" in combined or "claude" in combined


class TestMainCLI:
    def test_runs_without_error(self, capsys):
        # Just verify it doesn't crash — some checks may fail in CI
        exit_code = main()
        assert exit_code in (0, 1)
        output = capsys.readouterr().out
        assert "Health Check" in output
        assert "Results:" in output
