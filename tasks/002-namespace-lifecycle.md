# Task 002: Task Namespace Lifecycle

## Goal

Implement namespace creation and cleanup for TaskSpec pipelines. When a TaskSpec is created, the controller creates an isolated namespace with ResourceQuota, NetworkPolicy, and PSS labels. When the task completes or fails, the namespace is cleaned up based on TTL.

## Contracts

### `src/nubi/crd/defaults.py` — new constants

```python
DEFAULT_NAMESPACE_PREFIX = "nubi-"
DEFAULT_GVISOR_RUNTIME_CLASS = "gvisor"
DEFAULT_CLEANUP_TTL_SUCCESS = 3600
DEFAULT_CLEANUP_TTL_FAILURE = 0
LABEL_TASK_ID = "nubi.io/task-id"
LABEL_TASK_TYPE = "nubi.io/task-type"
LABEL_MANAGED_BY = "app.kubernetes.io/managed-by"
```

### `src/nubi/exceptions.py` — new exception

```python
class NamespaceError(NubiError):
    """Raised when namespace lifecycle operations fail."""
```

### `src/nubi/crd/schema.py` — schema changes

- `WorkspaceStatus`: add `namespace: str = Field(default="")`
- `TaskSpecStatus`: add `phase_changed_at: str = Field(default="", alias="phaseChangedAt")`

### `src/nubi/controller/namespace.py` — new module

**`task_namespace_name(task_name: str) -> str`**
- Returns `f"nubi-{task_name}"`, truncated to 63 chars.

**`async def ensure_task_namespace(*, api_client, task_name, task_type, constraints) -> str`**
- Orchestrates creation of namespace, ResourceQuota, NetworkPolicy.
- Returns the namespace name.
- All sub-calls are idempotent (handle 409 Conflict).

**`async def _create_namespace(*, core_api, ns_name, task_name, task_type) -> None`**
- Creates Namespace with labels:
  - `nubi.io/task-id: {task_name}`
  - `nubi.io/task-type: {task_type}`
  - `app.kubernetes.io/managed-by: nubi`
  - `pod-security.kubernetes.io/enforce: restricted`

**`async def _create_resource_quota(*, core_api, ns_name, constraints) -> None`**
- Creates ResourceQuota `nubi-quota` with hard limits from `constraints.resources` (cpu, memory) and `pods: "4"`.

**`async def _create_network_policy(*, networking_api, ns_name, network_access) -> None`**
- Creates NetworkPolicy `nubi-default`:
  - Deny all ingress.
  - Always allow DNS egress (port 53 UDP+TCP to kube-system).
  - If `network_access` is non-empty, allow egress on ports 80/443.
  - If `network_access` is empty, only DNS.

**`async def delete_task_namespace(*, api_client, ns_name) -> None`**
- Deletes namespace. Handles 404 gracefully.
- Non-404 ApiException wrapped in NamespaceError.

### `src/nubi/controller/handlers.py` — integration

- `on_taskspec_created`: call `ensure_task_namespace()`, store namespace in `patch.status["workspace"]["namespace"]`, set `patch.status["phaseChangedAt"]`. Catch `NamespaceError` → phase FAILED, raise `kopf.PermanentError`.
- Add `on_taskspec_cleanup` kopf timer (interval=60s): check if phase is Done/Failed, if TTL elapsed, call `delete_task_namespace`.

## Acceptance Criteria

1. `task_namespace_name("my-task")` returns `"nubi-my-task"`, truncates to 63 chars.
2. `ensure_task_namespace` creates Namespace, ResourceQuota, NetworkPolicy via kubernetes-asyncio.
3. Namespace has correct PSS label and nubi labels.
4. ResourceQuota hard limits match spec constraints.
5. NetworkPolicy: DNS always allowed, web egress only when `network_access` non-empty, all ingress denied.
6. All creation functions handle 409 idempotently.
7. `delete_task_namespace` handles 404 gracefully.
8. Non-409/404 ApiException wrapped in `NamespaceError`.
9. Handler integration: namespace name stored in status, phase FAILED on error.
10. Cleanup timer deletes namespace after TTL for Done tasks.
11. All checks pass: ruff, mypy, pytest.

## Testing Notes

- Mock kubernetes-asyncio at API class level (`CoreV1Api`, `NetworkingV1Api`) using `unittest.mock.AsyncMock`.
- Patch `nubi.controller.namespace.CoreV1Api` and `nubi.controller.namespace.NetworkingV1Api` constructors.
- Use `kubernetes_asyncio.client.ApiException` for error simulation.
- Existing handler tests need `ensure_task_namespace` mocked out to avoid K8s calls.
