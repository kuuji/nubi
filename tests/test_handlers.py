"""Tests for nubi.controller.handlers — kopf event handlers."""

from __future__ import annotations

import logging
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from kubernetes_asyncio.client import CustomObjectsApi
from pydantic import ValidationError

from nubi.agents.gate_result import GateCategory, GateResult, GatesResult, GateStatus
from nubi.agents.result import ExecutorResult
from nubi.agents.review_result import ReviewDecision, ReviewResult
from nubi.controller.handlers import (
    EXECUTOR_JOB_STATUS_ANNOTATION,
    JOB_STATUS_ANNOTATION,
    REVIEWER_JOB_STATUS_ANNOTATION,
    _annotate_task_completion,
    on_executor_completion,
    on_job_status_change,
    on_reviewer_completion,
    on_taskspec_created,
)
from nubi.crd.defaults import LABEL_TASKSPEC_NAMESPACE
from nubi.exceptions import CredentialError, NamespaceError, SandboxError

VALID_SPEC: dict = {
    "description": "Implement feature X",
    "type": "code-change",
    "inputs": {"repo": "kuuji/test"},
}

VALID_SPEC_REVIEW_ENABLED: dict = {
    **VALID_SPEC,
    "review": {"enabled": True},
}

VALID_SPEC_REVIEW_DISABLED: dict = {
    **VALID_SPEC,
    "review": {"enabled": False},
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
        self.meta: FakeMeta = FakeMeta()


class FakeMeta:
    """Mimics kopf.Meta for annotation updates."""

    def __init__(self) -> None:
        self.annotations: dict = {}


class RecordingApiClient:
    """Captures outgoing Kubernetes API calls while using real kwarg validation."""

    def __init__(self) -> None:
        self.client_side_validation = True
        self.calls: list[dict[str, object]] = []

    def select_header_accept(self, accepts: Any) -> str:
        return accepts[0]

    def select_header_content_type(self, content_types: Any, http_method: str, body: object) -> str:
        return "application/merge-patch+json"

    async def call_api(
        self,
        resource_path: str,
        method: str,
        path_params: dict[str, object],
        query_params: list[tuple[str, object]],
        header_params: dict[str, str],
        **kwargs: object,
    ) -> object:
        self.calls.append(
            {
                "resource_path": resource_path,
                "method": method,
                "path_params": path_params,
                "query_params": query_params,
                "header_params": header_params,
                "kwargs": kwargs,
            }
        )
        return {}


# -- Mocking targets ---------------------------------------------------------

NS_MOCK = "nubi.controller.handlers.ensure_task_namespace"
CRED_MOCK = "nubi.controller.handlers.ensure_stage_secret"
EXEC_JOB_MOCK = "nubi.controller.handlers.create_executor_job"
REVIEW_JOB_MOCK = "nubi.controller.handlers.create_reviewer_job"
TOKEN_MOCK = "nubi.controller.handlers._read_github_token"


# -- on_taskspec_created — happy path ----------------------------------------


class TestOnTaskSpecCreated:
    @patch(EXEC_JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
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

    @patch(EXEC_JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
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

    @patch(EXEC_JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-test-task-1")
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


# -- on_taskspec_created — error handling ------------------------------------


class TestOnTaskSpecCreatedErrors:
    @patch(EXEC_JOB_MOCK, new_callable=AsyncMock)
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

    @patch(EXEC_JOB_MOCK, new_callable=AsyncMock)
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

    @patch(EXEC_JOB_MOCK, new_callable=AsyncMock, side_effect=SandboxError("job failed"))
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


class TestOnTaskSpecCreatedInvalid:
    async def test_invalid_spec_raises(self) -> None:
        fp = FakePatch()
        with pytest.raises((ValidationError, ValueError)):
            await on_taskspec_created(
                spec=INVALID_SPEC, patch=fp, name="bad-task", namespace="nubi-system"
            )


# -- on_job_status_change — routes by stage ----------------------------------


class TestOnJobStatusChange:
    async def test_logs_task_id_and_stage(self, caplog: pytest.LogCaptureFixture) -> None:
        labels = {
            "nubi.io/task-id": "task-abc-123",
            "nubi.io/stage": "executor",
            LABEL_TASKSPEC_NAMESPACE: "taskspec-ns",
        }
        with caplog.at_level(logging.INFO):
            await on_job_status_change(
                labels=labels, name="job-xyz", namespace="nubi-task-abc", status={}
            )
        assert "task-abc-123" in caplog.text

    async def test_ignores_running_job(self) -> None:
        labels = {
            "nubi.io/task-id": "task-1",
            "nubi.io/stage": "executor",
            LABEL_TASKSPEC_NAMESPACE: "explicit-ns",
        }
        with patch(
            "nubi.controller.handlers._annotate_task_completion", new_callable=AsyncMock
        ) as mock_annotate:
            await on_job_status_change(labels=labels, name="j", namespace="ns", status={})
            mock_annotate.assert_not_called()

    @patch("nubi.controller.handlers._annotate_task_completion", new_callable=AsyncMock)
    async def test_executor_completion_uses_executor_annotation(
        self, mock_annotate: AsyncMock
    ) -> None:
        labels = {
            "nubi.io/task-id": "task-1",
            "nubi.io/stage": "executor",
            LABEL_TASKSPEC_NAMESPACE: "ns",
        }
        status = {"conditions": [{"type": "Complete", "status": "True"}]}
        with patch("kubernetes_asyncio.client.CustomObjectsApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_namespaced_custom_object = AsyncMock(return_value={"spec": VALID_SPEC})
            await on_job_status_change(labels=labels, name="j", namespace="ns", status=status)
        mock_annotate.assert_awaited_once_with(
            "task-1", "ns", "j", "ns", "succeeded", EXECUTOR_JOB_STATUS_ANNOTATION
        )

    @patch("nubi.controller.handlers._annotate_task_completion", new_callable=AsyncMock)
    async def test_reviewer_completion_uses_reviewer_annotation(
        self, mock_annotate: AsyncMock
    ) -> None:
        labels = {
            "nubi.io/task-id": "task-1",
            "nubi.io/stage": "reviewer",
            LABEL_TASKSPEC_NAMESPACE: "ns",
        }
        status = {"conditions": [{"type": "Complete", "status": "True"}]}
        with patch("kubernetes_asyncio.client.CustomObjectsApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_namespaced_custom_object = AsyncMock(return_value={"spec": VALID_SPEC})
            await on_job_status_change(labels=labels, name="j", namespace="ns", status=status)
        mock_annotate.assert_awaited_once_with(
            "task-1", "ns", "j", "ns", "succeeded", REVIEWER_JOB_STATUS_ANNOTATION
        )

    @patch("nubi.controller.handlers._annotate_task_completion", new_callable=AsyncMock)
    async def test_unknown_stage_ignored(self, mock_annotate: AsyncMock) -> None:
        labels = {
            "nubi.io/task-id": "task-1",
            "nubi.io/stage": "planner",
            LABEL_TASKSPEC_NAMESPACE: "ns",
        }
        status = {"conditions": [{"type": "Complete", "status": "True"}]}
        await on_job_status_change(labels=labels, name="j", namespace="ns", status=status)
        mock_annotate.assert_not_called()

    @patch("nubi.controller.handlers._annotate_task_completion", new_callable=AsyncMock)
    async def test_duplicate_completion_does_not_reannotate(self, mock_annotate: AsyncMock) -> None:
        labels = {
            "nubi.io/task-id": "task-1",
            "nubi.io/stage": "executor",
            LABEL_TASKSPEC_NAMESPACE: "ns",
        }
        status = {"conditions": [{"type": "Complete", "status": "True"}]}
        with patch("kubernetes_asyncio.client.CustomObjectsApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_namespaced_custom_object = AsyncMock(
                return_value={
                    "spec": VALID_SPEC,
                    "metadata": {"annotations": {EXECUTOR_JOB_STATUS_ANNOTATION: "processed"}},
                }
            )
            await on_job_status_change(labels=labels, name="j", namespace="ns", status=status)
        mock_annotate.assert_not_called()


class TestAnnotateTaskCompletion:
    async def test_uses_correct_annotation_key(self) -> None:
        recording_client = RecordingApiClient()
        real_api = CustomObjectsApi(api_client=cast(Any, recording_client))

        with patch("kubernetes_asyncio.client.CustomObjectsApi", return_value=real_api):
            await _annotate_task_completion(
                "task-1",
                "ns",
                "job-1",
                "exec-ns",
                "succeeded",
                EXECUTOR_JOB_STATUS_ANNOTATION,
            )

        assert len(recording_client.calls) == 1
        call = cast(dict[str, Any], recording_client.calls[0])
        assert (
            call["kwargs"]["body"]["metadata"]["annotations"][EXECUTOR_JOB_STATUS_ANNOTATION]
            == "succeeded"
        )


# -- on_executor_completion — field handler ----------------------------------


class TestOnExecutorCompletion:
    @pytest.fixture
    def mock_secret(self) -> object:
        return type("Secret", (), {"data": {"github-token": "Z2hwX3Rlc3Q="}})()

    async def test_ignores_processed_annotation(self) -> None:
        fp = FakePatch()
        await on_executor_completion(
            spec=VALID_SPEC,
            name="task-1",
            namespace="ns",
            status={},
            patch=fp,
            old="succeeded",
            new="processed",
        )
        assert fp.status == {}

    async def test_failed_job_sets_failed_phase(self) -> None:
        fp = FakePatch()
        await on_executor_completion(
            spec=VALID_SPEC,
            name="task-1",
            namespace="ns",
            status={},
            patch=fp,
            old=None,
            new="failed",
        )
        assert fp.status.get("phase") == "Failed"
        assert fp.meta.annotations.get(EXECUTOR_JOB_STATUS_ANNOTATION) == "processed"

    @patch("nubi.controller.handlers.read_gates_result", new_callable=AsyncMock)
    @patch("nubi.controller.handlers.read_executor_result", new_callable=AsyncMock)
    @patch(TOKEN_MOCK, new_callable=AsyncMock, return_value="ghp_test")
    async def test_review_disabled_sets_done(
        self,
        mock_token: AsyncMock,
        mock_result: AsyncMock,
        mock_gates: AsyncMock,
    ) -> None:
        mock_result.return_value = ExecutorResult(
            status="success", commit_sha="abc123", summary="Done"
        )
        mock_gates.return_value = GatesResult(discovered=[], gates=[], overall_passed=True)

        fp = FakePatch()
        await on_executor_completion(
            spec=VALID_SPEC_REVIEW_DISABLED,
            name="task-1",
            namespace="ns",
            status={},
            patch=fp,
            old=None,
            new="succeeded",
        )
        assert fp.status.get("phase") == "Done"

    @patch(REVIEW_JOB_MOCK, new_callable=AsyncMock, return_value="nubi-reviewer-task-1")
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-reviewer-credentials")
    @patch("nubi.controller.handlers.read_gates_result", new_callable=AsyncMock)
    @patch("nubi.controller.handlers.read_executor_result", new_callable=AsyncMock)
    @patch(TOKEN_MOCK, new_callable=AsyncMock, return_value="ghp_test")
    async def test_review_enabled_sets_reviewing_and_spawns_reviewer(
        self,
        mock_token: AsyncMock,
        mock_result: AsyncMock,
        mock_gates: AsyncMock,
        mock_cred: AsyncMock,
        mock_review_job: AsyncMock,
    ) -> None:
        mock_result.return_value = ExecutorResult(
            status="success", commit_sha="abc123", summary="Done"
        )
        mock_gates.return_value = GatesResult(discovered=[], gates=[], overall_passed=True)

        fp = FakePatch()
        await on_executor_completion(
            spec=VALID_SPEC_REVIEW_ENABLED,
            name="task-1",
            namespace="ns",
            status={"workspace": {"namespace": "nubi-task-1"}},
            patch=fp,
            old=None,
            new="succeeded",
        )
        assert fp.status.get("phase") == "Reviewing"
        assert fp.status["stages"]["reviewer"]["status"] == "running"
        mock_cred.assert_awaited_once_with("nubi-task-1", "task-1", "reviewer")
        mock_review_job.assert_awaited_once()

    @patch("nubi.controller.handlers.read_gates_result", new_callable=AsyncMock)
    @patch("nubi.controller.handlers.read_executor_result", new_callable=AsyncMock)
    @patch(TOKEN_MOCK, new_callable=AsyncMock, return_value="ghp_test")
    async def test_gate_failure_below_retry_sets_executing(
        self,
        mock_token: AsyncMock,
        mock_result: AsyncMock,
        mock_gates: AsyncMock,
    ) -> None:
        mock_result.return_value = ExecutorResult(
            status="success", commit_sha="abc123", summary="Done"
        )
        mock_gates.return_value = GatesResult(
            discovered=[],
            gates=[GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.FAILED)],
            overall_passed=False,
            attempt=1,
        )

        fp = FakePatch()
        await on_executor_completion(
            spec={**VALID_SPEC, "loopPolicy": {"max_retries": 2, "on_max_retries": "escalate"}},
            name="task-1",
            namespace="ns",
            status={"stages": {"executor": {"attempts": 1}}},
            patch=fp,
            old=None,
            new="succeeded",
        )
        assert fp.status.get("phase") == "Executing"

    @patch("nubi.controller.handlers.read_gates_result", new_callable=AsyncMock)
    @patch("nubi.controller.handlers.read_executor_result", new_callable=AsyncMock)
    @patch(TOKEN_MOCK, new_callable=AsyncMock, return_value="ghp_test")
    async def test_gate_failure_at_retry_limit_escalates(
        self,
        mock_token: AsyncMock,
        mock_result: AsyncMock,
        mock_gates: AsyncMock,
    ) -> None:
        mock_result.return_value = ExecutorResult(
            status="success", commit_sha="abc123", summary="Done"
        )
        mock_gates.return_value = GatesResult(
            discovered=[],
            gates=[GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.FAILED)],
            overall_passed=False,
            attempt=1,
        )

        fp = FakePatch()
        await on_executor_completion(
            spec={**VALID_SPEC, "loopPolicy": {"max_retries": 1, "on_max_retries": "escalate"}},
            name="task-1",
            namespace="ns",
            status={"stages": {"executor": {"attempts": 1}}},
            patch=fp,
            old=None,
            new="succeeded",
        )
        assert fp.status.get("phase") == "Escalated"


# -- on_reviewer_completion — field handler ----------------------------------


class TestOnReviewerCompletion:
    @patch("nubi.controller.handlers.read_review_result", new_callable=AsyncMock)
    @patch(TOKEN_MOCK, new_callable=AsyncMock, return_value="ghp_test")
    async def test_approve_sets_done(
        self,
        mock_token: AsyncMock,
        mock_review: AsyncMock,
    ) -> None:
        mock_review.return_value = ReviewResult(
            decision=ReviewDecision.APPROVE,
            feedback="LGTM",
            summary="All good",
        )

        fp = FakePatch()
        await on_reviewer_completion(
            spec=VALID_SPEC_REVIEW_ENABLED,
            name="task-1",
            namespace="ns",
            status={"stages": {"executor": {"status": "complete"}}},
            patch=fp,
            old=None,
            new="succeeded",
        )
        assert fp.status.get("phase") == "Done"
        assert fp.status["stages"]["reviewer"]["decision"] == "approve"
        assert fp.meta.annotations.get(REVIEWER_JOB_STATUS_ANNOTATION) == "processed"

    @patch(EXEC_JOB_MOCK, new_callable=AsyncMock, return_value="nubi-executor-task-1")
    @patch(CRED_MOCK, new_callable=AsyncMock, return_value="nubi-executor-credentials")
    @patch("nubi.controller.handlers.read_review_result", new_callable=AsyncMock)
    @patch(TOKEN_MOCK, new_callable=AsyncMock, return_value="ghp_test")
    async def test_request_changes_respawns_executor(
        self,
        mock_token: AsyncMock,
        mock_review: AsyncMock,
        mock_cred: AsyncMock,
        mock_exec_job: AsyncMock,
    ) -> None:
        mock_review.return_value = ReviewResult(
            decision=ReviewDecision.REQUEST_CHANGES,
            feedback="Fix error handling",
            summary="Needs work",
        )

        fp = FakePatch()
        await on_reviewer_completion(
            spec={**VALID_SPEC_REVIEW_ENABLED, "loop_policy": {"reviewer_to_executor": True}},
            name="task-1",
            namespace="ns",
            status={
                "workspace": {"namespace": "nubi-task-1"},
                "stages": {"executor": {"status": "complete"}},
            },
            patch=fp,
            old=None,
            new="succeeded",
        )
        assert fp.status.get("phase") == "Executing"
        mock_exec_job.assert_awaited_once()

    @patch("nubi.controller.handlers.read_review_result", new_callable=AsyncMock)
    @patch(TOKEN_MOCK, new_callable=AsyncMock, return_value="ghp_test")
    async def test_reject_sets_escalated(
        self,
        mock_token: AsyncMock,
        mock_review: AsyncMock,
    ) -> None:
        mock_review.return_value = ReviewResult(
            decision=ReviewDecision.REJECT,
            feedback="Completely wrong",
            summary="Rejected",
        )

        fp = FakePatch()
        await on_reviewer_completion(
            spec=VALID_SPEC_REVIEW_ENABLED,
            name="task-1",
            namespace="ns",
            status={"stages": {}},
            patch=fp,
            old=None,
            new="succeeded",
        )
        assert fp.status.get("phase") == "Escalated"
        assert fp.meta.annotations.get(REVIEWER_JOB_STATUS_ANNOTATION) == "processed"

    async def test_failed_reviewer_job_sets_failed(self) -> None:
        fp = FakePatch()
        await on_reviewer_completion(
            spec=VALID_SPEC_REVIEW_ENABLED,
            name="task-1",
            namespace="ns",
            status={"stages": {}},
            patch=fp,
            old=None,
            new="failed",
        )
        assert fp.status.get("phase") == "Failed"
        assert fp.meta.annotations.get(REVIEWER_JOB_STATUS_ANNOTATION) == "processed"

    async def test_ignores_processed_annotation(self) -> None:
        fp = FakePatch()
        await on_reviewer_completion(
            spec=VALID_SPEC,
            name="task-1",
            namespace="ns",
            status={},
            patch=fp,
            old="succeeded",
            new="processed",
        )
        assert fp.status == {}

    @patch("nubi.controller.handlers.read_review_result", new_callable=AsyncMock)
    @patch(TOKEN_MOCK, new_callable=AsyncMock, return_value="ghp_test")
    async def test_request_changes_without_retry_escalates(
        self,
        mock_token: AsyncMock,
        mock_review: AsyncMock,
    ) -> None:
        mock_review.return_value = ReviewResult(
            decision=ReviewDecision.REQUEST_CHANGES,
            feedback="Fix this",
            summary="Needs work",
        )

        fp = FakePatch()
        await on_reviewer_completion(
            spec={**VALID_SPEC_REVIEW_ENABLED, "loop_policy": {"reviewer_to_executor": False}},
            name="task-1",
            namespace="ns",
            status={"stages": {}},
            patch=fp,
            old=None,
            new="succeeded",
        )
        assert fp.status.get("phase") == "Escalated"


# -- Backward compat alias ---------------------------------------------------


class TestBackwardCompat:
    def test_job_status_annotation_alias(self) -> None:
        assert JOB_STATUS_ANNOTATION == EXECUTOR_JOB_STATUS_ANNOTATION
