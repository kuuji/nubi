"""Tests for nubi.controller.sandbox — gVisor Job builder."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from nubi.controller.sandbox import (
    build_executor_job,
    build_reviewer_job,
    create_executor_job,
    create_reviewer_job,
    parse_duration,
)
from nubi.crd.defaults import LABEL_TASKSPEC_NAMESPACE
from nubi.crd.schema import TaskSpecSpec
from nubi.exceptions import SandboxError

# -- Helpers -----------------------------------------------------------------


def _api_exc(status: int, reason: str = "Error") -> ApiException:
    resp = MagicMock()
    resp.status = status
    resp.reason = reason
    resp.data = ""
    return ApiException(status=status, reason=reason, http_resp=resp)


def _spec(**overrides: object) -> TaskSpecSpec:
    """Build a minimal TaskSpecSpec."""
    base: dict = {
        "description": "Add rate limiting",
        "type": "code-change",
        "inputs": {"repo": "kuuji/some-app", "branch": "main"},
        "constraints": {
            "timeout": "300s",
            "tools": ["shell", "git", "file_read"],
            "resources": {"cpu": "1", "memory": "512Mi"},
        },
    }
    base.update(overrides)
    return TaskSpecSpec.model_validate(base)


def _build(**kw: object) -> object:
    """Build an executor Job with defaults."""
    defaults: dict = {
        "task_name": "task-1",
        "ns_name": "nubi-task-1",
        "spec": _spec(),
        "secret_name": "nubi-executor-credentials",
        "taskspec_namespace": "nubi-system",
    }
    defaults.update(kw)
    return build_executor_job(**defaults)


# -- parse_duration ----------------------------------------------------------


class TestParseDuration:
    def test_300s(self) -> None:
        assert parse_duration("300s") == 300

    def test_60s(self) -> None:
        assert parse_duration("60s") == 60

    def test_bare_number_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("300")

    def test_minutes_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("5m")

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("abc")


# -- build_executor_job — metadata -------------------------------------------


class TestBuildExecutorJobMetadata:
    def test_job_name_format(self) -> None:
        job = _build(task_name="task-1")
        assert job.metadata.name == "nubi-executor-task-1"

    def test_job_name_truncated_to_63(self) -> None:
        job = _build(task_name="a" * 200)
        assert len(job.metadata.name) <= 63

    def test_job_namespace(self) -> None:
        job = _build(ns_name="nubi-task-1")
        assert job.metadata.namespace == "nubi-task-1"

    def test_labels(self) -> None:
        job = _build(task_name="task-1")
        labels = job.metadata.labels
        assert labels["nubi.io/task-id"] == "task-1"
        assert labels["nubi.io/stage"] == "executor"
        assert labels[LABEL_TASKSPEC_NAMESPACE] == "nubi-system"
        assert labels["app.kubernetes.io/managed-by"] == "nubi"

    def test_no_owner_references(self) -> None:
        job = _build(task_name="task-1")
        assert job.metadata.owner_references is None


# -- build_executor_job — job spec -------------------------------------------


class TestBuildExecutorJobSpec:
    def test_backoff_limit_zero(self) -> None:
        job = _build()
        assert job.spec.backoff_limit == 0

    def test_active_deadline_seconds(self) -> None:
        job = _build(spec=_spec(constraints={"timeout": "600s", "resources": {}}))
        assert job.spec.active_deadline_seconds == 600

    def test_restart_policy_never(self) -> None:
        job = _build()
        assert job.spec.template.spec.restart_policy == "Never"

    def test_gvisor_runtime_class(self) -> None:
        job = _build()
        assert job.spec.template.spec.runtime_class_name == "gvisor"

    def test_runtime_class_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUBI_RUNTIME_CLASS", "kata")
        job = _build()
        assert job.spec.template.spec.runtime_class_name == "kata"

    def test_runtime_class_empty_omits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUBI_RUNTIME_CLASS", "")
        job = _build()
        assert job.spec.template.spec.runtime_class_name is None


# -- build_executor_job — container ------------------------------------------


class TestBuildExecutorJobContainer:
    def _container(self, **kw: object) -> object:
        job = _build(**kw)
        return job.spec.template.spec.containers[0]

    def test_name(self) -> None:
        c = self._container()
        assert c.name == "executor"

    def test_image(self) -> None:
        c = self._container()
        assert c.image == "ghcr.io/kuuji/nubi-agent:latest"

    def test_image_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUBI_AGENT_IMAGE", "my-registry/custom-agent:v1")
        c = self._container()
        assert c.image == "my-registry/custom-agent:v1"

    def test_image_pull_policy_default_none(self) -> None:
        c = self._container()
        assert c.image_pull_policy is None

    def test_image_pull_policy_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUBI_AGENT_IMAGE_PULL_POLICY", "IfNotPresent")
        c = self._container()
        assert c.image_pull_policy == "IfNotPresent"

    def test_working_dir(self) -> None:
        c = self._container()
        assert c.working_dir == "/workspace"

    def test_resource_limits(self) -> None:
        c = self._container()
        assert c.resources.limits["cpu"] == "1"
        assert c.resources.limits["memory"] == "512Mi"

    def test_resource_requests(self) -> None:
        c = self._container()
        assert c.resources.requests["cpu"] == "1"
        assert c.resources.requests["memory"] == "512Mi"

    def test_workspace_volume_mount(self) -> None:
        c = self._container()
        mounts = c.volume_mounts
        ws = [m for m in mounts if m.name == "workspace"]
        assert len(ws) == 1
        assert ws[0].mount_path == "/workspace"


# -- build_executor_job — security context -----------------------------------


class TestBuildExecutorJobSecurity:
    def _sec_ctx(self) -> object:
        job = _build()
        return job.spec.template.spec.containers[0].security_context

    def test_run_as_non_root(self) -> None:
        assert self._sec_ctx().run_as_non_root is True

    def test_run_as_user_nobody(self) -> None:
        assert self._sec_ctx().run_as_user == 65534

    def test_no_privilege_escalation(self) -> None:
        assert self._sec_ctx().allow_privilege_escalation is False

    def test_read_only_root_fs_disabled(self) -> None:
        assert self._sec_ctx().read_only_root_filesystem is False

    def test_drop_all_capabilities(self) -> None:
        assert self._sec_ctx().capabilities.drop == ["ALL"]

    def test_seccomp_runtime_default(self) -> None:
        assert self._sec_ctx().seccomp_profile.type == "RuntimeDefault"


# -- build_executor_job — env vars ------------------------------------------


class TestBuildExecutorJobEnvVars:
    def _env(self) -> list:
        job = _build()
        return job.spec.template.spec.containers[0].env

    def _env_by_name(self, name: str) -> object:
        for e in self._env():
            if e.name == name:
                return e
        raise AssertionError(f"Env var {name} not found")

    def test_github_token_from_secret(self) -> None:
        e = self._env_by_name("GITHUB_TOKEN")
        assert e.value_from.secret_key_ref.name == "nubi-executor-credentials"
        assert e.value_from.secret_key_ref.key == "github-token"

    def test_llm_api_key_from_secret(self) -> None:
        e = self._env_by_name("LLM_API_KEY")
        assert e.value_from.secret_key_ref.name == "nubi-executor-credentials"
        assert e.value_from.secret_key_ref.key == "llm-api-key"

    def test_nubi_task_id(self) -> None:
        e = self._env_by_name("NUBI_TASK_ID")
        assert e.value == "task-1"

    def test_nubi_repo(self) -> None:
        e = self._env_by_name("NUBI_REPO")
        assert e.value == "kuuji/some-app"

    def test_nubi_branch(self) -> None:
        e = self._env_by_name("NUBI_BRANCH")
        assert e.value == "main"

    def test_nubi_description(self) -> None:
        e = self._env_by_name("NUBI_DESCRIPTION")
        assert e.value == "Add rate limiting"

    def test_nubi_tools(self) -> None:
        e = self._env_by_name("NUBI_TOOLS")
        assert e.value == "shell,git,file_read"

    def test_git_safe_directory_via_env(self) -> None:
        assert self._env_by_name("GIT_CONFIG_COUNT").value == "1"
        assert self._env_by_name("GIT_CONFIG_KEY_0").value == "safe.directory"
        assert self._env_by_name("GIT_CONFIG_VALUE_0").value == "/workspace"


# -- build_executor_job — volumes -------------------------------------------


class TestBuildExecutorJobVolumes:
    def test_workspace_empty_dir(self) -> None:
        job = _build()
        volumes = job.spec.template.spec.volumes
        ws = [v for v in volumes if v.name == "workspace"]
        assert len(ws) == 1
        assert ws[0].empty_dir is not None


# -- create_executor_job -----------------------------------------------------


class TestCreateExecutorJob:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        batch_p = patch("nubi.controller.sandbox.BatchV1Api")
        self.mock_batch_cls = batch_p.start()
        self.mock_batch = MagicMock()
        self.mock_batch_cls.return_value = self.mock_batch
        self.mock_batch.create_namespaced_job = AsyncMock()

        yield
        batch_p.stop()

    async def test_returns_job_name(self) -> None:
        result = await create_executor_job(
            "task-1", "nubi-task-1", _spec(), "nubi-executor-credentials", "nubi-system"
        )
        assert result == "nubi-executor-task-1"

    async def test_calls_create_namespaced_job(self) -> None:
        await create_executor_job(
            "task-1", "nubi-task-1", _spec(), "nubi-executor-credentials", "nubi-system"
        )
        self.mock_batch.create_namespaced_job.assert_awaited_once()

    async def test_409_returns_name(self) -> None:
        self.mock_batch.create_namespaced_job.side_effect = _api_exc(409, "Conflict")
        result = await create_executor_job(
            "task-1", "nubi-task-1", _spec(), "nubi-executor-credentials", "nubi-system"
        )
        assert result == "nubi-executor-task-1"

    async def test_500_raises_sandbox_error(self) -> None:
        self.mock_batch.create_namespaced_job.side_effect = _api_exc(500, "Internal")
        with pytest.raises(SandboxError):
            await create_executor_job(
                "task-1", "nubi-task-1", _spec(), "nubi-executor-credentials", "nubi-system"
            )


# -- build_reviewer_job -------------------------------------------------------


def _build_reviewer(**kw: object) -> object:
    """Build a reviewer Job with defaults."""
    defaults: dict = {
        "task_name": "task-1",
        "ns_name": "nubi-task-1",
        "spec": _spec(),
        "secret_name": "nubi-reviewer-credentials",
        "taskspec_namespace": "nubi-system",
    }
    defaults.update(kw)
    return build_reviewer_job(**defaults)


class TestBuildReviewerJobMetadata:
    def test_job_name_format(self) -> None:
        job = _build_reviewer(task_name="task-1")
        assert job.metadata.name == "nubi-reviewer-task-1"

    def test_job_name_truncated_to_63(self) -> None:
        job = _build_reviewer(task_name="a" * 200)
        assert len(job.metadata.name) <= 63

    def test_labels_stage_reviewer(self) -> None:
        job = _build_reviewer(task_name="task-1")
        labels = job.metadata.labels
        assert labels["nubi.io/stage"] == "reviewer"
        assert labels["nubi.io/task-id"] == "task-1"


class TestBuildReviewerJobContainer:
    def _container(self, **kw: object) -> object:
        job = _build_reviewer(**kw)
        return job.spec.template.spec.containers[0]

    def test_name(self) -> None:
        c = self._container()
        assert c.name == "reviewer"

    def test_command_override(self) -> None:
        c = self._container()
        assert c.command == ["python", "-m", "nubi.reviewer_entrypoint"]

    def test_security_context_matches_executor(self) -> None:
        c = self._container()
        sec = c.security_context
        assert sec.run_as_non_root is True
        assert sec.run_as_user == 65534
        assert sec.allow_privilege_escalation is False
        assert sec.capabilities.drop == ["ALL"]


class TestBuildReviewerJobEnvVars:
    def _env(self) -> list:
        job = _build_reviewer()
        return job.spec.template.spec.containers[0].env

    def _env_by_name(self, name: str) -> object:
        for e in self._env():
            if e.name == name:
                return e
        raise AssertionError(f"Env var {name} not found")

    def test_nubi_tools_reviewer_set(self) -> None:
        e = self._env_by_name("NUBI_TOOLS")
        assert e.value == "shell,git_read,file_read,file_list,review"

    def test_nubi_review_focus(self) -> None:
        spec = _spec()
        # Default spec has no review focus
        job = build_reviewer_job("task-1", "ns", spec, "secret", "nubi-system")
        env = job.spec.template.spec.containers[0].env
        focus = [e for e in env if e.name == "NUBI_REVIEW_FOCUS"][0]
        assert focus.value == ""

    def test_github_token_from_secret(self) -> None:
        e = self._env_by_name("GITHUB_TOKEN")
        assert e.value_from.secret_key_ref.name == "nubi-reviewer-credentials"

    def test_llm_api_key_from_secret(self) -> None:
        e = self._env_by_name("LLM_API_KEY")
        assert e.value_from.secret_key_ref.name == "nubi-reviewer-credentials"

    def test_uses_reviewer_model_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUBI_MODEL_ID", "cheap/executor-model")
        monkeypatch.setenv("NUBI_REVIEWER_MODEL_ID", "smart/reviewer-model")
        job = _build_reviewer()
        env = job.spec.template.spec.containers[0].env
        model = [e for e in env if e.name == "NUBI_MODEL_ID"][0]
        assert model.value == "smart/reviewer-model"

    def test_falls_back_to_shared_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUBI_MODEL_ID", "shared/model")
        monkeypatch.delenv("NUBI_REVIEWER_MODEL_ID", raising=False)
        job = _build_reviewer()
        env = job.spec.template.spec.containers[0].env
        model = [e for e in env if e.name == "NUBI_MODEL_ID"][0]
        assert model.value == "shared/model"

    def test_uses_reviewer_provider_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUBI_LLM_PROVIDER", "openai")
        monkeypatch.setenv("NUBI_REVIEWER_LLM_PROVIDER", "anthropic")
        job = _build_reviewer()
        env = job.spec.template.spec.containers[0].env
        provider = [e for e in env if e.name == "NUBI_LLM_PROVIDER"][0]
        assert provider.value == "anthropic"

    def test_uses_reviewer_base_url_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUBI_LLM_BASE_URL", "https://openrouter.ai/api/v1")
        monkeypatch.setenv("NUBI_REVIEWER_LLM_BASE_URL", "https://api.anthropic.com")
        job = _build_reviewer()
        env = job.spec.template.spec.containers[0].env
        base_url = [e for e in env if e.name == "NUBI_LLM_BASE_URL"][0]
        assert base_url.value == "https://api.anthropic.com"


class TestCreateReviewerJob:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        batch_p = patch("nubi.controller.sandbox.BatchV1Api")
        self.mock_batch_cls = batch_p.start()
        self.mock_batch = MagicMock()
        self.mock_batch_cls.return_value = self.mock_batch
        self.mock_batch.create_namespaced_job = AsyncMock()

        yield
        batch_p.stop()

    async def test_returns_job_name(self) -> None:
        result = await create_reviewer_job(
            "task-1", "nubi-task-1", _spec(), "nubi-reviewer-credentials", "nubi-system"
        )
        assert result == "nubi-reviewer-task-1"

    async def test_409_returns_name(self) -> None:
        self.mock_batch.create_namespaced_job.side_effect = _api_exc(409, "Conflict")
        result = await create_reviewer_job(
            "task-1", "nubi-task-1", _spec(), "nubi-reviewer-credentials", "nubi-system"
        )
        assert result == "nubi-reviewer-task-1"

    async def test_500_raises_sandbox_error(self) -> None:
        self.mock_batch.create_namespaced_job.side_effect = _api_exc(500, "Internal")
        with pytest.raises(SandboxError):
            await create_reviewer_job(
                "task-1", "nubi-task-1", _spec(), "nubi-reviewer-credentials", "nubi-system"
            )
