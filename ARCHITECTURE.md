# Nubi — Architecture

**Nubi** (누비) — Korean quilting art. Layers stitched together into something stronger than the individual pieces.

An agentic harness for orchestrating AI agent workflows with structured task decomposition, sandboxed execution, deterministic + agentic validation, code review, and full observability.

---

## Overview

Nubi sits between human intent and agent execution. It takes a structured task spec, decomposes it, runs agents in isolated sandboxes, validates their output through layered gates (deterministic and agentic), reviews the work, and produces a human-readable summary of everything that happened.

```
Human → Orchestrator (produces spec)
  → Nubi Harness (creates task namespace)
    → Planner Agent: decomposes spec into sub-tasks
    → Loop per sub-task:
      → Executor Agent: does the work
      → Validator Agent: checks correctness (deterministic + agentic)
      → Reviewer Agent: checks quality (agentic)
        → If issues: loop back to Executor (bounded)
    → Monitorer Agent: observes full run, produces human summary
  → Harness cleans up
→ Results + summary returned to orchestrator
```

---

## Design Principles

1. **Deterministic where possible, agentic where necessary.** Linters, tests, and dry-runs don't need a model. Intent-matching and quality judgment do. Layer both.
2. **The spec is the contract.** Every layer reads from and validates against the same structured spec format. This makes the system composable and recursive.
3. **Isolation is non-negotiable.** Agent code runs in gVisor-sandboxed containers with restricted permissions. Blast radius is contained by namespace.
4. **Observability is built in, not bolted on.** Every agent action traces to Langfuse via OpenTelemetry. The monitorer turns traces into human narrative.
5. **Bounded autonomy.** Agents can loop (reviewer → executor), but within defined limits. Escalate to humans, don't spiral.

---

## Technology Choices

