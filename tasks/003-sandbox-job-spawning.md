# Task 003: Sandbox Job Spawning (Credentials + gVisor Job Builder)

## Goal

Enable the controller to spawn sandboxed executor Jobs. After this task, applying a TaskSpec creates an isolated namespace, scopes credentials per-stage, and launches a gVisor-sandboxed executor Job — completing the v0.1 pipeline path.

## Contracts

### 1. Credential Scoping — `src/nubi/controller/credentials.py`

#### Constants (add to `src/nubi/crd/defaults.py`)

```python
MASTER_SECRET_NAME = "nubi-credentials"
MASTER_SECRET_NAMESPACE = "nubi-system"
LABEL_STAGE = "nubi.io/stage"
CREDENTIAL_GITHUB_TOKEN = "github-token"
CREDENTIAL_LLM_API_KEY = "llm-api-key"
```

#### Stage credential mapping

| Stage | `github-token` | `llm-api-key` |
|---|---|---|
| executor | yes | yes |
| validator | yes | yes |
| reviewer | no | yes |
| gate | no | no |

Define this as a module-level dict `STAGE_CREDENTIALS: dict[str, list[str]]` mapping stage name to the list of credential keys it receives.

#### `ensure_stage_secret(ns_name: str, task_name: str, stage: str) -> str`

- Reads the master Secret `nubi-credentials` from `nubi-system` via `CoreV1Api.read_namespaced_secret`
- Filters to only the keys defined in `STAGE_CREDENTIALS[stage]`
- Creates a Secret named `nubi-{stage}-credentials` in `ns_name` with:
  - Labels: `nubi.io/task-id: {task_name}`, `nubi.io/stage: {stage}`, `app.kubernetes.io/managed-by: nubi`
  - Only the scoped credential data
- Returns the Secret name
- Idempotent: 409 = log + return name
- Raises `CredentialError` on API failures or if stage is `"gate"` (no credentials needed)

#### Exception

Add `CredentialError(NubiError)` to `src/nubi/exceptions.py`.

---

### 2. gVisor Job Builder — `src/nubi/controller/sandbox.py`

#### Constants (add to `src/nubi/crd/defaults.py`)

```python
DEFAULT_AGENT_IMAGE = "ghcr.io/kuuji/nubi-agent:latest"
```

#### `parse_duration(duration: str) -> int`

- Converts `"300s"` to `300`
- Only supports seconds suffix (`s`) for v0.1
- Raises `ValueError` on invalid format

#### `build_executor_job(task_name: str, ns_name: str, spec: TaskSpecSpec, secret_name: str, owner_uid: str) -> V1Job`

Constructs a `V1Job` with:

- **metadata.name**: `nubi-executor-{task_name}` (truncated to 63 chars)
- **metadata.namespace**: `ns_name`
- **metadata.labels**: `nubi.io/task-id: {task_name}`, `nubi.io/stage: executor`, `app.kubernetes.io/managed-by: nubi`
- **metadata.owner_references**: single ref to TaskSpec CRD (`apiVersion: nubi.io/v1`, `kind: TaskSpec`, `name: task_name`, `uid: owner_uid`)
- **spec.backoff_limit**: `0`
- **spec.active_deadline_seconds**: from `parse_duration(spec.constraints.timeout)`
- **spec.template.spec.runtime_class_name**: `gvisor` (from `DEFAULT_GVISOR_RUNTIME_CLASS`)
- **spec.template.spec.restart_policy**: `"Never"`
- **Container** (single, named `executor`):
  - **image**: `DEFAULT_AGENT_IMAGE`
  - **working_dir**: `/workspace`
  - **resources**: requests and limits = `{cpu: spec.constraints.resources.cpu, memory: spec.constraints.resources.memory}`
  - **security_context**:
    - `run_as_non_root: True`
    - `run_as_user: 65534`
    - `allow_privilege_escalation: False`
    - `read_only_root_filesystem: True`
    - `capabilities: V1Capabilities(drop=["ALL"])`
    - `seccomp_profile: V1SeccompProfile(type="RuntimeDefault")`
  - **env from Secret** (via `V1EnvVar` with `V1EnvVarSource` + `V1SecretKeySelector`):
    - `GITHUB_TOKEN` from `secret_name` key `github-token`
    - `LLM_API_KEY` from `secret_name` key `llm-api-key`
  - **env plain**:
    - `NUBI_TASK_ID` = `task_name`
    - `NUBI_REPO` = `spec.inputs.repo`
    - `NUBI_BRANCH` = `spec.inputs.branch`
    - `NUBI_DESCRIPTION` = `spec.description`
    - `NUBI_TOOLS` = `",".join(spec.constraints.tools)` (empty string if no tools)
  - **volume_mounts**: `/workspace` from `workspace` volume
- **Volumes**: one `emptyDir` named `workspace`

#### `create_executor_job(task_name: str, ns_name: str, spec: TaskSpecSpec, secret_name: str, owner_uid: str) -> str`

- Calls `build_executor_job` to construct the Job
- Creates it via `BatchV1Api.create_namespaced_job`
- Returns the Job name
- Idempotent: 409 = log + return name
- Raises `SandboxError` on API failures

#### Exception

Add `SandboxError(NubiError)` to `src/nubi/exceptions.py`.

---

### 3. Handler Wiring — `src/nubi/controller/handlers.py`

Update `on_taskspec_created`:

```python
# After namespace creation:
secret_name = await ensure_stage_secret(ns_name, name, "executor")
job_name = await create_executor_job(name, ns_name, task_spec, secret_name, kwargs["uid"])
patch.status["phase"] = Phase.EXECUTING
patch.status["stages"] = {"executor": {"status": "running", "attempts": 1}}
```

- Import `ensure_stage_secret` from `nubi.controller.credentials`
- Import `create_executor_job` from `nubi.controller.sandbox`
- Import `CredentialError` and `SandboxError` from `nubi.exceptions`
- Catch `CredentialError` and `SandboxError` — set phase to `Phase.FAILED` and re-raise
- Remove the TODO comments

---

## Acceptance Criteria

1. `ensure_stage_secret` creates a Secret with only the credentials the stage needs
2. `ensure_stage_secret` raises `CredentialError` for stage `"gate"` (no credentials)
3. `ensure_stage_secret` is idempotent (409 = no-op)
4. `build_executor_job` produces a Job with gVisor runtime, restricted PSS security context, resource limits, owner reference, correct labels, env vars from Secret, and tool control env var
5. `create_executor_job` is idempotent (409 = no-op)
6. `parse_duration("300s")` returns `300`; invalid input raises `ValueError`
7. `on_taskspec_created` calls credential scoping and job creation, sets phase to `Executing`
8. All verification passes: `ruff check`, `ruff format --check`, `mypy`, `pytest`
