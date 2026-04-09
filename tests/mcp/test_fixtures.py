"""Shared test fixtures and mocks for MCP server tests.

This module provides test fixtures, mock classes, and test data used across
the MCP server test suite. It also handles module mocking for the
kubernetes and mcp packages when running tests.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


# =============================================================================
# Mock ApiException class
# =============================================================================


class MockApiException(Exception):
    """Mock for kubernetes.client.ApiException."""

    def __init__(self, status: int | None = None, reason: str | None = None) -> None:
        self.status = status
        self.reason = reason
        super().__init__(f"ApiException({status}): {reason}")


# =============================================================================
# Mock FastMCP class
# =============================================================================


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


# =============================================================================
# Module setup for testing
# =============================================================================

# Set up mocks for external dependencies before any imports
def setup_mocks() -> None:
    """Set up mocks for kubernetes and mcp modules."""
    # Mock ApiException in kubernetes.client
    sys.modules["kubernetes"] = MagicMock()
    sys.modules["kubernetes.client"] = MagicMock()
    sys.modules["kubernetes.client"].ApiException = MockApiException
    sys.modules["kubernetes.config"] = MagicMock()
    sys.modules["kubernetes.config"].ConfigException = Exception

    # Mock mcp module
    sys.modules["mcp"] = MagicMock()
    sys.modules["mcp.server"] = MagicMock()
    sys.modules["mcp.server.fastmcp"] = MagicMock()
    sys.modules["mcp.server.fastmcp"].FastMCP = MockFastMCP


# =============================================================================
# Test data fixtures
# =============================================================================

VALID_SPEC: dict = {
    "description": "Add rate limiting to API endpoints",
    "type": "code-change",
    "inputs": {
        "repo": "kuuji/some-app",
        "branch": "main",
    },
}

VALID_SPEC_MINIMAL: dict = {
    "description": "Fix a bug",
    "type": "code-change",
    "inputs": {"repo": "kuuji/some-app"},
}

TASK_CR: dict = {
    "apiVersion": "nubi.io/v1",
    "kind": "TaskSpec",
    "metadata": {
        "name": "test-task",
        "namespace": "nubi-system",
        "creationTimestamp": "2024-01-15T10:30:00Z",
    },
    "spec": {
        "description": "Test task",
        "type": "code-change",
        "inputs": {"repo": "kuuji/test", "branch": "main"},
    },
    "status": {
        "phase": "Pending",
        "phaseChangedAt": "2024-01-15T10:30:00Z",
        "workspace": {
            "namespace": "nubi-test-task",
            "repo": "kuuji/test",
            "branch": "nubi-test-task-branch",
            "headSHA": "abc123",
        },
        "stages": {
            "executor": {
                "status": "pending",
                "attempts": 0,
                "commitSHA": "",
                "summary": "",
            },
            "validator": {
                "status": "pending",
                "deterministic": {"lint": "", "tests": "", "secret_scan": ""},
                "testCommitSHA": "",
            },
            "reviewer": {
                "status": "pending",
                "decision": "",
                "feedback": "",
            },
            "gating": {
                "status": "pending",
                "passed": False,
                "attempt": 0,
            },
        },
    },
}


# =============================================================================
# Pytest configuration
# =============================================================================

# Set up mocks when this module is imported (for pytest)
setup_mocks()
