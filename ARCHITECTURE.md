# Nubi — Architecture

**Nubi** — layers stitched together into something stronger than the individual pieces.

An agentic harness for orchestrating AI agent workflows on Kubernetes. Structured task specs in, sandboxed execution, layered validation, and observable results out.

---

## Overview

Nubi is a Kubernetes controller that turns a TaskSpec CRD into a pipeline of sandboxed agent work. Apply a spec, the controller creates an isolated namespace, runs agents as Jobs, gates their output through deterministic checks and agentic review, and reports everything back through the CRD status.

The pipeline:

```
kubectl apply TaskSpec
  → Controller creates task namespace + branch
  → Executor Agent (writes code + tests, pushes to git branch)
  → Deterministic Gates (lint, complexity, pytest — code, not an agent)
    → If gates fail: loop back to Executor with feedback (bounded)
  → Reviewer Agent (read-only evaluation, approve/request-changes/reject)
    → If request-changes: loop back to Executor with feedback (bounded)
  → Monitor Agent (audits entire workflow, writes PR summary, creates PR)
  → Done — PR created on the target repo
```

---

## Design Principles

1. **Deterministic where possible, agentic where necessary.** Linters, tests, and complexity checks don't need a model. Intent-matching and quality judgment do. Layer both.
2. **The spec is the contract.** The TaskSpec CRD is the single source of truth. Every stage reads from it. There is no separate spec format — the CRD IS the spec.
3. **Isolation is non-negotiable.** Agent code runs in gVisor-sandboxed containers with restricted permissions. Blast radius is contained by namespace.
4. **Git is the workspace.** No PVCs, no shared volumes, no pod-to-pod communication. Git branches are the shared workspace. CRD status is the shared state.
5. **Bounded autonomy.** Agents can loop (reviewer → executor), but within defined limits. Escalate to humans, don't spiral.
6. **Graceful degradation.** Monitor failure doesn't block task completion. The pipeline is resilient to non-critical stage failures.

---

## Technology Choices

