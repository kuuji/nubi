"""Tests for nubi.controller.results — read executor result from GitHub API."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nubi.agents.result import ExecutorResult
from nubi.controller.results import read_executor_result
from nubi.exceptions import ResultError


class _MockResponse:
    """Minimal mock of aiohttp.ClientResponse as an async context manager."""

    def __init__(self, data: dict[str, Any], status: int = 200) -> None:
        self.status = status
        self._data = data

    async def json(self) -> dict[str, Any]:
        return self._data

    async def text(self) -> str:
        return "error"


class _MockSession:
    """Minimal mock of aiohttp.ClientSession."""

    def __init__(self, response: _MockResponse) -> None:
        self._response = response

    @asynccontextmanager
    async def get(self, url: str, **kwargs: Any) -> Any:
        yield self._response

    async def __aenter__(self) -> _MockSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class TestReadExecutorResult:
    @patch("nubi.controller.results.aiohttp.ClientSession")
    async def test_parses_valid_result(self, mock_session_cls: AsyncMock) -> None:
        result = ExecutorResult(status="success", commit_sha="abc123", summary="did work")
        content = result.model_dump_json()
        data = {
            "content": base64.b64encode(content.encode()).decode(),
            "encoding": "base64",
        }
        mock_session_cls.return_value = _MockSession(_MockResponse(data))

        parsed = await read_executor_result("kuuji/app", "nubi/task-1", "tok")
        assert parsed.status == "success"
        assert parsed.commit_sha == "abc123"

    @patch("nubi.controller.results.aiohttp.ClientSession")
    async def test_raises_on_404(self, mock_session_cls: AsyncMock) -> None:
        mock_session_cls.return_value = _MockSession(_MockResponse({}, status=404))

        with pytest.raises(ResultError):
            await read_executor_result("kuuji/app", "nubi/task-1", "tok")

    @patch("nubi.controller.results.aiohttp.ClientSession")
    async def test_raises_on_malformed_json(self, mock_session_cls: AsyncMock) -> None:
        data = {
            "content": base64.b64encode(b"not json").decode(),
            "encoding": "base64",
        }
        mock_session_cls.return_value = _MockSession(_MockResponse(data))

        with pytest.raises(ResultError):
            await read_executor_result("kuuji/app", "nubi/task-1", "tok")
