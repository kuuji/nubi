# Nubi — TODO

## Done
- [x] Executor agent — Strands agent with tool filtering, gate loop, git push
- [x] Deterministic gates — ruff, radon, pytest, diff size, auto-discovery
- [x] Smart gate discovery — reads AGENTS.md/CLAUDE.md verification section for exact commands
- [x] Reviewer agent — read-only evaluation, approve/reject with feedback, reviewer→executor retry loop
- [x] Monitor agent — audits entire workflow, writes PR summary, creates GitHub PR
- [x] Monitor CI loop — polls GitHub Checks API, kicks back to executor on failure
- [x] Rich PR summaries — monitor produces narrative description for PRs
- [x] `.nubi/{task_id}/` namespacing — artifacts don't conflict across merged PRs
- [x] Existing branch support — executor checks out existing branches, monitor updates existing PRs
- [x] Controller integration tests — real K8s (k3d) in CI, 8 scenarios
- [x] Sandbox hardening — read-only rootfs, shell allowlist, no SA token, storage limits
- [x] Gate scoping — lint/complexity only check changed files, tests run everything
- [x] MCP server — FastMCP with streamable HTTP, 5 tools, K8s client wrapper
- [x] CI parity — same checks locally and in CI, integration tests with k3d in GitHub Actions

## Backlog
- [ ] Context management — executor fills context running diagnostic commands on real projects. Need smarter output handling (truncation, summarization, scoped execution)
- [ ] Deploy nubi + MCP server — MCP Dockerfile, Kustomize base in kuuji/nubi (CRD, controller, MCP server, RBAC, services), then ArgoCD app in gitops repo. Have nubi do this as a dogfood test of non-code infrastructure work.
- [ ] Planner as MCP skill — interactive task scoping through conversation, then submit via MCP
- [ ] Langfuse integration — tracing, cost tracking, observability

## Ideas
- Cost budget enforcement based on Langfuse data
- Human-in-the-loop approval gates (pause reconciliation)
- GitOps integration examples (ArgoCD + TaskSpec)
- Discord/Slack input channel — submit tasks via chat
- Kustomize overlays for different environments
- Integration/e2e test support — standard interface for tasks needing external dependencies (databases, APIs)
- Comment-driven re-execution — `/nubi fix` on a PR triggers a new executor run with PR comments as feedback
