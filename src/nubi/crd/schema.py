"""Pydantic v2 models for the Nubi TaskSpec CRD."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nubi.agents.gate_result import (  # noqa: F401
    GateCategory,
    GatePolicy,
    GateStatus,
    GateThreshold,
)
from nubi.crd.defaults import (
    DEFAULT_BRANCH,
    DEFAULT_DECOMPOSITION_ALLOW,
    DEFAULT_DECOMPOSITION_MAX_DEPTH,
    DEFAULT_DECOMPOSITION_MAX_SUBTASKS,
    DEFAULT_MAX_CI_RETRIES,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MONITORING_SUMMARY,
    DEFAULT_ON_MAX_RETRIES,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_PR_DRAFT,
    DEFAULT_PR_TITLE_PREFIX,
    DEFAULT_RESOURCE_CPU,
    DEFAULT_RESOURCE_MEMORY,
    DEFAULT_REVIEW_ENABLED,
    DEFAULT_TIMEOUT,
    DEFAULT_TOTAL_TIMEOUT,
)

# -- Enums -------------------------------------------------------------------


class TaskType(StrEnum):
    CODE_CHANGE = "code-change"
    RESEARCH = "research"
    REFACTOR = "refactor"
    DOCS = "docs"


class Phase(StrEnum):
    PENDING = "Pending"
    PLANNING = "Planning"
    EXECUTING = "Executing"
    GATING = "Gating"
    VALIDATING = "Validating"
    REVIEWING = "Reviewing"
    MONITORING = "Monitoring"
    SUMMARIZING = "Summarizing"
    DONE = "Done"
    FAILED = "Failed"
    ESCALATED = "Escalated"


class OnMaxRetries(StrEnum):
    ESCALATE = "escalate"
    ABANDON = "abandon"


class OutputFormat(StrEnum):
    PR = "pr"
    BRANCH = "branch"
    PATCH = "patch"


class NotifyChannelType(StrEnum):
    DISCORD = "discord"
    TELEGRAM = "telegram"
    SLACK = "slack"


class CheckResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


# -- Frozen spec models ------------------------------------------------------


class ResourceConstraints(BaseModel):
    model_config = ConfigDict(frozen=True)

    cpu: str = Field(default=DEFAULT_RESOURCE_CPU)
    memory: str = Field(default=DEFAULT_RESOURCE_MEMORY)


class TaskConstraints(BaseModel):
    model_config = ConfigDict(frozen=True)

    timeout: str = Field(default=DEFAULT_TIMEOUT)
    total_timeout: str = Field(default=DEFAULT_TOTAL_TIMEOUT)
    network_access: list[str] = Field(default_factory=lambda: ["github.com"])
    tools: list[str] = Field(default_factory=list)
    resources: ResourceConstraints = Field(default_factory=ResourceConstraints)


class TaskInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo: str
    branch: str = Field(default=DEFAULT_BRANCH)
    files_of_interest: list[str] = Field(default_factory=list)

    @field_validator("repo")
    @classmethod
    def normalize_repo(cls, v: str) -> str:
        from nubi.tools.git import normalize_repo

        return normalize_repo(v)


class TaskValidation(BaseModel):
    model_config = ConfigDict(frozen=True)

    deterministic: list[str] = Field(default_factory=list)
    agentic: list[str] = Field(default_factory=list)


class TaskReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=DEFAULT_REVIEW_ENABLED)
    focus: list[str] = Field(default_factory=list)


class LoopPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(default=DEFAULT_MAX_RETRIES)
    max_ci_retries: int = Field(default=DEFAULT_MAX_CI_RETRIES)
    validator_to_executor: bool = Field(default=True)
    reviewer_to_executor: bool = Field(default=True)
    reviewer_to_planner: bool = Field(default=False)
    on_max_retries: OnMaxRetries = Field(default=OnMaxRetries(DEFAULT_ON_MAX_RETRIES))


class PROutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    title_prefix: str = Field(default=DEFAULT_PR_TITLE_PREFIX)
    labels: list[str] = Field(default_factory=list)
    draft: bool = Field(default=DEFAULT_PR_DRAFT)


class TaskOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    format: OutputFormat = Field(default=OutputFormat(DEFAULT_OUTPUT_FORMAT))
    pr: PROutput | None = Field(default=None)


class TaskDecomposition(BaseModel):
    model_config = ConfigDict(frozen=True)

    allow: bool = Field(default=DEFAULT_DECOMPOSITION_ALLOW)
    max_depth: int = Field(default=DEFAULT_DECOMPOSITION_MAX_DEPTH)
    max_subtasks: int = Field(default=DEFAULT_DECOMPOSITION_MAX_SUBTASKS)


class NotifyChannel(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: NotifyChannelType
    target: str


class TaskMonitoring(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: bool = Field(default=DEFAULT_MONITORING_SUMMARY)
    notify: list[NotifyChannel] = Field(default_factory=list)


class TaskSpecSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    type: TaskType
    inputs: TaskInputs
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    validation: TaskValidation = Field(default_factory=TaskValidation)
    review: TaskReview = Field(default_factory=TaskReview)
    loop_policy: LoopPolicy = Field(default_factory=LoopPolicy)
    output: TaskOutput = Field(default_factory=TaskOutput)
    decomposition: TaskDecomposition = Field(default_factory=TaskDecomposition)
    monitoring: TaskMonitoring = Field(default_factory=TaskMonitoring)
    gate_policy: GatePolicy = Field(default_factory=GatePolicy)


# -- Mutable status models ---------------------------------------------------


class WorkspaceStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    namespace: str = Field(default="")
    repo: str = Field(default="")
    branch: str = Field(default="")
    head_sha: str = Field(default="", alias="headSHA")


class DeterministicResults(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lint: str = Field(default="")
    tests: str = Field(default="")
    secret_scan: str = Field(default="")


class ExecutorStageStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="pending")
    attempts: int = Field(default=0)
    commit_sha: str = Field(default="", alias="commitSHA")
    summary: str = Field(default="")


class ValidatorStageStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="pending")
    deterministic: DeterministicResults = Field(default_factory=DeterministicResults)
    test_commit_sha: str = Field(default="", alias="testCommitSHA")


class ReviewerStageStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="pending")
    feedback: str = Field(default="")
    decision: str = Field(default="")


class GatingStageStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="pending")
    passed: bool = Field(default=False)
    attempt: int = Field(default=0)


class MonitorStageStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="pending")
    decision: str = Field(default="")
    summary: str = Field(default="")
    concerns: list[dict[str, str]] = Field(default_factory=list)
    pr_url: str = Field(default="", alias="prURL")


class StageStatuses(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    executor: ExecutorStageStatus = Field(default_factory=ExecutorStageStatus)
    validator: ValidatorStageStatus = Field(default_factory=ValidatorStageStatus)
    reviewer: ReviewerStageStatus = Field(default_factory=ReviewerStageStatus)
    gating: GatingStageStatus = Field(default_factory=GatingStageStatus)
    monitor: MonitorStageStatus = Field(default_factory=MonitorStageStatus)


class TaskSpecStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    phase: Phase = Field(default=Phase.PENDING)
    phase_changed_at: str = Field(default="", alias="phaseChangedAt")
    workspace: WorkspaceStatus = Field(default_factory=WorkspaceStatus)
    stages: StageStatuses = Field(default_factory=StageStatuses)


# -- Top-level resource ------------------------------------------------------


class TaskSpecResource(BaseModel):
    metadata_name: str
    metadata_namespace: str = Field(default="nubi-system")
    spec: TaskSpecSpec
    status: TaskSpecStatus = Field(default_factory=TaskSpecStatus)
