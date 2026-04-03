# Nubi — TODO

## Next Up
- [ ] Project scaffold — pyproject.toml, src/nubi/ package structure, dev dependencies (pytest, ruff, mypy)
- [ ] TaskSpec CRD Pydantic schema — `nubi.crd.schema` with full spec/status models
- [ ] kopf handler skeleton — on.create for TaskSpec, on.field for Job status changes

## Backlog
- [ ] Task namespace lifecycle — create/cleanup namespace with ResourceQuota, NetworkPolicy, PSS labels
- [ ] Credential scoping — per-stage Secret creation with least-privilege
- [ ] gVisor Job builder — create sandboxed executor Jobs with RuntimeClass, resource limits, env-var tool control
- [ ] Executor agent — Strands agent with shell, file, git tools; clone branch, do work, push
- [ ] Deterministic gates — lint/test/secret-scan runners, failure feedback to executor
- [ ] Validator agent — writes test suites, commits to branch, runs them
- [ ] Reviewer agent — read-only evaluation, approve/reject with feedback
- [ ] Loop resolution — retry logic (gate→executor, validator→executor, reviewer→executor) with max_retries
- [ ] PR output — create GitHub PR from task branch on approval
- [ ] Summary call — single LLM call to produce human-readable narrative from CRD status + traces
- [ ] TaskSpec CRD YAML manifest — CustomResourceDefinition for kubectl apply
- [ ] Controller Deployment manifest — Deployment + ServiceAccount + RBAC
- [ ] Planner agent — task decomposition for complex specs (opt-in)
- [ ] Notification output — Discord/Telegram notifications on completion

## Ideas
- Langfuse integration for tracing and cost tracking
- Helm chart for production deployment
- Cost budget enforcement based on Langfuse data
- Parallel sub-task execution for independent planner outputs
- Human-in-the-loop approval gates (pause reconciliation)
- GitOps integration examples (ArgoCD + TaskSpec)
- OpenClaw skill for natural language → TaskSpec
