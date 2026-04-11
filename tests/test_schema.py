"""Tests for nubi.crd.schema — TaskSpec CRD Pydantic models."""

import pytest
from pydantic import ValidationError

from nubi.crd.schema import (
    Phase,
    TaskInputs,
    TaskSpecResource,
    TaskSpecSpec,
    TaskSpecStatus,
)

# -- Fixtures ----------------------------------------------------------------

FULL_SPEC: dict = {
    "description": "Add rate limiting to API endpoints",
    "type": "code-change",
    "inputs": {
        "repo": "kuuji/some-app",
        "branch": "main",
        "files_of_interest": ["src/api/routes.py"],
    },
    "constraints": {
        "timeout": "300s",
        "total_timeout": "1800s",
        "network_access": ["github.com", "pypi.org"],
        "tools": ["shell", "git", "file_read", "file_write"],
        "resources": {"cpu": "1", "memory": "512Mi"},
    },
    "validation": {
        "deterministic": ["lint", "test", "secret_scan"],
        "agentic": ["intent_match", "completeness"],
    },
    "review": {
        "enabled": True,
        "focus": ["code_quality", "architecture_fit"],
    },
    "loop_policy": {
        "max_retries": 2,
        "validator_to_executor": True,
        "reviewer_to_executor": True,
        "reviewer_to_planner": False,
        "on_max_retries": "escalate",
    },
    "output": {
        "format": "pr",
        "pr": {
            "title_prefix": "nubi:",
            "labels": ["nubi", "automated"],
            "draft": True,
        },
    },
    "decomposition": {
        "allow": False,
        "max_depth": 2,
        "max_subtasks": 5,
    },
    "monitoring": {
        "summary": True,
        "notify": [{"channel": "discord", "target": "chan-123"}],
    },
}

MINIMAL_SPEC: dict = {
    "description": "Fix a bug",
    "type": "code-change",
    "inputs": {"repo": "kuuji/some-app"},
}


# -- 1. Full example round-trip ----------------------------------------------


class TestFullExampleSpec:
    def test_parse_full_example(self):
        spec = TaskSpecSpec.model_validate(FULL_SPEC)
        assert spec.description == "Add rate limiting to API endpoints"
        assert spec.inputs.repo == "kuuji/some-app"
        assert spec.inputs.branch == "main"
        assert spec.inputs.files_of_interest == ["src/api/routes.py"]
        assert spec.constraints.timeout == "300s"
        assert spec.constraints.network_access == ["github.com", "pypi.org"]
        assert spec.constraints.resources.cpu == "1"
        assert spec.constraints.resources.memory == "512Mi"
        assert spec.validation.deterministic == ["lint", "test", "secret_scan"]
        assert spec.review.enabled is True
        assert spec.review.focus == ["code_quality", "architecture_fit"]
        assert spec.loop_policy.max_retries == 2
        assert spec.output.pr is not None
        assert spec.output.pr.title_prefix == "nubi:"
        assert spec.output.pr.labels == ["nubi", "automated"]
        assert spec.decomposition.allow is False
        assert spec.monitoring.summary is True
        assert len(spec.monitoring.notify) == 1
        assert spec.monitoring.notify[0].channel == "discord"


# -- 2. Minimal spec — defaults applied --------------------------------------