| Component | Technology | Rationale |
|---|---|---|
| Agent Framework | [Strands Agents SDK](https://strandsagents.com/) (Python) | Model-driven, minimal orchestration overhead. Tools are Python functions. |
| Controller Framework | [kopf](https://kopf.readthedocs.io/) | Python K8s operator framework. Keeps the entire stack single-language. |
| Sandbox Runtime | [gVisor](https://gvisor.dev/) (`runsc`) | Syscall-level isolation via user-space kernel. K8s-native RuntimeClass. |
| Execution Model | Kubernetes Jobs | Ephemeral, namespaced, auto-cleaned. Standard K8s primitives. |
| CRD | `TaskSpec` (`nubi.io/v1`) | The task spec IS the Custom Resource. Applied via `kubectl`, reconciled by the controller. |
| Language | Python 3.12+ | Strands SDK is Python-first. kopf is Python. Single language across controller and agents. |

---

## Git as Workspace

This is a critical design decision. There are no PVCs, no shared volumes, no pod-to-pod communication.

**Git is the shared workspace.** Each task gets a branch (`nubi/{task-id}`). Each agent pod clones the branch, does its work, pushes.

**CRD status is the shared state.** Metadata, decisions, phase transitions — all in the TaskSpec status subresource. Agents don't talk to each other. They read and write to git, and the controller watches Jobs and advances the pipeline.

**Artifacts are namespaced by task.** Each pipeline run stores its artifacts in `.nubi/{task-id}/` on the branch — `result.json`, `gates.json`, `review.json`, `monitor.json`. This prevents conflicts when multiple PRs merge, and preserves the audit trail of every run.

On completion, the monitor creates a PR (if `output.format: pr`) with a narrative summary.

The status tracks workspace state:

```yaml
status:
  phase: Reviewing
  workspace:
    repo: kuuji/some-app
    branch: nubi/task-abc123
    headSHA: "a1b2c3d"
  stages:
    executor:
      status: complete
      attempts: 1
      commitSHA: "a1b2c3d"
      summary: "Added rate limiting middleware"
    reviewer:
      status: approve
      decision: approve
      feedback: "LGTM — clean implementation"
    monitor:
      status: approve
      decision: approve
      summary: "Pipeline executed correctly"
      prURL: "https://github.com/kuuji/some-app/pull/42"
```

Why this works:
- **No coordination problems.** Agents are sequential — only one writes at a time.
- **Full audit trail.** Every change is a commit. Every decision is in CRD status. Artifacts persist on the branch.
- **Crash recovery is trivial.** Branch is durable. Pod dies, new pod clones the branch, picks up where things left off.
- **Cleanup is simple.** Delete the branch. Delete the namespace. Done.

---

## Pipeline Stages

### Agents and Roles

| Role | What it does | Write access | Is a pod? |
|---|---|---|---|
| **Executor** | Does the work — writes code, tests, configs | Yes (git push) | Yes |
| **Deterministic Gates** | Lint, complexity, test runner, diff size | No | **No** — runs inside executor pod |
| **Reviewer** | Read-only evaluation — quality, security, architecture fit | No (read-only) | Yes |
| **Monitor** | Audits entire workflow, writes PR summary, creates PR | Yes (GitHub API only) | Yes |

### Stage 1: Execution

The Executor runs inside a gVisor-sandboxed pod. Clones the task branch, does the work (including writing tests), pushes.

- **Tools (configurable per spec):** shell, file read/write, git
- **Output:** Commits on the task branch + structured result in `.nubi/{task-id}/result.json`

The executor's system prompt instructs it to write both implementation code and tests. The quality of tests is enforced by gates (pytest must pass) and reviewed by the reviewer.

### Stage 2: Deterministic Gates

Runs inside the executor's gate loop — not a separate pod. Fast-fail checks after each executor attempt:

- **Linting** — ruff (Python), eslint (Node)
- **Complexity** — radon cyclomatic complexity
- **Test execution** — pytest (Python), jest (Node)
- **Diff size** — total changed lines vs threshold

Gates are auto-discovered based on changed file types and available tools. If any gate fails, the executor retries with the failure output as feedback. No LLM call needed for gate evaluation — this is pure code.

Gate results are written to `.nubi/{task-id}/gates.json`.

### Stage 3: Review

The Reviewer does a PR-style evaluation. Read-only access to the branch.

- Code quality, readability, duplication
- Architecture fit — does this align with the project's patterns?
- Security, performance, maintainability
- Test quality — are the executor's tests thorough enough?
- Gaps — anything the spec asked for that's missing?

**Tools:** file read, git diff/log, shell (read-only), submit_review
**Output:** Approve, request-changes, or reject with feedback. Request-changes loops back to the executor (bounded by `loop_policy.max_retries`). Reject escalates to human.

Review result is written to `.nubi/{task-id}/review.json`.

### Stage 4: Monitor

The Monitor audits the entire pipeline workflow — both process quality and output quality. It reads all artifacts via the GitHub REST API (no git clone needed).

- Did the executor produce reasonable changes?
- Did gates pass? Were there excessive retries?
- Did the reviewer catch everything?
- Are there security concerns in the diff?

If approved, the monitor **creates a GitHub PR** with a narrative summary describing what changed, key implementation decisions, validation results, and caveats.

If flagged, the task goes to Done without a PR, and concerns are recorded in the CRD status for human review.

**Tools:** read_branch_file, read_diff, list_branch_files, submit_audit (GitHub REST API)
**Output:** Approve (creates PR) or flag (records concerns). Monitor failure is graceful — task completes regardless.

Monitor result is written to `.nubi/{task-id}/monitor.json` via GitHub Contents API.

### Loop Resolution

```
Deterministic gate fails → Executor retries with failure output (up to 3 attempts)
Reviewer request-changes → Executor retries with review feedback
Max retries exceeded     → Escalate to human (or abandon, per spec)
Monitor failure          → Graceful degradation — task completes as Done
```

---

## Kubernetes Controller

### Why a Controller?

The pipeline is inherently Kubernetes-native — namespaces, Jobs, NetworkPolicies, ResourceQuotas, cleanup. The controller approach makes it a first-class K8s citizen:

- **Declarative lifecycle** — reconciles desired state, handles failures automatically.
- **Native status** — `kubectl describe taskspec my-task` shows exactly where things are.
- **GitOps** — commit a TaskSpec YAML to a repo, ArgoCD applies it.
- **Crash recovery** — controller restarts, reads CRD state from etcd, picks up where it left off.
- **Event-driven** — watches Job completions via annotations, not polling.

### CRD: TaskSpec

```yaml
apiVersion: nubi.io/v1
kind: TaskSpec
metadata:
  name: add-rate-limiting
  namespace: nubi-system
spec:
  description: "Add rate limiting to API endpoints with tests"
  type: code-change
  inputs:
    repo: kuuji/some-app
    branch: main
    files_of_interest:
      - src/api/routes.py
  constraints:
    timeout: 900s
    total_timeout: 2700s
    network_access: [github.com]
    tools: [shell, git, file_read, file_write]
    resources:
      cpu: "500m"
      memory: 256Mi
  review:
    enabled: true
    focus: [correctness, test_coverage, security]
  loop_policy:
    max_retries: 2
    reviewer_to_executor: true
    on_max_retries: escalate
  output:
    format: pr
    pr:
      title_prefix: "nubi:"
      labels: [nubi, automated]
      draft: true
  monitoring:
    summary: true
```

### Event-Driven Reconciliation

The controller does NOT watch `status.phase` (self-trigger footgun) and does NOT poll on a timer. It uses annotations on the TaskSpec to signal stage completions:

1. **`on_taskspec_created`** — Creates namespace, credentials, spawns executor Job
2. **`on_job_status_change`** — Watches all nubi-managed Jobs. On completion, annotates the TaskSpec with the result
3. **`on_executor_completion`** — Reads results from GitHub API, advances to reviewer or retries
4. **`on_reviewer_completion`** — Reads review from GitHub API, advances to monitor, retries, or escalates
5. **`on_monitor_completion`** — Reads monitor result, marks task Done (always — graceful degradation)

Every Job gets labels for routing:

```yaml
metadata:
  labels:
    nubi.io/task-id: "abc123"
    nubi.io/taskspec-namespace: "nubi-system"
    nubi.io/stage: "executor"
    app.kubernetes.io/managed-by: "nubi"
```

### Credential Scoping

The controller creates a scoped Secret in the task namespace at spawn time with only the credentials that stage needs:

| Stage | GitHub Token | LLM API Key |
|---|---|---|
| Executor | Yes | Yes |
| Reviewer | Yes | Yes |
| Monitor | Yes | Yes |
| Gates | No | No (runs inside executor) |

Per-stage model overrides are supported via environment variables (`NUBI_REVIEWER_MODEL_ID`, `NUBI_MONITOR_MODEL_ID`).

---

## Agent Definitions

Each agent is a Strands Agent with a specific system prompt and tool set. Single container image (`ghcr.io/kuuji/nubi-agent:latest`) with tool availability controlled by environment variables at spawn time.

### Executor

```
Role: Implement the task — write code, tests, configs.
Tools: shell, file_read, file_write, git, gates (discover + run)
Isolation: gVisor sandbox, restricted PSS, scoped NetworkPolicy
Entrypoint: python -m nubi.entrypoint
```

### Reviewer

```
Role: Read-only evaluation — quality, architecture fit, security, test coverage.
Tools: shell (read-only), file_read, file_list, git_read (diff/log/status), submit_review
Isolation: gVisor sandbox (read-only tools only)
Entrypoint: python -m nubi.reviewer_entrypoint
```

### Monitor

```
Role: Audit entire workflow, write PR summary, create GitHub PR.
Tools: read_branch_file, read_diff, list_branch_files, submit_audit (GitHub REST API)
Isolation: gVisor sandbox (no git clone — GitHub API only)
Entrypoint: python -m nubi.monitor_entrypoint
```

---

## Security Model

### gVisor Sandboxing

- **Syscall filtering:** gVisor intercepts all syscalls in user space. Agent code never directly touches the host kernel.
- **Restricted PSS:** No privilege escalation, no host mounts, read-only root filesystem, non-root user (uid 65534).
- **Shell allowlist:** Only safe commands permitted (git, python, pytest, ruff, ls, grep, etc.). Blocks curl, wget, nc, ssh, apt-get, and pipe/chain attempts to bypass.
- **NetworkPolicy:** Deny-all default. Egress explicitly allowed per spec (e.g., `github.com`).
- **Resource limits:** CPU and memory caps per pod, enforced by ResourceQuota.
- **Timeout enforcement:** `activeDeadlineSeconds` on Jobs — hard kill if exceeded.
- **No service account token:** `automountServiceAccountToken: false` — agents can't access the K8s API.

### Trust Boundaries

```
┌─────────────────────────────────────────────────────────┐
│ Controller (trusted)                                    │
│  - Creates namespaces, manages lifecycle                │
│  - Has cluster RBAC for namespace/job management        │
│  - Runs outside sandbox in nubi-system                  │
│  - Reads results via GitHub REST API                    │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │ Task Namespace (partially trusted)                │  │
│  │  - ResourceQuota, NetworkPolicy enforced          │  │
│  │  - Scoped credentials per stage                   │  │
│  │                                                   │  │
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │ Agent Pod (untrusted code execution)        │  │  │
│  │  │  - gVisor runtime                          │  │  │
│  │  │  - Restricted PSS, read-only rootfs        │  │  │
│  │  │  - Shell allowlist                         │  │  │
│  │  │  - emptyDir workspace (ephemeral)          │  │  │
│  │  │  - No K8s API access                       │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## Namespace Lifecycle

Each task gets its own namespace: `nubi-{task-id}`

**Creation:**
1. Namespace with labels (`nubi.io/task-id`, `nubi.io/task-type`) and `pod-security.kubernetes.io/enforce: restricted`
2. ResourceQuota from spec constraints
3. NetworkPolicy (deny-all + DNS egress + specific HTTP/HTTPS egress from spec)
4. Scoped credentials Secret per stage

**Cleanup:**
- Success: namespace deleted after configurable TTL (default: 1 hour, for debugging)
- Failure: namespace retained for investigation (configurable)

---

## Repo Structure

```
kuuji/nubi/
├── ARCHITECTURE.md
├── AGENTS.md
├── pyproject.toml
├── src/nubi/
│   ├── controller/
│   │   ├── handlers.py        # kopf handlers — event-driven pipeline orchestration
│   │   ├── namespace.py       # Task namespace lifecycle
│   │   ├── sandbox.py         # gVisor Job builders (executor, reviewer, monitor)
│   │   ├── credentials.py     # Per-stage Secret creation
│   │   └── results.py         # Read results from GitHub API
│   ├── crd/
│   │   ├── schema.py          # TaskSpec CRD schema (Pydantic v2)
│   │   └── defaults.py        # Default values and constants
│   ├── agents/
│   │   ├── executor.py        # Executor Strands agent factory
│   │   ├── reviewer.py        # Reviewer Strands agent factory
│   │   ├── monitor.py         # Monitor Strands agent factory
│   │   ├── logging_handler.py # Strands callback handler for structured logging
│   │   ├── result.py          # ExecutorResult model
│   │   ├── gate_result.py     # GatesResult model + gate policy
│   │   ├── review_result.py   # ReviewResult model
│   │   └── monitor_result.py  # MonitorResult model
│   ├── tools/
│   │   ├── __init__.py        # Tool registry — filters by NUBI_TOOLS env var
│   │   ├── shell.py           # Sandboxed shell with command allowlist
│   │   ├── git.py             # Git operations (clone, diff, log, commit, push)
│   │   ├── files.py           # Workspace-scoped file read/write/list
│   │   ├── gates.py           # Gate discovery and execution
│   │   ├── review.py          # submit_review tool for reviewer
│   │   └── github_api.py      # GitHub REST API tools for monitor
│   ├── entrypoint.py          # Executor pod entrypoint
│   ├── reviewer_entrypoint.py # Reviewer pod entrypoint
│   ├── monitor_entrypoint.py  # Monitor pod entrypoint
│   └── exceptions.py          # Custom exception types
├── manifests/
│   └── base/
│       ├── crd.yaml           # TaskSpec CRD definition
│       └── ...                # Controller + RBAC + RuntimeClass
├── images/
│   ├── controller/Dockerfile  # Controller image (kopf + nubi)
│   ├── agent/Dockerfile       # Unified agent image (Strands + tools)
│   └── fake-agent/Dockerfile  # Test agent for integration tests
├── scripts/
│   ├── e2e.sh                 # Live end-to-end test runner
│   ├── integration-setup.sh   # k3d cluster for integration tests
│   └── dev-cluster.sh         # Local dev cluster setup
└── tests/
    ├── integration/           # Real K8s (k3d) + mocked results
    ├── agents/                # Agent model tests
    ├── tools/                 # Tool tests
    └── test_*.py              # Unit tests for all modules
```

---

## Integration Points

### MCP Server (Planned)

An MCP server will expose nubi as tools for any AI agent harness:

```
create_taskspec  — Submit a new task
list_tasks       — List active/completed tasks
get_task_status  — Get detailed status of a task
get_task_logs    — Read pod logs for a task
```

This enables an interactive planner UX — discuss the task with your agent, refine the spec through conversation, then submit it via MCP. Works with Claude Code, Claude Desktop, or any MCP-compatible client.

### kubectl (Native CLI)

No custom CLI needed — kubectl IS the CLI:

```bash
kubectl apply -f task.yaml              # Submit a task
kubectl get taskspecs -w                # Watch progress
kubectl describe taskspec my-task       # Detailed status
kubectl logs -n nubi-abc123 -l nubi.io/stage=executor  # Agent logs
kubectl delete taskspec my-task         # Cancel
```

### GitOps

TaskSpec YAMLs in a git repo + ArgoCD = scheduled, repeatable, auditable task submission.

---

## Open Questions

1. **Planner as interactive phase:** The planner is likely a conversational step before the TaskSpec exists — the user discusses what they want, the agent helps scope it, then submits via MCP. Not an autonomous pod in the pipeline.

2. **Human-in-the-loop hooks:** Configurable approval gates — after review, or fully autonomous with escalation only on failure. Could use a `spec.approval` field that pauses reconciliation.

3. **Cost budgets:** LLM cost tracking and enforcement. Could integrate with Langfuse for tracing and cost visibility.

4. **Parallel sub-task execution:** When tasks are decomposed, run independent sub-tasks in parallel? Adds complexity to review/monitor stages.
