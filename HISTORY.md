# Nubi — History

<!-- Updated by the Planner after each approved task. Format: -->
<!-- ## YYYY-MM-DD — Short description -->
<!-- - What changed and why -->
<!-- - Files affected -->
<!-- - Any decisions made during implementation -->

## 2026-04-03 — Infrastructure & pipeline scaffold

- Controller Dockerfile — multi-stage build (python:3.12-slim), non-root, `kopf run` entrypoint
- Agent Dockerfile — single image with git/curl/build tools, strands-agents SDK, env-var tool control via NUBI_TOOLS
- TaskSpec CRD YAML manifest — full openAPIV3Schema matching Pydantic models, printer columns (name, type, phase, age), status subresource
- Controller Deployment manifest — nubi-system namespace, ServiceAccount, ClusterRole with scoped RBAC, security-hardened pod spec (non-root, read-only rootfs, drop ALL caps)
- gVisor RuntimeClass manifest (handler: runsc)
- GitHub Actions CI — ruff + mypy + pytest on PRs, image build & push to GHCR on merge to main
- Files: .github/workflows/ci.yml, images/controller/Dockerfile, images/agent/Dockerfile, manifests/crd.yaml, manifests/deployment.yaml

## 2026-04-03 — Sandbox job spawning (credentials + gVisor Job builder)

- Implemented `nubi.controller.credentials` — per-stage Secret creation with least-privilege scoping
- Stage credential mapping: executor/validator get both github-token + llm-api-key, reviewer gets llm-api-key only, gate gets nothing
- Reads master Secret from `nubi-system/nubi-credentials`, creates scoped Secret in task namespace
- Implemented `nubi.controller.sandbox` — gVisor Job builder with restricted PSS security context
- Job features: gVisor RuntimeClass, run-as-nobody (65534), read-only root fs, drop ALL caps, RuntimeDefault seccomp, emptyDir workspace, activeDeadlineSeconds from timeout, owner references for GC
- Env vars: Secret-backed GITHUB_TOKEN/LLM_API_KEY, plain NUBI_TASK_ID/REPO/BRANCH/DESCRIPTION/TOOLS
- Handler wired end-to-end: on_taskspec_created now creates namespace → scopes credentials → spawns executor Job → sets phase to Executing
- New exceptions: `CredentialError`, `SandboxError`
- New constants: MASTER_SECRET_NAME, LABEL_STAGE, DEFAULT_AGENT_IMAGE, credential key names
- 52 new tests (16 credentials, 30 sandbox, 6 handler updates), total 134 passing
- Decisions: parse_duration supports seconds-only for v0.1, env-var tool control via NUBI_TOOLS comma-separated

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
