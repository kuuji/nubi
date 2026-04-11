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
- [ ] Context management — needs more research. Subagent approach solved overflow but lost error detail. Scoped gates help. Open question.
- [x] Deploy manifests — Kustomize base, MCP Dockerfile, split monolithic deployment.yaml (PR #6, done by nubi)
- [x] Deploy to gitops-lab — ArgoCD Application pointing to kuuji/nubi/manifests/, cluster-specific secrets + ingress for MCP
- [x] Repo input normalization — `git_clone` strips full GitHub URLs to `owner/repo` format
- [x] MCP create_taskspec validation — Pydantic validator on `TaskInputs.repo` normalizes at schema level
- [x] Controller delete handler — add `@kopf.on.delete` to clean up task namespace, jobs, and pods when a TaskSpec is deleted (PR #7)
- [x] Controller update/retry handling — retry via `nubi.io/retry` annotation on Failed/Escalated tasks (PR #7)
- [ ] Graceful task cancellation — ability to stop a running task via TaskSpec status/MCP without deleting the namespace, so logs and artifacts are preserved for debugging
- [x] Smarter git_commit — `git_commit` accepts optional `files` param for selective staging; workspace excludes prevent junk from being staged
- [x] Workspace .gitignore — uses `.git/info/exclude` (local-only, never committed) to exclude `.cache/`, `.local/`, `__pycache__/`, `.venv/`, `.nubi/`
- [ ] Guard against destructive git operations — agent uses `run_shell` to `git reset --hard`, undoing its own work. Consider blocking destructive git commands in the shell allowlist
- [ ] Agent pip install leaks into workspace — agent tries to install dev tools (ruff, pytest) at runtime into `.local/`, but they don't end up in PATH for the gate runner. Either pre-install dev tools in the agent image (`pip install ".[dev]"`) or make gate runner aware of `.local/bin`
- [ ] Planner network inference — the task interview/planner should analyze the task description to determine what network access the agent will need (e.g. external APIs, package registries) and set `constraints.network_access` accordingly
- [ ] Better CI feedback — pass actual check run output to executor on retry, don't retry on timeouts
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
