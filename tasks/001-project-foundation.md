# Task 001: Project Foundation

## Goal

Set up the Nubi project scaffold, implement the TaskSpec CRD Pydantic schema, and create the kopf handler skeleton. This is the foundation everything else builds on.

## Scope

Three deliverables, all in one task because they're tightly coupled:

1. **Project scaffold** — pyproject.toml, src/nubi/ package tree, exceptions
2. **CRD schema** — Pydantic v2 models for TaskSpec spec and status
3. **Handler skeleton** — kopf handlers that validate specs and log intent

## Contracts

### Part 1: Project Scaffold

**`pyproject.toml`**
```toml
[project]
name = "nubi"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "kopf",
    "pydantic>=2.0",
    "kubernetes-asyncio",
    "strands-agents-builder",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
    "mypy",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.backends"

[tool.hatch.build.targets.wheel]
packages = ["src/nubi"]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**Package structure:**
```
src/nubi/__init__.py          # __version__ = "0.1.0"
src/nubi/controller/__init__.py  # empty
src/nubi/crd/__init__.py         # empty
src/nubi/agents/__init__.py      # empty
src/nubi/tools/__init__.py       # empty
src/nubi/output/__init__.py      # empty
src/nubi/exceptions.py
```

**`src/nubi/exceptions.py`**
```python
class NubiError(Exception):
    """Base exception for all nubi errors."""

class TaskSpecValidationError(NubiError):
    """Raised when a TaskSpec fails Pydantic validation."""

class PhaseTransitionError(NubiError):
    """Raised on an invalid phase transition."""

class HandlerError(NubiError):
    """Raised when a kopf handler encounters an error."""
```

### Part 2: CRD Schema

**`src/nubi/crd/defaults.py`** — Constants for all default values:
```python
DEFAULT_TIMEOUT = "300s"
DEFAULT_TOTAL_TIMEOUT = "1800s"
DEFAULT_MAX_RETRIES = 2
DEFAULT_ON_MAX_RETRIES = "escalate"
DEFAULT_OUTPUT_FORMAT = "pr"
DEFAULT_PR_TITLE_PREFIX = "nubi:"
DEFAULT_PR_DRAFT = True
DEFAULT_DECOMPOSITION_ALLOW = False
DEFAULT_DECOMPOSITION_MAX_DEPTH = 2
DEFAULT_DECOMPOSITION_MAX_SUBTASKS = 5
DEFAULT_REVIEW_ENABLED = True
DEFAULT_MONITORING_SUMMARY = True
DEFAULT_RESOURCE_CPU = "1"
DEFAULT_RESOURCE_MEMORY = "512Mi"
DEFAULT_BRANCH = "main"
```

**`src/nubi/crd/schema.py`** — Pydantic v2 models:

Enums (StrEnum):
- `TaskType`: `code-change`, `research`, `refactor`, `docs`
- `Phase`: `Pending`, `Planning`, `Executing`, `Gating`, `Validating`, `Reviewing`, `Summarizing`, `Done`, `Failed`, `Escalated`
- `OnMaxRetries`: `escalate`, `abandon`
- `OutputFormat`: `pr`, `branch`, `patch`
- `NotifyChannelType`: `discord`, `telegram`, `slack`
- `CheckResult`: `passed`, `failed`, `skipped`

Spec models (all with `model_config = ConfigDict(frozen=True)`):
- `ResourceConstraints(cpu: str, memory: str)`
- `TaskConstraints(timeout: str, total_timeout: str, network_access: list[str], tools: list[str], resources: ResourceConstraints)`
- `TaskInputs(repo: str, branch: str, files_of_interest: list[str])`
- `TaskValidation(deterministic: list[str], agentic: list[str])`
- `TaskReview(enabled: bool, focus: list[str])`
- `LoopPolicy(max_retries: int, validator_to_executor: bool, reviewer_to_executor: bool, reviewer_to_planner: bool, on_max_retries: OnMaxRetries)`
- `PROutput(title_prefix: str, labels: list[str], draft: bool)`
- `TaskOutput(format: OutputFormat, pr: PROutput | None)`
- `TaskDecomposition(allow: bool, max_depth: int, max_subtasks: int)`
- `NotifyChannel(channel: NotifyChannelType, target: str)`
- `TaskMonitoring(summary: bool, notify: list[NotifyChannel])`
- `TaskSpecSpec(description: str, type: TaskType, inputs: TaskInputs, constraints, validation, review, loop_policy, output, decomposition, monitoring)` — all optional fields have defaults from defaults.py

Status models (mutable, `ConfigDict(populate_by_name=True)`):
- `WorkspaceStatus(repo: str, branch: str, head_sha: str)` — head_sha aliased from "headSHA"
- `DeterministicResults(lint: CheckResult, tests: CheckResult, secret_scan: CheckResult)` — default empty string or a suitable default
- `ExecutorStageStatus(status: str, attempts: int, commit_sha: str, summary: str)` — commit_sha aliased from "commitSHA"
- `ValidatorStageStatus(status: str, deterministic: DeterministicResults, test_commit_sha: str)` — test_commit_sha aliased from "testCommitSHA"
- `ReviewerStageStatus(status: str, feedback: str, decision: str)`
- `StageStatuses(executor, validator, reviewer)`
- `TaskSpecStatus(phase: Phase, workspace: WorkspaceStatus, stages: StageStatuses)`

Top-level:
- `TaskSpecResource(metadata_name: str, metadata_namespace: str, spec: TaskSpecSpec, status: TaskSpecStatus)`

### Part 3: Handler Skeleton

**`src/nubi/controller/handlers.py`**

```python
@kopf.on.create("nubi.io", "v1", "taskspecs")
async def on_taskspec_created(spec: dict, name: str, namespace: str, patch: kopf.Patch, **kwargs) -> dict[str, str]:
    # Validate spec dict into TaskSpecSpec
    # Set patch.status["phase"] = Phase.PENDING
    # Log task details
    # Return {"message": f"TaskSpec {name} accepted"}

@kopf.on.field("batch", "v1", "jobs", field="status.conditions", labels={"nubi.io/task-id": kopf.PRESENT})
async def on_job_status_change(name: str, namespace: str, status: dict, labels: dict, **kwargs) -> None:
    # Read task-id and stage from labels
    # Log the status change
    # TODO stubs for phase advancement
```

## Acceptance Criteria

1. `pip install -e ".[dev]"` succeeds
2. `ruff check src/ tests/` passes with no errors
3. `ruff format --check src/ tests/` passes
4. `mypy src/nubi/` passes (targeted `type: ignore` for kopf untyped kwargs is acceptable)
5. `pytest tests/ -v` — all tests pass
6. `TaskSpecSpec.model_validate(full_example_dict)` round-trips correctly with the ARCHITECTURE.md example
7. `TaskSpecSpec` with only required fields (description, type, inputs) uses correct defaults from defaults.py
8. Spec models are frozen (assignment raises error)
9. Status models are mutable
10. `on_taskspec_created` sets `patch.status["phase"]` to `"Pending"` when given a valid spec
11. `on_taskspec_created` raises `ValidationError` when given an invalid spec
12. `on_job_status_change` reads labels and logs without error

## Out of Scope

- Namespace lifecycle, credential scoping, Job creation — those are future tasks
- Duration string parsing to timedelta — keep as `str` for now
- Actual Kubernetes cluster interaction — handlers are stubs
- Strands agent definitions — future tasks