class TestMinimalSpecDefaults:
    def test_creates_successfully(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.description == "Fix a bug"

    def test_default_branch(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.inputs.branch == "main"

    def test_default_constraints(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.constraints.timeout == "300s"
        assert spec.constraints.total_timeout == "1800s"
        assert spec.constraints.resources.cpu == "1"
        assert spec.constraints.resources.memory == "512Mi"

    def test_default_loop_policy(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.loop_policy.max_retries == 2

    def test_default_review_enabled(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.review.enabled is True

    def test_default_output_format(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.output.format == "pr"

    def test_default_decomposition_disabled(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.decomposition.allow is False


# -- Repo normalization in TaskInputs ----------------------------------------


class TestTaskInputsRepoNormalization:
    def test_normalizes_full_url(self) -> None:
        inputs = TaskInputs(repo="https://github.com/kuuji/nubi")
        assert inputs.repo == "kuuji/nubi"

    def test_normalizes_url_with_git_suffix(self) -> None:
        inputs = TaskInputs(repo="https://github.com/kuuji/nubi.git")
        assert inputs.repo == "kuuji/nubi"

    def test_passes_through_owner_repo(self) -> None:
        inputs = TaskInputs(repo="kuuji/nubi")
        assert inputs.repo == "kuuji/nubi"

    def test_rejects_invalid_repo(self) -> None:
        with pytest.raises(ValidationError, match="Invalid repo format"):
            TaskInputs(repo="nubi")


# -- 3. Invalid type raises ValidationError ----------------------------------


class TestInvalidType:
    def test_invalid_type_value(self):
        bad = {**MINIMAL_SPEC, "type": "banana"}
        with pytest.raises(ValidationError):
            TaskSpecSpec.model_validate(bad)

    def test_missing_required_description(self):
        no_desc = {"type": "code-change", "inputs": {"repo": "x"}}
        with pytest.raises(ValidationError):
            TaskSpecSpec.model_validate(no_desc)

    def test_missing_required_inputs(self):
        no_inputs = {"description": "x", "type": "code-change"}
        with pytest.raises(ValidationError):
            TaskSpecSpec.model_validate(no_inputs)


# -- 4. TaskSpecStatus defaults -----------------------------------------------


class TestStatusDefaults:
    def test_default_phase_is_pending(self):
        status = TaskSpecStatus()
        assert status.phase == Phase.PENDING

    def test_default_workspace_empty(self):
        status = TaskSpecStatus()
        assert status.workspace.repo == ""
        assert status.workspace.branch == ""
        assert status.workspace.head_sha == ""

    def test_default_stages_pending(self):
        status = TaskSpecStatus()
        assert status.stages.executor.status == "pending"
        assert status.stages.validator.status == "pending"
        assert status.stages.reviewer.status == "pending"


# -- 5. Phase enum has all expected values ------------------------------------


class TestPhaseEnum:
    EXPECTED = [
        "Pending",
        "Planning",
        "Executing",
        "Gating",
        "Validating",
        "Reviewing",
        "Monitoring",
        "Summarizing",
        "Done",
        "Failed",
        "Escalated",
    ]

    def test_all_phases_exist(self):
        for value in self.EXPECTED:
            assert Phase(value) == value

    def test_phase_count(self):
        assert len(Phase) == 11


# -- 6. Spec models are frozen -----------------------------------------------


class TestFrozenSpec:
    def test_spec_is_frozen(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        with pytest.raises(ValidationError):
            spec.description = "changed"  # type: ignore[misc]


# -- 7. Status models are mutable --------------------------------------------


class TestMutableStatus:
    def test_status_phase_can_be_changed(self):
        status = TaskSpecStatus()
        status.phase = Phase.EXECUTING  # type: ignore[assignment]
        assert status.phase == Phase.EXECUTING


# -- 8. TaskSpecResource construction ----------------------------------------


class TestTaskSpecResource:
    def test_construct_resource(self):
        resource = TaskSpecResource(
            metadata_name="test-task",
            metadata_namespace="nubi-system",
            spec=TaskSpecSpec.model_validate(FULL_SPEC),
        )
        assert resource.metadata_name == "test-task"
        assert resource.metadata_namespace == "nubi-system"
        assert resource.spec.description == "Add rate limiting to API endpoints"
        assert resource.status.phase == Phase.PENDING

    def test_serialize_to_dict(self):
        resource = TaskSpecResource(
            metadata_name="test-task",
            spec=TaskSpecSpec.model_validate(FULL_SPEC),
        )
        data = resource.model_dump()
        assert data["metadata_name"] == "test-task"
        assert "spec" in data
        assert "status" in data


# -- 9. camelCase alias handling ----------------------------------------------


class TestCamelCaseAliases:
    def test_construct_workspace_with_camel_case(self):
        from nubi.crd.schema import WorkspaceStatus

        ws = WorkspaceStatus.model_validate({"repo": "r", "branch": "b", "headSHA": "abc123"})
        assert ws.head_sha == "abc123"

    def test_construct_executor_with_camel_case(self):
        from nubi.crd.schema import ExecutorStageStatus

        es = ExecutorStageStatus.model_validate(
            {"status": "complete", "attempts": 1, "commitSHA": "def456", "summary": "done"}
        )
        assert es.commit_sha == "def456"

    def test_construct_validator_with_camel_case(self):
        from nubi.crd.schema import ValidatorStageStatus

        vs = ValidatorStageStatus.model_validate({"status": "complete", "testCommitSHA": "ghi789"})
        assert vs.test_commit_sha == "ghi789"
