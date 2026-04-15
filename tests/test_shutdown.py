"""Tests for graceful shutdown behavior.

Verifies that SIGINT/SIGTERM trigger state flushing, Slack notification,
and that tmux sessions are NOT killed on bot shutdown.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anchovies.storage import Storage, reset_storage


@pytest.fixture
def fresh_storage(tmp_path, monkeypatch):
    """Create a fresh storage instance for each test."""
    reset_storage()
    db_path = tmp_path / "test.db"
    storage = Storage(db_path)

    import anchovies.storage as storage_module
    monkeypatch.setattr(storage_module, "_storage", storage)

    yield storage

    storage.close()
    reset_storage()


class TestFlushStateToStorage:
    """Test that flush_state_to_storage persists in-memory state."""

    @pytest.mark.anyio
    async def test_flush_persists_conversations(self, fresh_storage, monkeypatch):
        from anchovies import app
        import anchovies.handlers as handlers

        # Populate the conversation store
        test_store = {
            "thread_1": [{"role": "user", "content": "hello", "member": ""}],
            "thread_2": [{"role": "assistant", "content": "hi", "member": "marcus"}],
        }
        monkeypatch.setattr(app, "conversation_store", test_store)

        summary = await app.flush_state_to_storage()

        assert summary["conversations"] == 2
        assert fresh_storage.load_conversation("thread_1") == test_store["thread_1"]
        assert fresh_storage.load_conversation("thread_2") == test_store["thread_2"]

    @pytest.mark.anyio
    async def test_flush_logs_shutdown_event(self, fresh_storage, monkeypatch):
        from anchovies import app
        monkeypatch.setattr(app, "conversation_store", {})

        await app.flush_state_to_storage()

        events = fresh_storage.query_audit(event_type="bot_stopped")
        assert len(events) == 1

    @pytest.mark.anyio
    async def test_flush_returns_summary(self, fresh_storage, monkeypatch):
        from anchovies import app
        monkeypatch.setattr(app, "conversation_store", {
            "t1": [{"role": "user", "content": "x", "member": ""}],
        })

        summary = await app.flush_state_to_storage()

        assert "conversations" in summary
        assert "sessions" in summary
        assert summary["conversations"] == 1


class TestNotifyShutdown:
    """Test Slack shutdown notification."""

    @pytest.mark.anyio
    async def test_posts_to_status_channel(self, monkeypatch):
        from anchovies import app

        monkeypatch.setenv("SLACK_STATUS_CHANNEL", "C_STATUS")

        mock_client = AsyncMock()
        await app.notify_shutdown(mock_client, {"conversations": 5, "sessions": 2})

        mock_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C_STATUS"
        assert "offline" in call_kwargs["text"].lower() or "going" in call_kwargs["text"].lower()

    @pytest.mark.anyio
    async def test_falls_back_to_default_channel(self, monkeypatch):
        from anchovies import app
        from anchovies import config

        monkeypatch.delenv("SLACK_STATUS_CHANNEL", raising=False)
        monkeypatch.setattr(config, "SLACK_CHANNEL_ID", "C_DEFAULT")

        mock_client = AsyncMock()
        await app.notify_shutdown(mock_client, {"conversations": 0, "sessions": 0})

        mock_client.chat_postMessage.assert_called_once()
        assert mock_client.chat_postMessage.call_args.kwargs["channel"] == "C_DEFAULT"

    @pytest.mark.anyio
    async def test_skips_when_no_channel_configured(self, monkeypatch):
        from anchovies import app
        from anchovies import config

        monkeypatch.delenv("SLACK_STATUS_CHANNEL", raising=False)
        monkeypatch.setattr(config, "SLACK_CHANNEL_ID", "")

        mock_client = AsyncMock()
        await app.notify_shutdown(mock_client, {"conversations": 0, "sessions": 0})

        mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.anyio
    async def test_slack_failure_is_nonfatal(self, monkeypatch):
        """If Slack API fails, shutdown still proceeds."""
        from anchovies import app

        monkeypatch.setenv("SLACK_STATUS_CHANNEL", "C_TEST")

        mock_client = AsyncMock()
        mock_client.chat_postMessage.side_effect = Exception("API down")

        # Should NOT raise
        await app.notify_shutdown(mock_client, {"conversations": 0, "sessions": 0})


class TestGracefulShutdown:
    """Test the full graceful_shutdown sequence."""

    @pytest.mark.anyio
    async def test_shutdown_does_not_kill_tmux(self, monkeypatch, fresh_storage):
        """Graceful shutdown must NOT kill tmux sessions — they should survive for recovery."""
        from anchovies import app

        # Verify the source code does not call kill_session or kill-session
        source = inspect.getsource(app.graceful_shutdown)
        assert "kill_session" not in source
        assert "kill-session" not in source

    @pytest.mark.anyio
    async def test_shutdown_closes_handler(self, monkeypatch, fresh_storage):
        from anchovies import app
        monkeypatch.setattr(app, "conversation_store", {})
        monkeypatch.delenv("SLACK_STATUS_CHANNEL", raising=False)
        from anchovies import config
        monkeypatch.setattr(config, "SLACK_CHANNEL_ID", "")

        mock_handler = MagicMock()
        mock_handler.close_async = AsyncMock()
        mock_client = AsyncMock()

        await app.graceful_shutdown(mock_handler, mock_client)

        mock_handler.close_async.assert_called_once()

    @pytest.mark.anyio
    async def test_shutdown_flushes_and_notifies(self, monkeypatch, fresh_storage):
        from anchovies import app
        monkeypatch.setattr(app, "conversation_store", {
            "t1": [{"role": "user", "content": "x", "member": ""}],
        })
        monkeypatch.setenv("SLACK_STATUS_CHANNEL", "C_TEST")

        mock_handler = MagicMock()
        mock_handler.close_async = AsyncMock()
        mock_client = AsyncMock()

        await app.graceful_shutdown(mock_handler, mock_client)

        # Flushed to storage
        assert fresh_storage.load_conversation("t1") != []
        # Notified Slack
        mock_client.chat_postMessage.assert_called_once()


class TestSignalHandlers:
    """Verify the shutdown pathway is wired in async_main."""

    def test_async_main_registers_signals(self):
        from anchovies import app
        source = inspect.getsource(app.async_main)
        assert "SIGINT" in source
        assert "SIGTERM" in source

    def test_async_main_calls_graceful_shutdown(self):
        from anchovies import app
        source = inspect.getsource(app.async_main)
        assert "graceful_shutdown" in source

    def test_async_main_recovers_on_startup(self):
        from anchovies import app
        source = inspect.getsource(app.async_main)
        assert "recover_from_storage" in source
