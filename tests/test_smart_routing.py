"""Tests for smart routing and assign command."""

import inspect
import re

import pytest

import anchovies.handlers as handlers_module
from anchovies.handlers.routing import should_route_to_chat_hub
from anchovies.handlers.control_commands import handle_control_command


class TestShouldRouteToHub:
    def test_work_request(self):
        from anchovies.router import RoutingResult
        r = RoutingResult(members=["marcus"], cleaned_message="fix bug", is_broadcast=False)
        assert should_route_to_chat_hub("fix the bug in app.py", r, False) is True

    def test_crosstalk_skips(self):
        from anchovies.router import RoutingResult
        r = RoutingResult(members=["sofia"], cleaned_message="x", is_broadcast=False)
        assert should_route_to_chat_hub("x", r, True) is False

    def test_no_members(self):
        from anchovies.router import RoutingResult
        r = RoutingResult(members=[], cleaned_message="hello", is_broadcast=False)
        assert should_route_to_chat_hub("hello", r, False) is True

    def test_specific_persona(self):
        from anchovies.router import RoutingResult
        r = RoutingResult(members=["sofia"], cleaned_message="hello", is_broadcast=False)
        assert should_route_to_chat_hub("hello", r, False) is False


class TestAssignCommand:
    def test_assign_pattern(self):
        msg = "assign sofia build the calculator core module"
        match = re.match(r"assign\s+(\w+)\s+(.+)", msg, re.IGNORECASE)
        assert match is not None
        assert match.group(1) == "sofia"

    def test_assign_in_control_commands(self):
        source = inspect.getsource(handle_control_command)
        assert "assign" in source


class TestIntegration:
    def test_handle_team_message_exists(self):
        from anchovies.handlers import handle_team_message
        assert inspect.iscoroutinefunction(handle_team_message)

    def test_handle_chat_hub_message_exists(self):
        from anchovies.handlers import handle_chat_hub_message
        assert inspect.iscoroutinefunction(handle_chat_hub_message)
