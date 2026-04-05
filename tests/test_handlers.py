"""Tests for nubi.controller.handlers — kopf event handlers."""

from __future__ import annotations

import ast
import inspect
import logging
from collections.abc import Sequence
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from kubernetes_asyncio.client import CustomObjectsApi
from pydantic import ValidationError

from nubi.agents.gate_result import GateCategory, GateResult, GatesResult, GateStatus
from nubi.agents.result import ExecutorResult
from nubi.controller.handlers import (
    JOB_STATUS_ANNOTATION,
    _annotate_task_completion,
    on_job_completion_annotation,
    on_job_status_change,
    on_taskspec_created,
)
from nubi.crd.defaults import LABEL_TASKSPEC_NAMESPACE
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

    def select_header_accept(self, accepts: Sequence[str]) -> str:
        return accepts[0]

    def select_header_content_type(
        self,
        content_types: Sequence[str],
        http_method: str,
        body: object,
    ) -> str:
        assert http_method == "PATCH"
        assert body is not None
        assert "application/merge-patch+json" in content_types
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
        assert "executor" in caplog.text

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
    async def test_failed_job_annotates_task(self, mock_annotate: AsyncMock) -> None:
        labels = {
            "nubi.io/task-id": "task-1",
            "nubi.io/stage": "executor",
            LABEL_TASKSPEC_NAMESPACE: "explicit-ns",
        }
        status = {"conditions": [{"type": "Failed", "status": "True"}]}
        with patch("kubernetes_asyncio.client.CustomObjectsApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_namespaced_custom_object = AsyncMock(return_value={"spec": VALID_SPEC})
            await on_job_status_change(labels=labels, name="j", namespace="ns", status=status)
        mock_annotate.assert_awaited_once_with("task-1", "explicit-ns", "j", "ns", "failed")

    @patch("nubi.controller.handlers._annotate_task_completion", new_callable=AsyncMock)
    async def test_succeeded_job_annotates_task(self, mock_annotate: AsyncMock) -> None:
        labels = {
            "nubi.io/task-id": "task-1",
            "nubi.io/stage": "executor",
            LABEL_TASKSPEC_NAMESPACE: "explicit-ns",
        }
        status = {"conditions": [{"type": "Complete", "status": "True"}]}
        with patch("kubernetes_asyncio.client.CustomObjectsApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_namespaced_custom_object = AsyncMock(return_value={"spec": VALID_SPEC})
            await on_job_status_change(labels=labels, name="j", namespace="ns", status=status)
        mock_annotate.assert_awaited_once_with("task-1", "explicit-ns", "j", "ns", "succeeded")

    @patch("nubi.controller.handlers._annotate_task_completion", new_callable=AsyncMock)
    async def test_uses_explicit_taskspec_namespace_for_lookup(
        self, mock_annotate: AsyncMock
    ) -> None:
        labels = {
            "nubi.io/task-id": "task-1",
            "nubi.io/stage": "executor",
            LABEL_TASKSPEC_NAMESPACE: "explicit-ns",
        }
        status = {"conditions": [{"type": "Complete", "status": "True"}]}
        with patch("kubernetes_asyncio.client.CustomObjectsApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_namespaced_custom_object = AsyncMock(return_value={"spec": VALID_SPEC})
            await on_job_status_change(
                labels=labels,
                name="job-1",
                namespace="executor-ns",
                status=status,
            )

        mock_api.get_namespaced_custom_object.assert_awaited_once_with(
            group="nubi.io",
            version="v1",
            plural="taskspecs",
            name="task-1",
            namespace="explicit-ns",
        )
        mock_annotate.assert_awaited_once_with(
            "task-1",
            "explicit-ns",
            "job-1",
            "executor-ns",
            "succeeded",
        )

    @patch("nubi.controller.handlers._annotate_task_completion", new_callable=AsyncMock)
    async def test_missing_taskspec_namespace_label_does_not_guess(
        self, mock_annotate: AsyncMock
    ) -> None:
        labels = {"nubi.io/task-id": "task-1", "nubi.io/stage": "executor"}
        status = {"conditions": [{"type": "Complete", "status": "True"}]}
        with patch("kubernetes_asyncio.client.CustomObjectsApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_namespaced_custom_object = AsyncMock()
            await on_job_status_change(labels=labels, name="j", namespace="ns", status=status)

        mock_api.get_namespaced_custom_object.assert_not_called()
        mock_annotate.assert_not_called()

    @patch("nubi.controller.handlers._annotate_task_completion", new_callable=AsyncMock)
    async def test_duplicate_completion_does_not_reannotate_processed_taskspec(
        self, mock_annotate: AsyncMock
    ) -> None:
        labels = {
            "nubi.io/task-id": "task-1",
            "nubi.io/stage": "executor",
            LABEL_TASKSPEC_NAMESPACE: "explicit-ns",
        }
        status = {"conditions": [{"type": "Complete", "status": "True"}]}
        with patch("kubernetes_asyncio.client.CustomObjectsApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.get_namespaced_custom_object = AsyncMock(
                return_value={
                    "spec": VALID_SPEC,
                    "metadata": {"annotations": {JOB_STATUS_ANNOTATION: "processed"}},
                }
            )
            await on_job_status_change(
                labels=labels,
                name="job-1",
                namespace="executor-ns",
                status=status,
            )

        mock_api.get_namespaced_custom_object.assert_awaited_once()
        mock_annotate.assert_not_called()


class TestAnnotateTaskCompletion:
    async def test_uses_real_client_kwarg_and_merge_patch_for_metadata_annotations(
        self,
    ) -> None:
        recording_client = RecordingApiClient()
        real_api = CustomObjectsApi(api_client=cast(Any, recording_client))

        with patch("kubernetes_asyncio.client.CustomObjectsApi", return_value=real_api):
            await _annotate_task_completion(
                "task-1",
                "taskspec-ns",
                "job-1",
                "executor-ns",
                "succeeded",
            )

        assert len(recording_client.calls) == 1
        call = cast(dict[str, Any], recording_client.calls[0])
        assert (
            call["resource_path"]
            == "/apis/{group}/{version}/namespaces/{namespace}/{plural}/{name}"
        )
        assert call["method"] == "PATCH"
        assert call["path_params"] == {
            "group": "nubi.io",
            "version": "v1",
            "namespace": "taskspec-ns",
            "plural": "taskspecs",
            "name": "task-1",
        }
        assert call["header_params"]["Content-Type"] == "application/merge-patch+json"
        assert call["kwargs"]["body"] == {
            "metadata": {
                "annotations": {
                    JOB_STATUS_ANNOTATION: "succeeded",
                    "nubi.io/job-name": "job-1",
                    "nubi.io/job-namespace": "executor-ns",
                }
            }
        }


class TestOnJobCompletionAnnotationRegistration:
    def test_field_registration_uses_path_segments_for_annotation_key(self) -> None:
        source = inspect.getsource(on_job_completion_annotation)
        module = ast.parse(source)
        function_def = module.body[0]

        assert isinstance(function_def, ast.AsyncFunctionDef)
        assert function_def.decorator_list

        decorator = function_def.decorator_list[0]
        assert isinstance(decorator, ast.Call)

        field_keyword = next(keyword for keyword in decorator.keywords if keyword.arg == "field")
        field_value = field_keyword.value

        assert isinstance(field_value, (ast.Tuple, ast.List))
        assert len(field_value.elts) == 3
        assert isinstance(field_value.elts[0], ast.Constant)
        assert field_value.elts[0].value == "metadata"
        assert isinstance(field_value.elts[1], ast.Constant)
        assert field_value.elts[1].value == "annotations"
        assert isinstance(field_value.elts[2], ast.Name | ast.Constant)
        if isinstance(field_value.elts[2], ast.Constant):
            assert field_value.elts[2].value == JOB_STATUS_ANNOTATION
        else:
            assert field_value.elts[2].id == "JOB_STATUS_ANNOTATION"


# -- on_job_completion_annotation — field handler ----------------------------


class TestOnJobCompletionAnnotation:
    @pytest.fixture
    def mock_secret(self) -> object:
        return type("Secret", (), {"data": {"github-token": "Z2hwX3Rlc3Q="}})()

    async def test_ignores_processed_annotation(self) -> None:
        fp = FakePatch()
        await on_job_completion_annotation(
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
        await on_job_completion_annotation(
            spec=VALID_SPEC,
            name="task-1",
            namespace="ns",
            status={},
            patch=fp,
            old=None,
            new="failed",
        )
        assert fp.status.get("phase") == "Failed"
        assert fp.meta.annotations.get(JOB_STATUS_ANNOTATION) == "processed"

    @patch("nubi.controller.handlers.read_gates_result", new_callable=AsyncMock)
    @patch("nubi.controller.handlers.read_executor_result", new_callable=AsyncMock)
    async def test_succeeded_job_sets_done_phase(
        self,
        mock_result: AsyncMock,
        mock_gates_result: AsyncMock,
        mock_secret: object,
    ) -> None:
        mock_result.return_value = ExecutorResult(
            status="success", commit_sha="abc123", summary="Done"
        )
        mock_gates_result.return_value = GatesResult(discovered=[], gates=[], overall_passed=True)

        fp = FakePatch()
        with patch("kubernetes_asyncio.client.CoreV1Api") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.read_namespaced_secret = AsyncMock(return_value=mock_secret)
            await on_job_completion_annotation(
                spec=VALID_SPEC,
                name="task-1",
                namespace="ns",
                status={},
                patch=fp,
                old=None,
                new="succeeded",
            )
        assert fp.status.get("phase") == "Done"
        assert fp.meta.annotations.get(JOB_STATUS_ANNOTATION) == "processed"

    @patch("nubi.controller.handlers.read_gates_result", new_callable=AsyncMock)
    @patch("nubi.controller.handlers.read_executor_result", new_callable=AsyncMock)
    async def test_gate_failure_below_retry_limit_returns_to_executing(
        self,
        mock_result: AsyncMock,
        mock_gates_result: AsyncMock,
        mock_secret: object,
    ) -> None:
        mock_result.return_value = ExecutorResult(
            status="success", commit_sha="abc123", summary="Done"
        )
        mock_gates_result.return_value = GatesResult(
            discovered=[],
            gates=[GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.FAILED)],
            overall_passed=False,
            attempt=1,
        )

        fp = FakePatch()
        with patch("kubernetes_asyncio.client.CoreV1Api") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.read_namespaced_secret = AsyncMock(return_value=mock_secret)
            await on_job_completion_annotation(
                spec={
                    **VALID_SPEC,
                    "loopPolicy": {"max_retries": 2, "on_max_retries": "escalate"},
                },
                name="task-1",
                namespace="ns",
                status={"stages": {"executor": {"attempts": 1}}},
                patch=fp,
                old=None,
                new="succeeded",
            )

        assert fp.status.get("phase") == "Executing"
        assert fp.meta.annotations.get(JOB_STATUS_ANNOTATION) == "processed"

    @patch("nubi.controller.handlers.read_gates_result", new_callable=AsyncMock)
    @patch("nubi.controller.handlers.read_executor_result", new_callable=AsyncMock)
    async def test_gate_failure_at_retry_limit_sets_escalated(
        self,
        mock_result: AsyncMock,
        mock_gates_result: AsyncMock,
        mock_secret: object,
    ) -> None:
        mock_result.return_value = ExecutorResult(
            status="success", commit_sha="abc123", summary="Done"
        )
        mock_gates_result.return_value = GatesResult(
            discovered=[],
            gates=[GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.FAILED)],
            overall_passed=False,
            attempt=1,
        )

        fp = FakePatch()
        with patch("kubernetes_asyncio.client.CoreV1Api") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.read_namespaced_secret = AsyncMock(return_value=mock_secret)
            await on_job_completion_annotation(
                spec={
                    **VALID_SPEC,
                    "loopPolicy": {"max_retries": 1, "on_max_retries": "escalate"},
                },
                name="task-1",
                namespace="ns",
                status={"stages": {"executor": {"attempts": 1}}},
                patch=fp,
                old=None,
                new="succeeded",
            )

        assert fp.status.get("phase") == "Escalated"
        assert fp.meta.annotations.get(JOB_STATUS_ANNOTATION) == "processed"
