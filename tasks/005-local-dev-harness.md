# Task: Local dev harness (k3d + Makefile)

## Type
`scaffold`

## Goal
Create the local development infrastructure so the controller can be run against a real k3d cluster, and make the sandbox builder flexible enough to work without gVisor.

## Contracts
No new public APIs. Changes to existing:
- `build_executor_job()` now reads `NUBI_RUNTIME_CLASS`, `NUBI_AGENT_IMAGE`, `NUBI_AGENT_IMAGE_PULL_POLICY` from env vars with existing defaults.
- `on_taskspec_created` and `on_job_status_change` get kopf decorators for handler registration.

## Acceptance Criteria
- [ ] `Makefile` with targets: cluster-up, cluster-down, build, dev, test, test-integration, lint, smoke, clean
- [ ] `scripts/dev-cluster.sh` creates/destroys k3d cluster with CRD, RBAC, dummy credentials
- [ ] `scripts/smoke-test.sh` applies sample TaskSpec and verifies controller processing
- [ ] `examples/sample-taskspec.yaml` is a valid TaskSpec
- [ ] `handlers.py` has kopf decorators — `kopf run` registers both handlers
- [ ] `sandbox.py` respects `NUBI_RUNTIME_CLASS` (empty = omit), `NUBI_AGENT_IMAGE`, `NUBI_AGENT_IMAGE_PULL_POLICY` env vars
- [ ] Existing tests pass, new tests cover env var overrides
- [ ] `ruff check`, `ruff format --check`, `mypy` all clean

## Context
- Controller handlers are plain async functions without `@kopf.on.*` decorators
- `sandbox.py` hardcodes gVisor runtime class — jobs can't schedule in k3d
- Agent image uses `:latest` which defaults to `Always` pull policy — fails with `k3d image import`
- No Makefile or dev scripts exist

## Out of Scope
- Integration test suite (placeholder target only)
- Real credential setup (dummy values for now)
- In-cluster controller deployment testing
