# Nubi — History

<!-- Updated by the Planner after each approved task. Format: -->
<!-- ## YYYY-MM-DD — Short description -->
<!-- - What changed and why -->
<!-- - Files affected -->
<!-- - Any decisions made during implementation -->

## 2026-04-05 — Status persistence fix + robust live e2e harness

- **Fixed status persistence bug**: Controller annotation patch was using wrong kwarg (`content_type` instead of `_content_type`) causing 400 errors
- **Rewrote e2e.sh from scratch**:
  - Unique per-run TaskSpec identity with deterministic naming
  - Assertion-driven verification (TaskSpec phase, remote branch, file content)
  - Artifact capture per-run with cleanup reporting
  - Fail-fast detection for fatal pod states (ErrImagePull, ImagePullBackOff, etc.)
  - Post-job hang detection when TaskSpec phase remains stuck
  - Fixed Job terminal detection to handle non-standard condition ordering (SuccessCriteriaMet, FailureTarget before Complete/Failed)
  - Controller rollout restart on `e2e.sh up` to pick up new images
  - Scoped cleanup: only removes resources created by current run
- **Added comprehensive e2e tests**: 16 contract tests covering success/failure paths, delayed terminal conditions, fatal pod states
- **Live e2e passed end-to-end**: TaskSpec created → Job completed → GitHub branch pushed → phase updated to Done → cleanup successful
- **Verification**: 300 tests passing, ruff/mypy clean
- Files: scripts/e2e.sh, tests/test_e2e_script.py, src/nubi/controller/handlers.py, README.md, 7 task specs in tasks/

## 2026-04-05 — E2E test run + infrastructure fixes

- Ran first real e2e test: created TaskSpec → executor job in k3d → agent ran → committed code to GitHub
- Found and fixed multiple issues:
  - `NUBI_LLM_PROVIDER=anthropic` hardcoded but agent uses OpenRouter — fixed via deployment.yaml env vars
  - `imagePullPolicy` defaults to Always for executor jobs — fixed via `NUBI_AGENT_IMAGE_PULL_POLICY=IfNotPresent`
  - k3d needs `IfNotPresent` to use locally imported images instead of pulling from GHCR
  - Controller Deployment manifest missing env vars that executor jobs need
- Executor successfully called Kimi K2 via OpenRouter, ran agent loop, committed to `nubi/bootstrap-go-cli-playground` branch
- Gate `diff_size` passed successfully
- **BUG**: TaskSpec status doesn't persist — PATCH returns 200 but phase stays `Executing`
- Created `scripts/e2e.sh` for repeatable e2e testing workflow
- Updated Makefile `dev` target with proper env vars for k3d
- Files: Makefile, manifests/deployment.yaml, scripts/e2e.sh, backlog/status-persistence-bug.md

## 2026-04-04 — Local dev harness + end-to-end fixes

- Created local dev infrastructure: Makefile, k3d dev cluster scripts, smoke test, sample TaskSpec
- Makefile targets: cluster-up/down, build, dev, test, lint, smoke, clean
- Added kopf decorators to handlers (`@kopf.on.create`, `@kopf.on.event`) — handlers were plain functions, `kopf run` couldn't discover them
- Made sandbox configurable via env vars: `NUBI_RUNTIME_CLASS` (empty = omit), `NUBI_AGENT_IMAGE`, `NUBI_AGENT_IMAGE_PULL_POLICY`
- Removed cross-namespace ownerRef from executor Job (K8s GC'd jobs immediately since TaskSpec is in a different namespace than the job)
- Fixed agent Dockerfile: now installs nubi package + openai dep, correct entrypoint (`python -m nubi.entrypoint`)
- Disabled read-only rootfs on agent pods (git/python need writable home and temp dirs; isolation comes from gVisor + network policy + ephemeral namespace)
- Set `HOME=/workspace` in agent pod env (uid 65534 has no home dir)
- Fixed git clone for sandboxed pods: `safe.directory=*` via `-c` flag, sanitized token from error output
- Added `NUBI_LLM_BASE_URL` support for OpenAI-compatible endpoints (OpenRouter, ollama, etc.)
- LLM config passthrough: controller forwards `NUBI_LLM_PROVIDER`, `NUBI_MODEL_ID`, `NUBI_LLM_BASE_URL` to agent pods
- Agent now creates `nubi/{task_id}` branch off base branch — never pushes to main directly
- `.env` / `.env.example` for local credentials (gitignored)
- Verified end-to-end: k3d cluster → controller → TaskSpec → namespace + credentials → job → agent pod → Kimi K2 via OpenRouter → commit + push to task branch
- Files: Makefile, scripts/dev-cluster.sh, scripts/smoke-test.sh, examples/sample-taskspec.yaml, .env.example, .gitignore, handlers.py, sandbox.py, executor.py, entrypoint.py, git.py, agent Dockerfile, pyproject.toml, test updates
- Decisions: k3d over kind (lighter on Linux), env var overrides over config files (simpler), drop read-only rootfs (too many things need writable dirs)

## 2026-04-03 — Executor agent full loop

- Implemented executor agent with Strands SDK — tools, entrypoint, controller result reader, handler completion
- Tools: `run_shell` (subprocess, output truncation, timeout), `git_clone/diff/log/commit/push/status`, `file_read/write/list` (path traversal protection)
- Tool registry with `get_tools()` filtering by NUBI_TOOLS env var groups
- Agent factory with provider-agnostic model creation (anthropic/bedrock/openai via NUBI_LLM_PROVIDER)
- Container entrypoint: env parsing → clone → agent run → result write → commit → push
- Git-native result reporting: agent commits `.nubi/result.json` to branch, controller reads via GitHub REST API
- Handler `on_job_status_change` implemented: reads Job conditions, fetches result, updates CRD status (phase, executor stage, workspace SHA)
- New module: `nubi.controller.results` (aiohttp GitHub API client)
- New exception: `ResultError`
- Added NUBI_LLM_PROVIDER env var to Job spec in sandbox builder
- Added `strands-agents` and `aiohttp` dependencies
- 63 new tests, total 197 passing. mypy clean, ruff clean.
- Decisions: result via git (not logs/termination message), provider-agnostic (not Anthropic-only), callback_handler=None to suppress stdout

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
