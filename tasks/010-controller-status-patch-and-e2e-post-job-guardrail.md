# Task 010: Fix Controller Status Annotation Patching and Post-Job E2E Hang

## Goal

Fix the controller completion path so executor Job completion can successfully annotate the owning `TaskSpec`, allowing status to move out of `Executing`, and harden the live e2e harness so it does not keep waiting for the full timeout once the executor Job has already finished but `TaskSpec.status` is still stuck.

## Problem Summary

During a live `scripts/e2e.sh test` run:

- the executor Job completed successfully
- the remote GitHub branch and file were created successfully
- but the controller failed to annotate the `TaskSpec` with job completion due to a bad patch request, so `TaskSpec.status.phase` stayed `Executing`
- the e2e harness kept waiting on a terminal TaskSpec phase even though the executor work had already finished

Observed controller failure:

- `patch_namespaced_custom_object(...)` returned HTTP 400
- error: `json: cannot unmarshal object into Go value of type []handlers.jsonPatchOp`

This indicates the annotation patch is being sent with the wrong patch format/content type.

## Scope

Focus on:

- `src/nubi/controller/handlers.py`
- `scripts/e2e.sh`
- tests covering controller annotation patching and live e2e wait behavior

Do not bundle unrelated simplification work into this task.

## Contracts

### 1. Controller annotation patch uses the correct patch format

- `src/nubi/controller/handlers.py`

`_annotate_task_completion(...)` must patch TaskSpec metadata annotations using a patch format the Kubernetes API accepts for this object payload.

The implementation must ensure the controller can successfully write:
- `nubi.io/job-completed`
- `nubi.io/job-name`
- `nubi.io/job-namespace`

### 2. Status persistence path works in real controller flow

- `src/nubi/controller/handlers.py`

After successful annotation, the existing Kopf field handler must be able to process the annotation and move `TaskSpec.status.phase` to the correct terminal/retry state as already intended.

### 3. Live e2e does not wait the full timeout after job completion

- `scripts/e2e.sh`

Once the executor Job reaches a terminal state, the script must not continue waiting indefinitely for the full global timeout if TaskSpec status is not advancing.

Add a narrower post-job guardrail such as:
- a shorter phase-transition timeout after Job completion, and/or
- a targeted failure mode when the remote branch/file exists but TaskSpec phase is still non-terminal after a short grace period

The resulting failure should clearly explain that executor work finished but controller status did not persist.

### 4. Useful diagnostics on this failure path

- `scripts/e2e.sh`

When the post-job guardrail triggers, the script must still capture/report artifacts and include enough context in the error output to show that:
- the executor Job completed
- the TaskSpec phase failed to advance as expected

### 5. Regression coverage

- `tests/test_handlers.py`
- `tests/test_e2e_script.py`

Add or update tests to cover at least:
- controller completion annotation patch uses the correct patch content type/shape
- e2e fails fast after job completion when TaskSpec phase remains stuck
- the stuck-status error message is explicit
- artifact reporting still appears on that path

## Acceptance Criteria

1. Controller completion annotation patch no longer returns a 400 due to patch format mismatch.
2. Controller tests verify the annotation patch call uses the correct patch format/content type.
3. `scripts/e2e.sh test` no longer waits the full timeout after the Job is already complete but TaskSpec phase remains non-terminal.
4. The e2e failure message explicitly indicates that executor work completed but TaskSpec status did not advance.
5. Artifact reporting still occurs on this failure path.
6. Added/updated tests cover both the controller fix and the e2e guardrail.
7. Full verification passes: `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/nubi/`, `pytest tests/ -v`.
