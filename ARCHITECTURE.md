# Nubi — Architecture

**Nubi** (누비) — Korean quilting art. Layers stitched together into something stronger than the individual pieces.

An agentic harness for orchestrating AI agent workflows on Kubernetes. Structured task specs in, sandboxed execution, layered validation, and observable results out.

---

## Overview

Nubi is a Kubernetes controller that turns a TaskSpec CRD into a pipeline of sandboxed agent work. Apply a spec, the controller creates an isolated namespace, runs agents as Jobs, gates their output through deterministic checks and agentic review, and reports everything back through the CRD status.

The v1 pipeline for a simple task:

```
kubectl apply TaskSpec
  → Controller creates task namespace + branch
  → Executor Agent (does the work, pushes to git branch)
  → Deterministic Gates (lint, test, secret scan — code, not an agent)
  → Reviewer Agent (read-only evaluation, approve/reject)
    → If rejected: loop back to Executor (bounded)
  → Summary call (one LLM call, not a pod)
  → Done — branch becomes PR or gets cleaned up
```

For complex tasks, the Planner is opt-in via `decomposition.allow: true`.

---

## Design Principles

1. **Deterministic where possible, agentic where necessary.** Linters, tests, and dry-runs don't need a model. Intent-matching and quality judgment do. Layer both.
2. **The spec is the contract.** The TaskSpec CRD is the single source of truth. Every stage reads from it. There is no separate spec format — the CRD IS the spec.
3. **Isolation is non-negotiable.** Agent code runs in gVisor-sandboxed containers with restricted permissions. Blast radius is contained by namespace.
4. **Git is the workspace.** No PVCs, no shared volumes, no pod-to-pod communication. Git branches are the shared workspace. CRD status is the shared state.
5. **Observability is built in, not bolted on.** Every agent action traces to Langfuse via OpenTelemetry. The summary call turns traces into human narrative.
6. **Bounded autonomy.** Agents can loop (reviewer → executor), but within defined limits. Escalate to humans, don't spiral.

---

## Technology Choices

