"""Tests for nubi.controller.credentials — per-stage Secret projection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from nubi.controller.credentials import STAGE_CREDENTIALS, ensure_stage_secret
from nubi.exceptions import CredentialError

# -- Helpers -----------------------------------------------------------------


def _api_exc(status: int, reason: str = "Error") -> ApiException:
    resp = MagicMock()
    resp.status = status
    resp.reason = reason
    resp.data = ""
    return ApiException(status=status, reason=reason, http_resp=resp)


def _master_secret() -> MagicMock:
    """Fake master Secret with both credential keys."""
    secret = MagicMock()
    secret.data = {
        "github-token": "Z2h0b2tlbg==",
        "llm-api-key": "bGxtc2VjcmV0",
    }
    return secret


# -- STAGE_CREDENTIALS mapping -----------------------------------------------


class TestStageCredentials:
    def test_executor_gets_both(self) -> None:
        assert "github-token" in STAGE_CREDENTIALS["executor"]
        assert "llm-api-key" in STAGE_CREDENTIALS["executor"]

    def test_validator_gets_both(self) -> None:
        assert "github-token" in STAGE_CREDENTIALS["validator"]
        assert "llm-api-key" in STAGE_CREDENTIALS["validator"]

    def test_reviewer_gets_only_llm(self) -> None:
        assert "github-token" not in STAGE_CREDENTIALS["reviewer"]
        assert "llm-api-key" in STAGE_CREDENTIALS["reviewer"]

    def test_gate_gets_nothing(self) -> None:
        assert STAGE_CREDENTIALS["gate"] == []


# -- ensure_stage_secret — executor ------------------------------------------


class TestEnsureStageSecretExecutor:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        core_p = patch("nubi.controller.credentials.CoreV1Api")
        self.mock_core_cls = core_p.start()
        self.mock_core = MagicMock()
        self.mock_core_cls.return_value = self.mock_core

        self.mock_core.read_namespaced_secret = AsyncMock(return_value=_master_secret())
        self.mock_core.create_namespaced_secret = AsyncMock()

        yield
        core_p.stop()

    async def test_returns_secret_name(self) -> None:
        result = await ensure_stage_secret("nubi-task-1", "task-1", "executor")
        assert result == "nubi-executor-credentials"

    async def test_reads_master_secret(self) -> None:
        await ensure_stage_secret("nubi-task-1", "task-1", "executor")
        self.mock_core.read_namespaced_secret.assert_awaited_once_with(
            "nubi-credentials", "nubi-system"
        )

    async def test_creates_secret_with_both_keys(self) -> None:
        await ensure_stage_secret("nubi-task-1", "task-1", "executor")
        call_args = self.mock_core.create_namespaced_secret.call_args
        body = call_args.kwargs.get("body") or call_args[0][0]
        assert "github-token" in body.data
        assert "llm-api-key" in body.data

    async def test_secret_labels(self) -> None:
        await ensure_stage_secret("nubi-task-1", "task-1", "executor")
        call_args = self.mock_core.create_namespaced_secret.call_args
        body = call_args.kwargs.get("body") or call_args[0][0]
        labels = body.metadata.labels
        assert labels["nubi.io/task-id"] == "task-1"
        assert labels["nubi.io/stage"] == "executor"
        assert labels["app.kubernetes.io/managed-by"] == "nubi"

    async def test_creates_in_task_namespace(self) -> None:
        await ensure_stage_secret("nubi-task-1", "task-1", "executor")
        call_args = self.mock_core.create_namespaced_secret.call_args
        assert call_args.kwargs.get("namespace") == "nubi-task-1"


# -- ensure_stage_secret — reviewer ------------------------------------------


class TestEnsureStageSecretReviewer:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        core_p = patch("nubi.controller.credentials.CoreV1Api")
        self.mock_core_cls = core_p.start()
        self.mock_core = MagicMock()
        self.mock_core_cls.return_value = self.mock_core

        self.mock_core.read_namespaced_secret = AsyncMock(return_value=_master_secret())
        self.mock_core.create_namespaced_secret = AsyncMock()

        yield
        core_p.stop()

    async def test_returns_secret_name(self) -> None:
        result = await ensure_stage_secret("nubi-task-1", "task-1", "reviewer")
        assert result == "nubi-reviewer-credentials"

    async def test_only_llm_key(self) -> None:
        await ensure_stage_secret("nubi-task-1", "task-1", "reviewer")
        call_args = self.mock_core.create_namespaced_secret.call_args
        body = call_args.kwargs.get("body") or call_args[0][0]
        assert "llm-api-key" in body.data
        assert "github-token" not in body.data


# -- ensure_stage_secret — gate (no credentials) ----------------------------


class TestEnsureStageSecretGate:
    async def test_raises_credential_error(self) -> None:
        with pytest.raises(CredentialError, match="no credentials"):
            await ensure_stage_secret("nubi-task-1", "task-1", "gate")

    async def test_unknown_stage_raises(self) -> None:
        with pytest.raises(CredentialError, match="Unknown stage"):
            await ensure_stage_secret("nubi-task-1", "task-1", "nonexistent")


# -- ensure_stage_secret — idempotency / errors ------------------------------


class TestEnsureStageSecretIdempotency:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        core_p = patch("nubi.controller.credentials.CoreV1Api")
        self.mock_core_cls = core_p.start()
        self.mock_core = MagicMock()
        self.mock_core_cls.return_value = self.mock_core

        self.mock_core.read_namespaced_secret = AsyncMock(return_value=_master_secret())
        self.mock_core.create_namespaced_secret = AsyncMock()

        yield
        core_p.stop()

    async def test_409_returns_name(self) -> None:
        self.mock_core.create_namespaced_secret.side_effect = _api_exc(409, "Conflict")
        result = await ensure_stage_secret("nubi-task-1", "task-1", "executor")
        assert result == "nubi-executor-credentials"

    async def test_500_on_create_raises(self) -> None:
        self.mock_core.create_namespaced_secret.side_effect = _api_exc(500, "Internal")
        with pytest.raises(CredentialError):
            await ensure_stage_secret("nubi-task-1", "task-1", "executor")

    async def test_500_on_read_master_raises(self) -> None:
        self.mock_core.read_namespaced_secret.side_effect = _api_exc(500, "Internal")
        with pytest.raises(CredentialError):
            await ensure_stage_secret("nubi-task-1", "task-1", "executor")
