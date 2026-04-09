"""Pytest configuration for MCP server tests.

This conftest.py sets up mocking for external dependencies (kubernetes, mcp)
before any tests run.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Import mock classes from test_fixtures to avoid duplication
from tests.mcp.test_fixtures import MockApiException, MockFastMCP

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
