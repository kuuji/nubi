# Nubi — History

<!-- Updated by the Planner after each approved task. Format: -->
<!-- ## YYYY-MM-DD — Short description -->
<!-- - What changed and why -->
<!-- - Files affected -->
<!-- - Any decisions made during implementation -->

## 2026-04-03 — Task namespace lifecycle

- Implemented `nubi.controller.namespace` — creates isolated namespace per task with ResourceQuota, NetworkPolicy, PSS labels
- Namespace naming: `nubi-{task-name}`, truncated to 63 chars
- ResourceQuota mirrors spec constraints (cpu, memory) + pods cap of 4
- NetworkPolicy: deny-all ingress, always allow DNS egress (port 53), allow web egress (80/443) only when `network_access` non-empty
- All K8s API calls idempotent (409 = no-op), non-recoverable errors raise `NamespaceError`
- Handler integration: `on_taskspec_created` now creates namespace and records it in `status.workspace.namespace`
- Schema additions: `WorkspaceStatus.namespace`, `TaskSpecStatus.phase_changed_at`
- Added `kubernetes_asyncio` dependency and mypy overrides
- 27 new tests in `test_namespace.py`, 8 handler tests updated
- Decisions: NetworkPolicy hostname matching deferred (v0.1 uses port-based), cleanup timer deferred to pipeline phase wiring

## 2026-04-03 — Project foundation (scaffold + CRD schema + handler skeleton)

- Created pyproject.toml with kopf, pydantic v2, kubernetes-asyncio deps and dev tooling (pytest, ruff, mypy)
- Implemented TaskSpec CRD Pydantic schema in `src/nubi/crd/schema.py` — 6 StrEnums, 11 frozen spec models, 7 mutable status models with camelCase alias support
- Default constants in `src/nubi/crd/defaults.py` referenced by schema Field() declarations
- kopf handler skeleton in `src/nubi/controller/handlers.py` — on_taskspec_created validates spec and sets phase, on_job_status_change reads labels and logs
- Exception hierarchy in `src/nubi/exceptions.py`
- 45 tests across 3 test files, all passing with ruff + mypy clean
- Decisions: kubernetes-asyncio over kr8s (kopf compatibility), duration strings kept as str (no timedelta parsing in v0.1)
