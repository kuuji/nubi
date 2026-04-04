"""Tests for nubi.controller.handlers — kopf event handlers."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from nubi.controller.handlers import on_job_status_change, on_taskspec_created
from nubi.exceptions import CredentialError, NamespaceError, SandboxError

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


# -- Mocking targets ---------------------------------------------------------

NS_MOCK = "nubi.controller.handlers.ensure_task_namespace"
CRED_MOCK = "nubi.controller.handlers.ensure_stage_secret"
JOB_MOCK = "nubi.controller.handlers.create_executor_job"


# -- on_taskspec_created — happy path ----------------------------------------


class TestOnTaskSpecCreated:
    @patch(JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-executor-credentials")
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_sets_phase_to_executing(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        assert fp.status.get("phase") == "Executing"

    @patch(JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-executor-credentials")
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_returns_dict_with_message(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        result = await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        assert isinstance(result, dict)
        assert "message" in result

    @patch(JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-executor-credentials")
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_stores_namespace_in_workspace(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        workspace = fp.status.get("workspace", {})
        assert workspace.get("namespace") == "nubi-test-task-1"

    @patch(JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-executor-credentials")
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_calls_ensure_task_namespace(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        mock_ns.assert_awaited_once()

    @patch(JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-executor-credentials")
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_calls_ensure_stage_secret(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        mock_cred.assert_awaited_once_with("nubi-test-task-1", "test-task-1", "executor")

    @patch(JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-executor-credentials")
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_calls_create_executor_job(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        mock_job.assert_awaited_once()

    @patch(JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-executor-credentials")
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_sets_executor_stage_running(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
        )
        stages = fp.status.get("stages", {})
        executor = stages.get("executor", {})
        assert executor.get("status") == "running"
        assert executor.get("attempts") == 1


# -- on_taskspec_created — error handling ------------------------------------


class TestOnTaskSpecCreatedErrors:
    @patch(JOB_MOCK, new_callable=AsyncMock)
    @patch(CRED_MOCK, new_callable=AsyncMock)
    @patch(NS_MOCK, new_callable=AsyncMock, side_effect=NamespaceError("boom"))
    async def test_namespace_error_sets_failed(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        with pytest.raises(NamespaceError):
            await on_taskspec_created(
                spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
            )
        assert fp.status.get("phase") == "Failed"

    @patch(JOB_MOCK, new_callable=AsyncMock)
    @patch(CRED_MOCK, new_callable=AsyncMock, side_effect=CredentialError("no creds"))
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_credential_error_sets_failed(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        with pytest.raises(CredentialError):
            await on_taskspec_created(
                spec=VALID_SPEC, patch=fp, name="test-task-1", namespace="nubi-system"
            )
        assert fp.status.get("phase") == "Failed"

    @patch(JOB_MOCK, new_callable=AsyncMock, side_effect=SandboxError("job failed"))
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-executor-credentials")
    @patch(NS_MOCK, new_callable=AsyncMock, return_value="nubi-test-task-1")
    async def test_sandbox_error_sets_failed(
        self, mock_ns: AsyncMock, mock_cred: AsyncMock, mock_job: AsyncMock
    ) -> None:
        fp = FakePatch()
        with pytest.raises(SandboxError):
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
        fp = FakePatch()
        labels = {
            "nubi.io/task-id": "task-abc-123",
            "nubi.io/stage": "executor",
        }
        with caplog.at_level(logging.INFO):
            await on_job_status_change(
                labels=labels, name="job-xyz", namespace="nubi-task-abc", status={}, patch=fp
            )
        assert "task-abc-123" in caplog.text
        assert "executor" in caplog.text

    async def test_ignores_running_job(self) -> None:
        fp = FakePatch()
        labels = {"nubi.io/task-id": "task-1", "nubi.io/stage": "executor"}
        await on_job_status_change(labels=labels, name="j", namespace="ns", status={}, patch=fp)
        assert "phase" not in fp.status

    async def test_failed_job_sets_failed_phase(self) -> None:
        fp = FakePatch()
        labels = {"nubi.io/task-id": "task-1", "nubi.io/stage": "executor"}
        status = {"conditions": [{"type": "Failed", "status": "True"}]}
        await on_job_status_change(labels=labels, name="j", namespace="ns", status=status, patch=fp)
        assert fp.status.get("phase") == "Failed"
