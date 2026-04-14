"""Tests for the Slack Bolt app configuration.

Verifies the app uses AsyncApp (not sync App), handlers are async,
and event filtering works correctly.
"""

import inspect

import pytest


class TestAsyncAppSetup:
    """Verify the app uses async Slack Bolt patterns."""

    def test_imports_async_app(self):
        """app.py imports AsyncApp, not sync App."""
        import anchovies.app as app_module
        source = inspect.getsource(app_module)
        assert "from slack_bolt.async_app import AsyncApp" in source

    def test_imports_async_handler(self):
        """app.py imports AsyncSocketModeHandler."""
        import anchovies.app as app_module
        source = inspect.getsource(app_module)
        assert "AsyncSocketModeHandler" in source

    def test_no_sync_asyncio_run_in_handlers(self):
        """Handlers should NOT use asyncio.run() to call async code.

        asyncio.run() inside a running event loop causes RuntimeError.
        The old pattern was:
            asyncio.run(handle_team_message(...))
        This should now be:
            await handle_team_message(...)
        """
        import anchovies.app as app_module
        source = inspect.getsource(app_module.create_app)
        # The create_app function should not contain asyncio.run
        assert "asyncio.run(" not in source

    def test_create_app_returns_async_app(self, anchovies_config):
        """create_app() returns an AsyncApp instance."""
        from slack_bolt.async_app import AsyncApp
        from anchovies.app import create_app
        app = create_app()
        assert isinstance(app, AsyncApp)

    def test_handlers_are_async(self):
        """Event handlers inside create_app should be async def, not def."""
        import anchovies.app as app_module
        source = inspect.getsource(app_module.create_app)
        # All handler functions should be async
        assert "async def handle_app_mention" in source
        assert "async def handle_message" in source
        assert "async def handle_app_home_opened" in source

    def test_handlers_await_not_asyncio_run(self):
        """Handlers should use 'await handle_team_message' not 'asyncio.run(handle_team_message'."""
        import anchovies.app as app_module
        source = inspect.getsource(app_module.create_app)
        assert "await handle_team_message(" in source


class TestEventFiltering:
    """Test that the message handler filters correctly."""

    def test_bot_messages_ignored_in_source(self):
        """Handler checks for bot_id to ignore bot messages."""
        import anchovies.app as app_module
        source = inspect.getsource(app_module.create_app)
        assert 'event.get("bot_id")' in source

    def test_subtypes_ignored_in_source(self):
        """Handler checks for subtype to ignore edits/deletes."""
        import anchovies.app as app_module
        source = inspect.getsource(app_module.create_app)
        assert 'event.get("subtype")' in source

    def test_public_channels_require_mention(self):
        """Public channel messages without @mention are not handled by message handler."""
        import anchovies.app as app_module
        source = inspect.getsource(app_module.create_app)
        # The message handler should filter out non-DM channel types
        assert '"im"' in source
        assert '"mpim"' in source
