# Nubi — TODO

## Bugs
- [x] TaskSpec status doesn't persist — PATCH returns 200 but phase stays "Executing" (Fixed: controller annotation patch now uses correct Kubernetes client signature)
- [x] Live e2e test hangs indefinitely — fixed Job terminal detection to handle non-standard condition ordering (SuccessCriteriaMet, FailureTarget)
- [x] Remove unnecessary git `.gitconfig` creation — moved safe.directory to container env vars (GIT_CONFIG_COUNT/KEY/VALUE), removed code-level workarounds

## Done
- [x] Executor agent — Strands agent with tool filtering, gate loop, git push
- [x] Deterministic gates — ruff, radon, pytest, diff size, auto-discovery
- [x] Reviewer agent — read-only evaluation, approve/reject with feedback, reviewer→executor retry loop
- [x] Monitor agent — audits entire workflow, writes PR summary, creates GitHub PR
- [x] Rich PR summaries — monitor produces narrative description for PRs
- [x] `.nubi/{task_id}/` namespacing — artifacts don't conflict across merged PRs
- [x] Controller integration tests — real K8s (k3d), mock LLM (fake agent), mock GitHub API. 8 scenarios
- [x] Sandbox hardening — read-only rootfs, shell allowlist, no SA token, storage limits
- [x] PR output — monitor creates GitHub PRs on approval

## Backlog
- [ ] MCP server — `create_taskspec`, `list_tasks`, `get_task_status`, `get_task_logs` for any agent harness
- [ ] Planner as MCP skill — interactive task scoping through conversation, then submit via MCP
- [ ] Per-stage model overrides — infrastructure exists, needs testing with different models per stage
- [ ] Langfuse integration — tracing, cost tracking, observability

## Ideas
- Cost budget enforcement based on Langfuse data
- Human-in-the-loop approval gates (pause reconciliation)
- GitOps integration examples (ArgoCD + TaskSpec)
- Discord/Slack input channel — submit tasks via chat
- Kustomize overlays for different environments
