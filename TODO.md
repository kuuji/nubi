# Nubi — TODO

## Next Up
- [x] Project scaffold — pyproject.toml, src/nubi/ package structure, dev dependencies (pytest, ruff, mypy)
- [x] TaskSpec CRD Pydantic schema — `nubi.crd.schema` with full spec/status models
- [x] kopf handler skeleton — on.create for TaskSpec, on.field for Job status changes

## Backlog
- [x] Task namespace lifecycle — create/cleanup namespace with ResourceQuota, NetworkPolicy, PSS labels
- [x] Credential scoping — per-stage Secret creation with least-privilege
- [x] gVisor Job builder — create sandboxed executor Jobs with RuntimeClass, resource limits, env-var tool control
- [x] Executor agent — Strands agent with shell, file, git tools; clone branch, do work, push
- [ ] Deterministic gates — lint/test/secret-scan runners, failure feedback to executor
- [ ] Validator agent — writes test suites, commits to branch, runs them
- [ ] Reviewer agent — read-only evaluation, approve/reject with feedback
- [ ] Loop resolution — retry logic (gate→executor, validator→executor, reviewer→executor) with max_retries
- [ ] PR output — create GitHub PR from task branch on approval
- [ ] Summary call — single LLM call to produce human-readable narrative from CRD status + traces
- [ ] Planner agent — task decomposition for complex specs (opt-in)
- [ ] Notification output — Discord/Telegram notifications on completion

## Local Testing & Dev Loop
- [ ] Local integration test harness — kind/k3d cluster + CRD applied + `kopf run` against it
- [ ] Integration test suite — real K8s API calls (namespace creation, Job spawning, status updates)
- [ ] End-to-end smoke test — apply a TaskSpec, verify full pipeline runs to completion

## Infrastructure & Pipeline
- [x] Controller Dockerfile — multi-stage build for kopf controller image
- [x] Agent Dockerfile — single image with tool availability via env vars
- [x] TaskSpec CRD YAML manifest — CustomResourceDefinition for kubectl apply
- [x] Controller Deployment manifest — Deployment + ServiceAccount + RBAC + RuntimeClass
- [x] GitHub Actions CI — lint, type-check, test on PRs; image build on merge
- [ ] Helm chart — templated deployment for production

## Ideas
- Langfuse integration for tracing and cost tracking
- Helm chart for production deployment
- Cost budget enforcement based on Langfuse data
- Parallel sub-task execution for independent planner outputs
- Human-in-the-loop approval gates (pause reconciliation)
- GitOps integration examples (ArgoCD + TaskSpec)
- OpenClaw skill for natural language → TaskSpec
