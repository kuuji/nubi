# Task 009: Fail Fast in Live E2E on Fatal Pod States

## Goal

Make the live e2e harness fail quickly with a useful error when the executor Job is clearly stuck in a fatal pod state, instead of continuing to wait until the overall timeout or manual interruption.

## Problem Summary

During a live `scripts/e2e.sh test` run, the executor Job entered a failed pod state caused by image pull failure (`ErrImagePull` / `ImagePullBackOff`), but the harness remained in a waiting loop long enough to require manual interruption.

The e2e script already has overall timeouts, but those are too coarse when Kubernetes is already exposing an unrecoverable failure reason.

## Scope

Keep this task focused on the live e2e harness in `scripts/e2e.sh` and its tests. Do not change controller/runtime behavior in this task.

## Contracts

### 1. Detect fatal pod states while waiting

- `scripts/e2e.sh`

While waiting for the executor Job to complete, the harness must inspect the executor pod state and fail fast when it sees clearly fatal reasons such as:
- `ErrImagePull`
- `ImagePullBackOff`
- `CreateContainerConfigError`
- `CreateContainerError`
- `InvalidImageName`

If a terminated container exposes a non-zero exit and a reason before the Job reaches a clean terminal condition, that should also be surfaced as a fatal state.

### 2. Useful failure message

- `scripts/e2e.sh`

When failing fast, the script must report the fatal pod reason in the error output so the user can immediately see why the run is not progressing.

### 3. Artifact capture still happens

- `scripts/e2e.sh`

The existing failure path must still capture/report artifacts when a fail-fast condition triggers.

### 4. Regression coverage

- `tests/test_e2e_script.py`

Add or update tests to cover:
- fatal waiting reason causes `scripts/e2e.sh test` to exit non-zero before waiting for the full timeout
- the error output includes the fatal reason
- artifact reporting still appears on this failure path

## Acceptance Criteria

1. `scripts/e2e.sh test` exits non-zero when the executor pod enters a fatal waiting state like `ImagePullBackOff`.
2. The script error output includes the detected fatal reason.
3. The script still reports the artifact directory on this failure path.
4. Existing live e2e behavior remains intact for successful runs.
5. Added/updated tests cover the new fail-fast guardrail.
6. Full verification passes: `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/nubi/`, `pytest tests/ -v`.