| Component | Technology | Rationale |
|---|---|---|
| Agent Framework | [Strands Agents SDK](https://strandsagents.com/) (Python) | Model-driven, minimal orchestration overhead. Tools are Python functions. Built-in OTEL support. |
| Observability | [Langfuse](https://langfuse.com/) (self-hosted) | Open source, OTEL-native, first-class Strands integration. Traces, costs, evals. Self-hosted on Postgres. |
| Sandbox Runtime | [gVisor](https://gvisor.dev/) (`runsc`) | Syscall-level isolation via user-space kernel. K8s-native RuntimeClass. No hypervisor overhead. |
| Execution Model | Kubernetes Jobs | Ephemeral, namespaced, auto-cleaned via TTL. Standard K8s primitives. |
| Namespace Strategy | Per-task namespaces | Organizational boundary for observability, cleanup, and resource isolation. |
| Container Orchestration | Kubernetes + ArgoCD | Existing cluster infrastructure. Declarative, GitOps-managed. |
| Language | Python | Strands SDK is Python-first. Kubernetes client library is mature. |

---

## Pipeline Stages

### Stage 1: Spec Production

The orchestrator (e.g., OpenClaw agent using a harness skill) translates human intent into a structured task spec. The spec defines what needs to be done, what constraints apply, and how to validate success.

The spec is the single source of truth for the entire pipeline. See [Task Spec Format](#task-spec-format) below.

### Stage 2: Planning

The **Planner Agent** receives the spec and decides how to approach it:

- **Simple tasks**: pass through directly to execution (no decomposition needed)
- **Complex tasks**: break into ordered sub-tasks, each with its own mini-spec
- **Dependency mapping**: which sub-tasks can run in parallel, which are sequential

The planner does NOT execute. It produces an execution plan — an ordered list of sub-specs.

**Tools available:** File read, git log/diff, web search (for research context).
**Tools NOT available:** File write, shell execution, git push. Read-only.

### Stage 3: Execution

The **Executor Agent** runs inside a gVisor-sandboxed pod in the task namespace. One executor per sub-task.

**Tools available (configurable per spec):**
- `shell`: Run commands in the sandbox
- `file_read` / `file_write`: Filesystem access within the workspace
- `git`: Clone, branch, commit, push
- `web_search`: Research (if network access is granted)
- `http_request`: API calls (scoped by NetworkPolicy to allowed hosts)

**What the executor produces:**
- Code changes (as a git branch/patch)
- Files (research output, configs, etc.)
- Structured result object (status, summary, files changed, decisions made)

### Stage 4: Validation

The **Validator** runs in two layers:

**Layer 1 — Deterministic (runs first, fast-fails):**
- Linting (ruff, eslint, etc.)
- Type checking (mypy, tsc)
- Test execution (pytest, jest)
- Dry-run commands (`kubectl apply --dry-run`, `terraform plan`)
- Diff size checks (reject >N lines changed)
- Secret scanning (trufflehog, gitleaks)
- Custom checks defined in the spec

**Layer 2 — Agentic (runs only if deterministic checks pass):**
- Intent matching: "Does this implementation actually solve what the spec asked for?"
- Completeness: "Are there edge cases or requirements the executor missed?"
- Regression risk: "Could this change break existing functionality?"

**Tools available:** File read, git diff, test output logs. Read-only access to executor's workspace.

**Output:** Pass/fail with detailed reasoning. On failure, produces actionable feedback for the executor.

### Stage 5: Review

The **Reviewer Agent** asks a different question than the validator: not "is this correct?" but "is this *good*?"

- Code quality: readability, duplication, naming, structure
- Architecture fit: does this align with the project's patterns?
- API contract impact: will this break consumers?
- Performance: obvious inefficiencies, N+1 queries, unnecessary allocations
- Maintainability: will someone understand this in 6 months?

**Tools available:** File read, git diff/log, project documentation. Read-only.

**Output:** Approved, approved-with-comments, or request-changes (with specific feedback).

### Stage 6: Loop Resolution

When validation or review fails:

```
Validator fails → Executor retries with feedback
Reviewer requests changes → Executor retries with feedback
Max retries exceeded → Escalate to human
```

Loop policy is defined in the spec:
- `max_retries`: How many executor retries before escalation (default: 2)
- `validator_to_executor`: Allow validator to trigger re-execution (default: true)
- `reviewer_to_executor`: Allow reviewer to trigger re-execution (default: true)
- `reviewer_to_planner`: Allow reviewer to trigger re-planning (default: false — too expensive)
- `on_max_retries`: `escalate` or `abandon`

### Stage 7: Monitoring

The **Monitorer Agent** observes the entire pipeline run and produces a human-readable summary:

- What was the task?
- How was it decomposed?
- What did each executor do? What decisions did it make?
- What did validation catch? What did review flag?
- How many loops occurred? What was fixed?
- What's the final state?
- Langfuse trace link for drill-down

**Inputs:** Langfuse traces, agent logs, spec, all stage outputs.
**Output:** Structured report (markdown) + optional Langfuse annotations.

The monitorer is the human's window into the system. Without it, understanding what happened requires reading raw traces.

---

## Task Spec Format

The spec is YAML-based. It's the contract between all pipeline stages.

```yaml
apiVersion: nubi/v1
kind: TaskSpec

metadata:
  id: <uuid>               # Auto-generated
  name: "human-readable"   # Short description
  created: <iso-timestamp>
  source: openclaw | cli | api

task:
  description: |
    Multi-line description of what needs to be done.
    Be specific about desired outcome.
  type: code-change | research | infra | investigation | refactor
  priority: low | normal | high | critical

inputs:
  repo: kuuji/some-repo          # GitHub repo (optional)
  branch: main                    # Base branch
  working_branch: nubi/<task-id>  # Auto-generated
  files_of_interest:              # Hint to planner/executor
    - path/to/relevant/file.py
    - docs/architecture.md
  context: |                      # Additional context
    Free-form text the orchestrator adds.
  artifacts:                      # Input files/data
    - name: requirements.txt
      content: <inline or path>

constraints:
  timeout: 300s                   # Per sub-task timeout
  total_timeout: 1800s            # Entire pipeline timeout
  max_tokens: 50000               # Token budget per agent invocation
  network_access:                 # Allowed egress (deny-all by default)
    - github.com
    - registry.k8s.io
    - pypi.org
  tools:                          # Tools available to executor
    - shell
    - git
    - file_read
    - file_write
    - web_search
  resources:                      # Per-pod resource limits
    cpu: "1"
    memory: 512Mi

validation:
  deterministic:                  # Fast-fail checks
    - lint
    - type_check
    - test
    - secret_scan
    - diff_size:
        max_lines: 500
    - custom:
        command: "make validate"
        expected_exit: 0
  agentic:                        # Model-based checks
    - intent_match
    - completeness
    - regression_risk

review:
  enabled: true
  focus:                          # What the reviewer should emphasize
    - code_quality
    - architecture_fit
    - api_contract

loop_policy:
  max_retries: 2
  validator_to_executor: true
  reviewer_to_executor: true
  reviewer_to_planner: false
  on_max_retries: escalate       # escalate | abandon

output:
  format: pr | patch | files | report
  destination: github | local     # Where results go
  pr:                             # If format is pr
    title_prefix: "nubi:"
    labels: [nubi, automated]
    draft: true

decomposition:
  allow: true                     # Planner can decompose
  max_depth: 2                    # Max recursion depth
  max_subtasks: 5                 # Prevent over-decomposition

monitoring:
  langfuse_project: nubi
  trace_tags:
    - <task-id>
    - <task-type>
  summary: true                   # Generate monitorer summary
  notify:                         # Where to send the summary
    - channel: discord
      target: <channel-id>
    - channel: telegram
      target: <chat-id>
```

### Spec Recursion

When the planner decomposes a task, it produces child specs that inherit from the parent:

- Child specs inherit `constraints`, `validation`, and `loop_policy` unless overridden
- Child specs get their own `metadata.id` and are linked to the parent via `metadata.parent_id`
- The harness enforces `decomposition.max_depth` to prevent infinite recursion

---

## Agent Definitions

Each agent is a Strands Agent with a specific system prompt and tool set.

### Planner

```
Role: Decompose tasks into actionable sub-tasks.
System prompt: Task decomposition specialist. Read the spec, analyze the
  codebase structure, and break the work into ordered, independent sub-tasks.
  Each sub-task should be completable by a single executor in one session.
Tools: file_read, git_log, git_diff, web_search
Isolation: Standard container (no gVisor needed — read-only operations)
```

### Executor

```
Role: Implement the task.
System prompt: Software engineer. Implement exactly what the spec describes.
  Follow existing patterns in the codebase. Write clean, tested code.
  Document your decisions.
Tools: shell, file_read, file_write, git, web_search, http_request (scoped)
Isolation: gVisor sandbox, restricted PSS, scoped NetworkPolicy
```

### Validator

```
Role: Verify correctness — deterministic and agentic.
System prompt: Quality assurance engineer. Run all deterministic checks first.
  If they pass, evaluate whether the implementation matches the spec's intent.
  Be thorough but fair. Provide actionable feedback on failures.
Tools: file_read, git_diff, shell (read-only commands: lint, test, etc.)
Isolation: gVisor sandbox, read-only filesystem access to executor workspace
```

### Reviewer

```
Role: Evaluate quality and fitness.
System prompt: Senior engineer doing code review. The code already passes
  tests and validation. Your job is to assess quality, architecture fit,
  maintainability, and potential issues. Approve, comment, or request changes.
Tools: file_read, git_diff, git_log, project_docs
Isolation: Standard container (read-only operations)
```

### Monitorer

```
Role: Observe and summarize the entire pipeline run.
System prompt: Technical writer and observer. You have access to everything
  that happened during this pipeline run. Produce a clear, concise summary
  for the human operator. Include what was done, what was caught and fixed,
  any concerns, and the final state.
Tools: langfuse_traces, file_read, stage_outputs
Isolation: Standard container (read-only)
```

---

## Infrastructure

### Cluster Requirements

- Kubernetes cluster with gVisor support (runsc installed on nodes)
- containerd configured with gVisor runtime
- NetworkPolicy support (Calico, Cilium, or similar CNI)
- Persistent storage for Langfuse (Postgres)

### Namespace Lifecycle

Each task gets its own namespace:

```
nubi-{task-id-short}
```

**Creation:**
1. Namespace with labels: `nubi.task-id`, `nubi.task-type`, `pod-security.kubernetes.io/enforce: restricted`
2. gVisor RuntimeClass reference
3. ResourceQuota (from spec constraints)
4. NetworkPolicy (deny-all + specific egress from spec)
5. ServiceAccount with minimal RBAC

**Cleanup:**
- On success: namespace deleted after configurable TTL (default: 1 hour, for debugging)
- On failure: namespace retained for investigation (configurable)
- Garbage collector: CronJob that cleans namespaces older than 24 hours

### Langfuse Deployment

Self-hosted Langfuse on the cluster:
- Postgres database (dedicated or shared)
- Langfuse web UI + API
- OTEL collector endpoint for Strands agents
- Accessible via `langfuse.lab.byeon.ca` (Traefik ingress)

### Container Images

Base images for agent pods:

```
ghcr.io/kuuji/nubi-executor:latest    # Python + Strands + build tools + git
ghcr.io/kuuji/nubi-planner:latest     # Python + Strands + read-only tools
ghcr.io/kuuji/nubi-validator:latest   # Python + Strands + linters + test runners
ghcr.io/kuuji/nubi-reviewer:latest    # Python + Strands + read-only tools
ghcr.io/kuuji/nubi-monitorer:latest   # Python + Strands + Langfuse client
```

Or: a single `ghcr.io/kuuji/nubi-agent:latest` image with tool availability controlled by the entrypoint/config. Simpler to maintain, slightly larger image.

---

## Repo Structure

```
kuuji/nubi/
├── ARCHITECTURE.md              # This file
├── README.md                    # Quick start, usage
├── pyproject.toml               # Python project config
├── src/
│   └── nubi/
│       ├── __init__.py
│       ├── harness/
│       │   ├── __init__.py
│       │   ├── runner.py        # Main harness orchestration loop
│       │   ├── namespace.py     # K8s namespace lifecycle
│       │   ├── sandbox.py       # gVisor job creation/management
│       │   └── loop.py          # Retry/escalation logic
│       ├── spec/
│       │   ├── __init__.py
│       │   ├── schema.py        # TaskSpec Pydantic models
│       │   ├── parser.py        # YAML parsing + validation
│       │   └── defaults.py      # Default values, inheritance
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── planner.py       # Planner agent definition
│       │   ├── executor.py      # Executor agent definition
│       │   ├── validator.py     # Validator agent definition
│       │   ├── reviewer.py      # Reviewer agent definition
│       │   └── monitorer.py     # Monitorer agent definition
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── shell.py         # Sandboxed shell execution
│       │   ├── git.py           # Git operations
│       │   ├── files.py         # File read/write
│       │   ├── web.py           # Web search, HTTP requests
│       │   └── langfuse.py      # Langfuse trace querying
│       ├── validation/
│       │   ├── __init__.py
│       │   ├── deterministic.py # Lint, test, secret scan, etc.
│       │   └── gates.py         # Gate definitions, registry
│       └── output/
│           ├── __init__.py
│           ├── pr.py            # GitHub PR creation
│           ├── report.py        # Markdown report generation
│           └── notify.py        # Discord/Telegram notification
├── agents/                      # Agent system prompts (markdown)
│   ├── planner.md
│   ├── executor.md
│   ├── validator.md
│   ├── reviewer.md
│   └── monitorer.md
├── specs/                       # Example/template specs
│   ├── code-change.yaml
│   ├── research.yaml
│   ├── infra.yaml
│   └── investigation.yaml
├── infra/                       # Kubernetes manifests
│   ├── gvisor-runtimeclass.yaml
│   ├── langfuse/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── postgres.yaml
│   │   └── ingress.yaml
│   └── rbac/
│       ├── harness-sa.yaml      # ServiceAccount for harness runner
│       └── sandbox-role.yaml    # Role for sandbox namespace management
├── images/                      # Dockerfiles
│   ├── agent/
│   │   └── Dockerfile           # Unified agent image
│   └── harness/
│       └── Dockerfile           # Harness runner image
├── tests/
│   ├── test_spec.py
│   ├── test_harness.py
│   └── test_agents.py
└── cli/                         # CLI for local runs
    └── __main__.py              # `python -m nubi run spec.yaml`
```

---

## Integration Points

### OpenClaw (Primary Orchestrator)

A **nubi skill** in OpenClaw translates human intent into task specs and submits them to the harness:

```
User: "Add rate limiting to the API endpoints in kuuji/some-app"
  → OpenClaw agent reads nubi skill
  → Produces TaskSpec YAML
  → Calls nubi harness (API, CLI, or direct Python invocation)
  → Harness runs the pipeline
  → Monitorer summary relayed to user
```

The skill can also be used by the Strands executor itself when it needs to decompose — same spec format, recursive invocation.

### CLI

For local/direct use without OpenClaw:

```bash
# Run a spec directly
python -m nubi run specs/my-task.yaml

# Watch a running task
python -m nubi status <task-id>

# View monitorer summary
python -m nubi summary <task-id>
```

### API (Future)

REST or gRPC API for programmatic access. Enables:
- CI/CD integration (trigger harness from GitHub Actions)
- Webhook receivers (trigger from external events)
- Status polling for UIs

---

## Security Model

### Sandbox Security (gVisor)

- **Syscall filtering**: gVisor intercepts all syscalls in user space. Agent code never directly touches the host kernel.
- **Restricted PSS**: No privilege escalation, no host mounts, read-only root filesystem, non-root user.
- **NetworkPolicy**: Deny-all default. Egress explicitly allowed per spec (e.g., `github.com`, `pypi.org`).
- **Resource limits**: CPU and memory caps per pod, enforced by ResourceQuota.
- **Timeout enforcement**: `activeDeadlineSeconds` on Jobs — hard kill if exceeded.

### Credential Management

- Agents receive credentials via Kubernetes Secrets mounted as environment variables
- Only the credentials needed for the task (e.g., GitHub token for code changes)
- No access to cluster-wide secrets or the harness's own credentials
- Future: network-layer credential injection (microsandbox pattern) when tooling matures

### Trust Boundaries

```
┌─────────────────────────────────────────────────────────┐
│ Harness Runner (trusted)                                │
│  - Creates namespaces, manages lifecycle                │
│  - Has cluster RBAC for namespace/job management        │
│  - Runs outside sandbox                                 │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │ Task Namespace (partially trusted)                │  │
│  │                                                   │  │
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │ Agent Pod (untrusted code execution)        │  │  │
│  │  │  - gVisor runtime                          │  │  │
│  │  │  - Restricted PSS                          │  │  │
│  │  │  - Scoped NetworkPolicy                    │  │  │
│  │  │  - Resource limits                         │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## Open Questions

1. **Model selection per agent**: Should the spec define which model each agent uses? Or should the harness pick based on task complexity? (e.g., planner/reviewer get Opus, executor gets Sonnet, monitorer gets Flash)
2. **State persistence across retries**: When the executor retries after reviewer feedback, does it start fresh or resume? Starting fresh is simpler but wasteful. Resuming requires state management.
3. **Parallel execution**: When the planner produces independent sub-tasks, should they run in parallel? Adds complexity to namespace management and resource consumption.
4. **Human-in-the-loop hooks**: Where exactly does the human intervene? After planning? After review? Configurable per spec?
5. **Cost budgets**: Should the spec define a max cost (in dollars) for the entire pipeline? Requires real-time token counting and price estimation.
6. **Harness deployment model**: Does the harness runner live in the Yura pod, in its own deployment, or as a CLI on the cluster? Separate deployment is cleaner but adds infrastructure.
7. **Single image vs per-agent images**: One `nubi-agent` image with entrypoint-based tool selection, or separate images per role? Single image is easier to maintain; separate images are more secure (smaller attack surface per role).
