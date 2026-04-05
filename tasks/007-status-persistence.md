# Task 007: Fix TaskSpec Status Persistence

## Goal

Fix the controller flow so executor job completion reliably updates `TaskSpec.status` out of `Executing` and into the correct terminal or retry state. The current end-to-end run reports a successful PATCH but the CRD status does not persist as expected.

## Problem Summary

- The controller creates executor Jobs in a task namespace, while the `TaskSpec` itself may live in another namespace such as `nubi-system`.
- Job completion handling must be able to find the originating `TaskSpec` deterministically.
- When an executor Job completes, the controller must transition status through the normal Kopf patch path so the status subresource is actually updated.
- Existing behavior must still support gate failures, retry state, and failure reporting.

## Design Constraints

- Keep the fix focused on status persistence and completion routing; do not bundle broader simplification work into this task.
- Do not use direct ad-hoc HTTP patching for status updates if Kopf patch/status handling can own the update path.
- Preserve current executor result and gates result reading behavior.
- Avoid namespace guessing; use explicit metadata on Jobs to identify the owning `TaskSpec` namespace.

## Contracts

### 1. Job metadata points back to the owning TaskSpec

- `src/nubi/controller/sandbox.py`
- `src/nubi/crd/defaults.py`

Add a dedicated Job label for the TaskSpec namespace and ensure executor Jobs are created with it.

### 2. Job completion routing is deterministic

- `src/nubi/controller/handlers.py`

`on_job_status_change` must:
- read the TaskSpec namespace from Job labels
- verify the TaskSpec exists in that namespace
- record job completion in a way that triggers the TaskSpec reconciliation path without guessing namespaces

### 3. TaskSpec field handler owns status transitions

- `src/nubi/controller/handlers.py`

`on_job_completion_annotation` must update Kopf patch status for these cases:
- job failure -> `Failed`
- executor success with passing gates -> `Done`
- gate failure below retry limit -> back to `Executing`
- gate failure at retry limit -> `Escalated`

It must also mark the completion annotation as processed so the handler is idempotent.

### 4. Regression coverage

- `tests/test_handlers.py`
- `tests/test_sandbox.py`

Add or update tests to cover:
- executor Jobs carrying the TaskSpec namespace label
- job completion annotation flow for success and failure
- `on_job_status_change` using explicit TaskSpec namespace routing instead of fallback guessing

## Acceptance Criteria

1. Executor Job metadata includes the TaskSpec namespace label.
2. `on_job_status_change` uses the explicit TaskSpec namespace from Job labels.
3. Successful executor completion sets `TaskSpec.status.phase` to `Done` through the Kopf patch path.
4. Failed executor completion sets `TaskSpec.status.phase` to `Failed` through the Kopf patch path.
5. Gate failure updates remain correct for retry and escalation states.
6. The completion annotation is marked `processed` after handling.
7. `pytest tests/test_handlers.py tests/test_sandbox.py -v` passes.
8. Full repo verification passes: `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/nubi/`, `pytest tests/ -v`.