| Component | Technology | Rationale |
|---|---|---|
| Agent Framework | [Strands Agents SDK](https://strandsagents.com/) (Python) | Model-driven, minimal orchestration overhead. Tools are Python functions. Built-in OTEL support. |
| Controller Framework | [kopf](https://kopf.readthedocs.io/) | Python K8s operator framework. Keeps the entire stack single-language (Python + Strands). Mature, production-tested. |
| Observability | [Langfuse](https://langfuse.com/) (self-hosted) | Open source, OTEL-native, first-class Strands integration. Traces, costs, evals. |
| Sandbox Runtime | [gVisor](https://gvisor.dev/) (`runsc`) | Syscall-level isolation via user-space kernel. K8s-native RuntimeClass. No hypervisor overhead. |
| Execution Model | Kubernetes Jobs | Ephemeral, namespaced, auto-cleaned via owner references. Standard K8s primitives. |
| CRD | `TaskSpec` (`nubi.io/v1`) | The task spec IS the Custom Resource. Applied via `kubectl`, reconciled by the controller. |
| Language | Python | Strands SDK is Python-first. kopf is Python. Single language across controller and agents. |

---

## Git as Workspace

This is a critical design decision. There are no PVCs, no shared volumes, no pod-to-pod communication.

**Git is the shared workspace.** Each task gets a branch (`nubi/{task-id}`). Each agent pod clones the branch, does its work, pushes. Local disk is `emptyDir` — ephemeral, gone when the pod dies.

**CRD status is the shared state.** Metadata, decisions, phase transitions — all in the TaskSpec status subresource. Agents don't talk to each other. They read and write to git, and the controller watches Jobs and advances the pipeline.

On completion, the branch either becomes a PR (if `output.format: pr`) or gets cleaned up.

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
    validator:
      status: complete
      deterministic:
        lint: passed
        tests: passed
      testCommitSHA: "d4e5f6g"
    reviewer:
      status: pending
```

Why this works:
- **No coordination problems.** Agents are sequential — only one writes at a time.
- **Full audit trail.** Every change is a commit. Every decision is in CRD status.
- **Crash recovery is trivial.** Branch is durable. Pod dies, new pod clones the branch, picks up where things left off.
- **Cleanup is simple.** Delete the branch. Delete the namespace. Done.

---

## Pipeline Stages

### Agents and Roles

| Role | What it does | Write access | Is a pod? |
|---|---|---|---|
| **Planner** | Decomposes complex tasks into sub-tasks | No (read-only) | Yes, but optional |
| **Executor** | Does the work — writes code, configs, docs | Yes (git push) | Yes |
| **Validator** | Writes tests and runs them to verify executor's work | Yes (git push — commits test suites) | Yes |
| **Reviewer** | Read-only evaluation — quality, security, architecture fit | No (read-only) | Yes |
| **Deterministic Gates** | Lint, type check, secret scan, etc. | No | **No** — code, not an agent |
| **Monitorer** | Produces human-readable summary of the run | No | **No** — single LLM API call |

The key distinction between Validator and Reviewer:
- **Validator writes things.** It creates test suites — a creative act with write access. Its output is actual code in the codebase.
- **Reviewer reads things.** It's a judgment call — read-only. Its output is approve/reject with feedback.

### Stage 1: Planning (Optional)

The Planner decomposes complex tasks into ordered sub-tasks. Skipped for simple tasks via `decomposition.allow: false` (the default).

- **Tools:** file read, git log/diff, web search. Read-only.
- **Output:** Execution plan — ordered list of sub-specs.

### Stage 2: Execution

The Executor runs inside a gVisor-sandboxed pod. Clones the task branch, does the work, pushes.

- **Tools (configurable per spec):** shell, file read/write, git, web search, HTTP requests (scoped by NetworkPolicy).
- **Output:** Commits on the task branch + structured result in CRD status.

### Stage 3: Deterministic Gates

Not an agent. Code that runs fast-fail checks before any agentic review:

- Linting (ruff, eslint)
- Type checking (mypy, tsc)
- Test execution (pytest, jest)
- Secret scanning (trufflehog, gitleaks)
- Diff size checks
- Custom commands from spec

If any gate fails, the executor retries with the failure output as feedback. No LLM call needed — this is pure code.

### Stage 4: Validation

The Validator writes tests to verify the executor's work, then runs them. This is the Tester from Forge — its output is an actual test suite committed to the branch.

- **Tools:** file read/write, git, shell (for running tests). Has write access.
- **Output:** Test suite committed to the branch + pass/fail with details.

### Stage 5: Review

The Reviewer does a PR-style evaluation. Different question than validation — not "is this correct?" but "is this good?"

- Code quality, readability, duplication
- Architecture fit — does this align with the project's patterns?
- Security, performance, maintainability
- Gaps — anything the spec asked for that's missing?

**Tools:** file read, git diff/log. Read-only.
**Output:** Approve or reject with feedback. Rejection loops back to the executor (bounded by `loop_policy.max_retries`).

### Stage 6: Summary

Not a pod. The controller collects structured data from CRD status + Langfuse traces and makes one LLM API call to produce a human-readable narrative:

- What was the task?
- What did the executor do? What decisions did it make?
- What did gates/validation catch? What did review flag?
- How many loops? What was fixed?
- Final state + Langfuse trace link.

### Loop Resolution

```
Deterministic gate fails → Executor retries with failure output
Validator fails          → Executor retries with test failures
Reviewer rejects         → Executor retries with review feedback
Max retries exceeded     → Escalate to human (or abandon, per spec)
```

---

## Kubernetes Controller

### Why a Controller?

The pipeline is inherently Kubernetes-native — namespaces, Jobs, NetworkPolicies, ResourceQuotas, cleanup. The controller approach makes it a first-class K8s citizen:

- **Declarative lifecycle** — reconciles desired state, handles failures automatically.
- **Native status** — `kubectl describe taskspec my-task` shows exactly where things are.
- **GitOps** — commit a TaskSpec YAML to a repo, ArgoCD applies it.
- **Crash recovery** — controller restarts, reads CRD state from etcd, picks up where it left off.
- **Event-driven** — watches Job completions, not polling.

### CRD: TaskSpec

```yaml
apiVersion: nubi.io/v1
kind: TaskSpec
metadata:
  name: add-rate-limiting
  namespace: nubi-system
spec:
  description: "Add rate limiting to API endpoints"
  type: code-change
  inputs:
    repo: kuuji/some-app
    branch: main
    files_of_interest:
      - src/api/routes.py
  constraints:
    timeout: 300s
    total_timeout: 1800s
    network_access: [github.com, pypi.org]
    tools: [shell, git, file_read, file_write]
    resources:
      cpu: "1"
      memory: 512Mi
  validation:
    deterministic: [lint, test, secret_scan]
    agentic: [intent_match, completeness]
  review:
    enabled: true
    focus: [code_quality, architecture_fit]
  loop_policy:
    max_retries: 2
    validator_to_executor: true
    reviewer_to_executor: true
    reviewer_to_planner: false
    on_max_retries: escalate
  output:
    format: pr
    pr:
      title_prefix: "nubi:"
      labels: [nubi, automated]
      draft: true
  decomposition:
    allow: false
    max_depth: 2
    max_subtasks: 5
  monitoring:
    summary: true
    notify:
      - channel: discord
        target: "<channel-id>"
```

### Event-Driven Reconciliation

The controller does NOT watch `status.phase` (self-trigger footgun) and does NOT poll on a timer. It uses the standard Kubernetes controller pattern: watch secondary resources (Jobs).

```python
# Primary: react to new TaskSpecs
@kopf.on.create('nubi.io', 'v1', 'taskspecs')
async def on_taskspec_created(spec, name, patch, **kwargs):
    ns = create_task_namespace(name)
    spawn_executor_job(ns, spec, owner_ref=name)
    patch.status['phase'] = 'Executing'

# Secondary: react to Job completions
@kopf.on.field('batch', 'v1', 'jobs', field='status.conditions',
               labels={'nubi.io/task-id': kopf.PRESENT})
async def on_job_status_change(name, namespace, status, labels, **kwargs):
    task_id = labels['nubi.io/task-id']
    stage = labels['nubi.io/stage']
    if job_succeeded(status):
        advance_phase(task_id, stage)
    elif job_failed(status):
        handle_failure(task_id, stage)
```

Every Job gets labels and owner references:

```yaml
metadata:
  labels:
    nubi.io/task-id: "abc123"
    nubi.io/stage: "executor"
  ownerReferences:
    - apiVersion: nubi.io/v1
      kind: TaskSpec
      name: add-rate-limiting
```

Owner references give garbage collection for free — delete the TaskSpec, all Jobs and their pods get cleaned up automatically.

### Crash Recovery

Inherently solved by the event-driven pattern:

- **Idempotent handlers:** Check before creating — "ensure X exists" not "create X". Don't spawn duplicate Jobs.
- **Resume on restart:** kopf's `on.resume` replays all unfinished TaskSpecs when the controller comes back up.
- **Missed events:** Completed Jobs that were missed during downtime get picked up on re-watch.

No special crash recovery logic needed. The pattern handles it.

### Credential Scoping

The controller creates a Secret in the task namespace at spawn time with only the credentials that stage needs:

| Stage | GitHub Token | LLM API Key |
|---|---|---|
| Executor | ✓ | ✓ |
| Validator | ✓ | ✓ |
| Reviewer | ✗ (read-only, no git push) | ✓ |
| Deterministic Gates | ✗ | ✗ (just runs tools) |

Least-privilege per stage. The reviewer can't push to git. The gates don't need credentials at all.

---

## Agent Definitions

Each agent is a Strands Agent with a specific system prompt and tool set. Single container image (`ghcr.io/kuuji/nubi-agent:latest`) with tool availability controlled by environment variables at spawn time.

### Planner

```
Role: Decompose complex tasks into actionable sub-tasks.
Tools: file_read, git_log, git_diff, web_search
Isolation: Standard container (read-only operations)
When: Only when decomposition.allow: true in the spec
```

### Executor

```
Role: Implement the task.
Tools: shell, file_read, file_write, git, web_search, http_request (scoped)
Isolation: gVisor sandbox, restricted PSS, scoped NetworkPolicy
Credentials: GitHub token + LLM API key
```

### Validator

```
Role: Write tests to verify the executor's work, then run them.
Tools: file_read, file_write, git, shell
Isolation: gVisor sandbox
Credentials: GitHub token + LLM API key
```

### Reviewer

```
Role: Read-only evaluation — quality, architecture fit, security, gaps.
Tools: file_read, git_diff, git_log
Isolation: Standard container (read-only)
Credentials: LLM API key only (no git push)
Output: approve or reject with feedback
```

---

## Security Model

### gVisor Sandboxing

- **Syscall filtering:** gVisor intercepts all syscalls in user space. Agent code never directly touches the host kernel.
- **Restricted PSS:** No privilege escalation, no host mounts, read-only root filesystem, non-root user.
- **NetworkPolicy:** Deny-all default. Egress explicitly allowed per spec (e.g., `github.com`, `pypi.org`).
- **Resource limits:** CPU and memory caps per pod, enforced by ResourceQuota.
- **Timeout enforcement:** `activeDeadlineSeconds` on Jobs — hard kill if exceeded.

### Trust Boundaries

```
┌─────────────────────────────────────────────────────────┐
│ Controller (trusted)                                    │
│  - Creates namespaces, manages lifecycle                │
│  - Has cluster RBAC for namespace/job management        │
│  - Runs outside sandbox in nubi-system                  │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │ Task Namespace (partially trusted)                │  │
│  │  - ResourceQuota, NetworkPolicy enforced          │  │
│  │  - Scoped credentials per stage                   │  │
│  │                                                   │  │
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │ Agent Pod (untrusted code execution)        │  │  │
│  │  │  - gVisor runtime                          │  │  │
│  │  │  - Restricted PSS                          │  │  │
│  │  │  - emptyDir workspace (ephemeral)          │  │  │
│  │  │  - Git clone → work → push                 │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## Namespace Lifecycle

Each task gets its own namespace: `nubi-{task-id-short}`

**Creation:**
1. Namespace with labels (`nubi.io/task-id`, `nubi.io/task-type`) and `pod-security.kubernetes.io/enforce: restricted`
2. gVisor RuntimeClass reference
3. ResourceQuota from spec constraints
4. NetworkPolicy (deny-all + specific egress from spec)
5. Scoped credentials Secret

**Cleanup:**
- Success: namespace deleted after configurable TTL (default: 1 hour, for debugging)
- Failure: namespace retained for investigation (configurable)
- Owner references on Jobs/Secrets mean deleting the namespace cascades everything

---

## Versioned Rollout

Each version is independently usable. v0.1 ships something real. Each subsequent version adds quality.

### v0.1 — Executor Only

```
kubectl apply TaskSpec
  → Controller creates namespace + git branch
  → Spawns executor pod
  → Executor does work, pushes to branch
  → Controller reports result in CRD status
```

This is the MVP. A single agent doing work in a sandbox, tracked by a CRD. No validation, no review — just execution.

### v0.2 — Deterministic Gates

After the executor, run lint/test/secret-scan as code (not an agent). Fast-fail before any agentic evaluation. Gate failures loop back to the executor with the output.

### v0.3 — Validator

After the executor passes gates, the Validator writes tests and runs them. Its output is an actual test suite committed to the branch.

### v0.4 — Reviewer

After validation, the Reviewer evaluates quality — architecture fit, code quality, gaps. Can loop back to the executor with feedback.

### v0.5 — Planner

For complex tasks, the Planner decomposes before executing. Opt-in via `decomposition.allow: true`.

### v0.6 — Monitorer

Summary call at the end. Controller collects CRD status + Langfuse traces, makes one LLM API call to produce a human-readable narrative. Not a pod.

---

## Minimal Viable Deployment (v0.1)

What you need to run v0.1:

1. **gVisor on cluster nodes** — `runsc` installed, containerd configured with the gVisor runtime handler.
2. **gVisor RuntimeClass** applied:
   ```yaml
   apiVersion: node.k8s.io/v1
   kind: RuntimeClass
   metadata:
     name: gvisor
   handler: runsc
   ```
3. **`nubi-system` namespace** created.
4. **TaskSpec CRD** applied.
5. **Controller Deployment** in `nubi-system`.
6. **One Secret** with credentials:
   ```yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: nubi-credentials
     namespace: nubi-system
   type: Opaque
   stringData:
     github-token: <GitHub PAT>
     llm-api-key: <Anthropic/OpenAI API key>
   ```

That's it. No Langfuse, no complex RBAC, no Helm chart. Apply the CRD, deploy the controller, create a Secret with creds. Start submitting TaskSpecs.

Langfuse, Helm packaging, and advanced RBAC come later as the pipeline matures.

---

## Integration Points

### OpenClaw (Primary Orchestrator)

A **nubi skill** in OpenClaw translates human intent into TaskSpec CRDs:

```
User: "Add rate limiting to the API in kuuji/some-app"
  → OpenClaw agent reads nubi skill
  → Produces TaskSpec YAML
  → kubectl apply (or K8s API call)
  → Controller runs the pipeline
  → Summary relayed to user
```

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

## Repo Structure

```
kuuji/nubi/
├── ARCHITECTURE.md
├── README.md
├── pyproject.toml
├── src/
│   └── nubi/
│       ├── controller/
│       │   ├── handlers.py      # kopf handlers (on.create, on.field for Jobs)
│       │   ├── namespace.py     # Task namespace lifecycle
│       │   ├── sandbox.py       # gVisor Job creation
│       │   ├── gates.py         # Deterministic gate runners
│       │   └── credentials.py   # Per-stage Secret creation
│       ├── crd/
│       │   ├── schema.py        # TaskSpec CRD schema (Pydantic)
│       │   └── defaults.py      # Default values, spec inheritance
│       ├── agents/
│       │   ├── base.py          # Base Strands agent factory
│       │   ├── planner.py
│       │   ├── executor.py
│       │   ├── validator.py
│       │   └── reviewer.py
│       ├── tools/
│       │   ├── shell.py         # Sandboxed shell execution
│       │   ├── git.py           # Git operations
│       │   ├── files.py         # File read/write
│       │   └── web.py           # Web search, HTTP requests
│       └── output/
│           ├── pr.py            # GitHub PR creation
│           ├── summary.py       # LLM summary call
│           └── notify.py        # Discord/Telegram notification
├── prompts/
│   ├── planner.md
│   ├── executor.md
│   ├── validator.md
│   └── reviewer.md
├── manifests/                   # Raw K8s manifests for v0.1
│   ├── crd.yaml                 # TaskSpec CRD definition
│   ├── namespace.yaml           # nubi-system
│   ├── runtimeclass.yaml        # gVisor RuntimeClass
│   ├── deployment.yaml          # Controller
│   └── rbac.yaml                # ServiceAccount, ClusterRole, bindings
├── examples/
│   ├── simple-code-change.yaml
│   ├── complex-refactor.yaml
│   └── research-task.yaml
├── images/
│   ├── controller/
│   │   └── Dockerfile           # Controller image (kopf + nubi)
│   └── agent/
│       └── Dockerfile           # Unified agent image (Strands + tools)
├── tests/
│   ├── test_handlers.py
│   ├── test_gates.py
│   ├── test_schema.py
│   └── test_credentials.py
└── hack/
    ├── install-crd.sh
    └── run-local.sh
```

---

## Open Questions

1. **Model selection per agent:** Should the spec define which model each agent uses, or should the controller pick based on task complexity? Could be a controller-level default with per-spec overrides.

2. **State persistence across retries:** When the executor retries after feedback, does it start fresh (clone branch, see previous commits) or resume? Starting fresh with git history is simpler and naturally gives context.

3. **Parallel sub-task execution:** When the planner produces independent sub-tasks, run them in parallel? The controller can manage this (multiple Jobs), but it adds complexity to validation/review.

4. **Human-in-the-loop hooks:** Configurable approval gates — after planning, after review, or fully autonomous with escalation only on failure. Could use a `spec.approval` field that pauses reconciliation.

5. **Cost budgets:** Langfuse tracks costs automatically. Budget enforcement can come in v0.3+ once there's real usage data to calibrate against.

6. **Single image vs per-agent images:** Leaning toward single `nubi-agent` image with tool availability controlled by environment variables. Simpler to maintain, and credential scoping handles the security boundary.
