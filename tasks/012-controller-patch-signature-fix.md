# Task 012: Fix Controller Annotation Patch Client Signature

## Goal

Fix the controller's TaskSpec completion annotation patch so it works with the real `kubernetes_asyncio` client at runtime, and add regression coverage that would have caught the client-signature mismatch before a live e2e run.

## Problem Summary

The live e2e proved that executor work completed successfully, but the controller failed while trying to annotate the owning `TaskSpec` with job completion metadata.

Observed runtime error:

- `Got an unexpected keyword argument 'content_type' to method patch_namespaced_custom_object`

This means the current controller code is calling the Kubernetes async client with the wrong keyword argument for patch content type. As a result:

- the completion annotation is never written
- the Kopf field handler never runs
- `TaskSpec.status.phase` stays `Executing`
- the live e2e harness cannot observe task completion through CRD status

## Scope

Focus on:

- `src/nubi/controller/handlers.py`
- `tests/test_handlers.py`

Do not bundle additional e2e harness changes into this task.

## Contracts

### 1. Use the real client's supported patch signature

- `src/nubi/controller/handlers.py`

`_annotate_task_completion(...)` must patch TaskSpec metadata annotations using the actual keyword arguments supported by `kubernetes_asyncio.client.CustomObjectsApi.patch_namespaced_custom_object`.

The implementation must still send a merge-patch payload for:
- `nubi.io/job-completed`
- `nubi.io/job-name`
- `nubi.io/job-namespace`

### 2. Regression coverage matches the real client contract

- `tests/test_handlers.py`

Tests must be strict enough to catch unsupported kwargs against the real client method signature.

At minimum, add/update tests so they would fail if code passes `content_type=` instead of the supported kwarg.

### 3. Existing completion flow remains intact

- `src/nubi/controller/handlers.py`
- `tests/test_handlers.py`

The change must preserve the existing annotation-driven completion flow and not loosen prior coverage for:
- explicit TaskSpec namespace routing
- duplicate completion idempotence
- field-handler registration

## Acceptance Criteria

1. The controller no longer raises a client signature error when patching TaskSpec completion annotations.
2. `tests/test_handlers.py` would fail on the old `content_type=` call shape.
3. Existing handler tests still pass.
4. Full verification passes: `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/nubi/`, `pytest tests/ -v`.
5. After rebuilding/restarting the controller, the live e2e can progress past the previous status-persistence blocker.
