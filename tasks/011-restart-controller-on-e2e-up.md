# Task 011: Restart Controller on `e2e.sh up` When Reusing `latest`

## Goal

Make `scripts/e2e.sh up` reliably activate newly built controller code even when reusing the same image tag (`ghcr.io/kuuji/nubi-controller:latest`).

## Problem Summary

After rebuilding and importing images with `./scripts/e2e.sh up`, the live e2e still used old controller behavior because the existing controller pod was not restarted. Applying the same deployment manifest with the same image tag does not necessarily rotate pods, so the cluster can keep running stale code.

This caused the live e2e to wait on behavior that had already been fixed in the local source tree because the controller deployment never picked up the new image.

## Scope

Focus on `scripts/e2e.sh` and shell-script tests only. Do not change controller runtime code in this task.

## Contracts

### 1. `up` forces controller rollout after image import

- `scripts/e2e.sh`

After building/importing images and applying the deployment manifest, `cmd_up` must ensure the controller deployment actually rolls to the newly imported image.

Acceptable approaches include:
- `kubectl rollout restart deployment/nubi-controller ...`
- or another explicit pod-template-changing rollout trigger

Simply re-applying the deployment is not sufficient.

### 2. `up` waits for the new rollout to become ready

- `scripts/e2e.sh`

After forcing the rollout, the script must wait for the restarted deployment/pod to become ready before reporting success.

### 3. Regression coverage

- `tests/test_e2e_script.py`

Add or update tests to cover that `scripts/e2e.sh up`:
- triggers a controller rollout restart
- waits for readiness after the restart

Tests can use mocked command execution.

## Acceptance Criteria

1. `./scripts/e2e.sh up` forces a controller rollout after image import.
2. `./scripts/e2e.sh up` waits for the restarted controller to become ready.
3. Added/updated tests cover the rollout restart behavior.
4. Full verification passes: `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/nubi/`, `pytest tests/ -v`.
