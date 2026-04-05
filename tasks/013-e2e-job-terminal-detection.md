# Task 013: Fix Live E2E Job Terminal Detection

## Goal

Fix `scripts/e2e.sh` so the live e2e recognizes terminal Kubernetes Job states reliably instead of depending on the first Job condition entry.

## Problem Summary

The live e2e can hang even after the executor Job has already finished because the script currently reads only:

- `.status.conditions[0].type`

and expects it to equal `Complete` or `Failed`.

In real Kubernetes responses, terminal Jobs may expose other condition types first, such as:

- successful job: `SuccessCriteriaMet`, then `Complete`
- failed job: `FailureTarget`, then `Failed`

This makes the current wait loop unreliable and can cause the script to keep polling forever even though the Job is already terminal.

## Scope

Focus on:

- `scripts/e2e.sh`
- `tests/test_e2e_script.py`

Do not bundle other e2e or controller changes into this task.

## Contracts

### 1. Terminal Job detection must not depend on condition ordering

- `scripts/e2e.sh`

The wait logic for executor Job completion must use a stable signal for terminal state.

Acceptable signals include:
- `status.succeeded > 0` for success
- `status.failed > 0` for failure
- or scanning all condition types rather than only index `0`

The implementation must correctly detect terminal success and terminal failure even when `Complete`/`Failed` are not the first condition entries.

### 2. Existing fail-fast behavior remains intact

- `scripts/e2e.sh`

Fatal pod-state detection and timeout behavior must continue to work.

### 3. Regression coverage

- `tests/test_e2e_script.py`

Add or update tests to cover at least:
- successful Job where `conditions[0].type` is not `Complete` but the Job is terminal
- failed Job where `conditions[0].type` is not `Failed` but the Job is terminal
- existing success/failure behavior still works through the updated logic

## Acceptance Criteria

1. `scripts/e2e.sh test` no longer hangs when a completed Job reports `SuccessCriteriaMet` before `Complete`.
2. `scripts/e2e.sh test` no longer hangs when a failed Job reports `FailureTarget` before `Failed`.
3. Existing fail-fast pod-state handling still works.
4. Added/updated tests cover the new terminal detection behavior.
5. Full verification passes: `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/nubi/`, `pytest tests/ -v`.
