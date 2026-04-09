"""Pytest configuration for MCP server tests.

This conftest.py sets up mocking for external dependencies (kubernetes, mcp)
before any tests run.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Set up mocks for external dependencies before any tests run
# This must happen at import time for pytest

# Mock ApiException class
class MockApiException(Exception):
    """Mock for kubernetes.client.ApiException."""

    def __init__(self, status: int | None = None, reason: str | None = None) -> None:
        self.status = status
        self.reason = reason
        super().__init__(f"ApiException({status}): {reason}")


# Mock FastMCP class that works as a decorator
class MockFastMCP:
    """Mock for mcp.server.fastmcp.FastMCP that supports the @tool() decorator."""

    def __init__(self, name: str, port: int = 8080) -> None:
        self.name = name
        self.port = port
        self._tools: dict = {}

    def tool(self):
        """Decorator that registers a tool function."""

        def decorator(func):
            self._tools[func.__name__] = func
            return func

        return decorator

    def run(self, transport=None):
        """Mock run method - does nothing."""
        pass


# Set up mocks for kubernetes
sys.modules["kubernetes"] = MagicMock()
sys.modules["kubernetes.client"] = MagicMock()
sys.modules["kubernetes.client"].ApiException = MockApiException
sys.modules["kubernetes.config"] = MagicMock()
sys.modules["kubernetes.config"].ConfigException = Exception

# Set up mocks for mcp
sys.modules["mcp"] = MagicMock()
sys.modules["mcp.server"] = MagicMock()
sys.modules["mcp.server.fastmcp"] = MagicMock()
sys.modules["mcp.server.fastmcp"].FastMCP = MockFastMCP
