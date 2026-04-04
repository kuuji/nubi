"""Tests for nubi.controller.handlers — kopf event handlers."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from nubi.controller.handlers import on_job_status_change, on_taskspec_created
from nubi.exceptions import NamespaceError

VALID_SPEC: dict = {
    "description": "Implement feature X",
    "type": "code-change",
    "inputs": {"repo": "kuuji/test"},
}

INVALID_SPEC: dict = {
    "description": "Bad task",
    "type": "banana",
    "inputs": {"repo": "kuuji/test"},
}


class FakePatch:
    """Mimics kopf.Patch — a dict-like object for status updates."""

    def __init__(self) -> None:
        self.status: dict = {}


# -- on_taskspec_created — valid spec ----------------------------------------

NS_MOCK = "nubi.controller.handlers.ensure_task_namespace"


class TestOnTaskSpecCreated:
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_sets_phase_to_pending(self, mock_ns: AsyncMock) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        assert fp.status.get("phase") == "Pending"

    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_returns_dict_with_message(self, mock_ns: AsyncMock) -> None:
        fp = FakePatch()
        result = await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        assert isinstance(result, dict)
        assert "message" in result

    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_stores_namespace_in_workspace(self, mock_ns: AsyncMock) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        workspace = fp.status.get("workspace", {})
        assert workspace.get("namespace") == "nubi-test-task-1"

    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_sets_phase_changed_at(self, mock_ns: AsyncMock) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        assert "phaseChangedAt" in fp.status

    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_calls_ensure_task_namespace(self, mock_ns: AsyncMock) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        mock_ns.assert_awaited_once()


# -- on_taskspec_created — namespace error -----------------------------------


class TestOnTaskSpecCreatedNamespaceError:
    @patch(NS_MOCK, new_callable=AsyncMock, side_effect=NamespaceError("boom"))
    async def test_sets_phase_failed(self, mock_ns: AsyncMock) -> None:
        fp = FakePatch()
        with pytest.raises((NamespaceError, Exception)):
            await on_taskspec_created(
                spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
            )
        assert fp.status.get("phase") == "Failed"


# -- on_taskspec_created — invalid spec --------------------------------------


class TestOnTaskSpecCreatedInvalid:
    async def test_invalid_spec_raises(self) -> None:
        fp = FakePatch()
        with pytest.raises((ValidationError, ValueError)):
            await on_taskspec_created(
                spec=INVALID_SPEC, patch=fp, name="bad-task", namespace="nubi-system"
            )


# -- on_job_status_change — logs task-id and stage ---------------------------


class TestOnJobStatusChange:
    async def test_logs_task_id_and_stage(self, caplog: pytest.LogCaptureFixture) -> None:
        labels = {
            "nubi.io/task-id": "task-abc-123",
            "nubi.io/stage": "executor",
        }
        with caplog.at_level(logging.INFO):
            await on_job_status_change(
                labels=labels, name="job-xyz", namespace="nubi-task-abc", status={}
            )
        assert "task-abc-123" in caplog.text
        assert "executor" in caplog.text
