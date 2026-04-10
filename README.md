# Nubi

A Kubernetes-native controller that orchestrates AI agent workflows. Define a task as a CRD, and Nubi runs a sandboxed pipeline of code generation, deterministic validation, agentic review, and PR creation — all inside your cluster.

## How It Works

You apply a `TaskSpec` custom resource. The controller takes it from there:

```
                         ┌─────────────────────────────────┐
                         │     kubectl apply taskspec.yaml  │
                         └────────────────┬────────────────┘
                                          │
                                          ▼
                         ┌─────────────────────────────────┐
                         │          Controller              │
                         │  (kopf operator in nubi-system)  │
                         │                                  │
                         │  Creates:                        │
                         │  - Task namespace                │
                         │  - NetworkPolicy (deny-all +     │
                         │    scoped egress)                │
                         │  - ResourceQuota                 │
                         │  - Scoped credentials Secret     │
                         └────────────────┬────────────────┘
                                          │
                    ┌─────────────────────┐│┌─────────────────────┐
                    │   gVisor Sandbox    │││   Git Branch        │
                    │   (RuntimeClass)    │││   nubi/{task-id}    │
                    └─────────────────────┘│└─────────────────────┘
                                          │
              ┌───────────────────────────┐│
              │                           ▼│
              │            ┌──────────────────────────┐
              │            │    1. Executor Agent      │
              │            │    (Strands SDK)          │
              │            │                           │
              │            │  - Clones branch          │
              │            │  - Writes code + tests    │
              │            │  - Pushes to git          │
              │            └────────────┬─────────────┘
              │                         │
              │                         ▼
              │            ┌──────────────────────────┐
              │  ┌─fail──  │    2. Deterministic Gates │
              │  │         │    (not an LLM call)      │
              │  │         │                           │
              │  │         │  - Lint (ruff/eslint)     │
              │  │         │  - Complexity (radon)     │
              │  │         │  - Tests (pytest/jest)    │
              │  │         │  - Diff size check        │
              │  │         └────────────┬─────────────┘
              │  │                      │ pass
              │  │  retry w/ feedback   │
              │  └──────────────────────│─────────────────┐
              │                         ▼                  │
              │            ┌──────────────────────────┐    │
              │            │    3. Reviewer Agent      │    │
              │            │    (read-only)            │    │
              │  ┌─changes─│                           │    │
              │  │         │  - Code quality           │    │
              │  │         │  - Architecture fit       │    │
              │  │         │  - Security review        │    │
              │  │         │  - Test coverage          │    │
              │  │         └────────────┬─────────────┘    │
              │  │                      │ approve          │
              │  │  retry w/ feedback   │                  │
              │  └──────────────────────│                  │
              │                         ▼                  │
              │            ┌──────────────────────────┐    │
              │            │    4. Monitor Agent       │    │
              │            │                           │    │
              │            │  - Audits full pipeline   │    │
              │            │  - Writes PR summary      │    │
              │            │  - Creates GitHub PR      │    │
              │            └────────────┬─────────────┘    │
              │                         │                  │
              │                         ▼                  │
              │            ┌──────────────────────────┐    │
              │            │         Done              │    │
              │            │   PR on target repo       │    │
              │            └──────────────────────────┘    │
              │                                            │
              │  All retry loops are bounded by             │
              │  spec.loop_policy.max_retries               │
              │  Exceeding limit → escalate to human        │
              └────────────────────────────────────────────┘
```

Each agent runs as a Kubernetes Job in a gVisor-sandboxed pod with scoped credentials, restricted networking, and resource limits. Git branches are the shared workspace — no PVCs, no shared volumes, no pod-to-pod communication.

## Features

- **Declarative tasks** — define what you want as a `TaskSpec` CRD, apply with `kubectl`
- **Sandboxed execution** — gVisor runtime, restricted Pod Security Standards, deny-all NetworkPolicy, no K8s API access from agent pods
- **Deterministic gates** — lint, test, complexity checks run as code, not LLM calls
- **Agentic review** — a separate read-only agent evaluates the executor's work
- **Bounded retry loops** — gate failures and review feedback loop back to the executor, with configurable limits and escalation
- **Git-native workspace** — each task gets a branch; artifacts live in `.nubi/{task-id}/`; the audit trail is the commit history
- **Model-agnostic** — works with any OpenAI-compatible API (OpenRouter, Anthropic, local models via ollama)
- **MCP server** — expose Nubi as tools for Claude Code, Claude Desktop, or any MCP client
- **GitOps-ready** — commit TaskSpec YAMLs to a repo, let ArgoCD apply them

## Quick Start

### Prerequisites

- Kubernetes cluster (k3d works well for local dev)
- [gVisor](https://gvisor.dev/) installed as a RuntimeClass (optional — can be disabled for dev)
- A GitHub token and an LLM API key

### 1. Apply the CRD and deploy the controller

```bash
# Apply CRD
kubectl apply -f manifests/crd.yaml

# Deploy controller with Kustomize
kubectl apply -k manifests/
```

### 2. Create credentials

```bash
kubectl create secret generic nubi-credentials \
  -n nubi-system \
  --from-literal=github-token="$GITHUB_TOKEN" \
  --from-literal=llm-api-key="$LLM_API_KEY"
```

### 3. Submit a task

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
    repo: your-org/your-repo
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
    on_max_retries: escalate
  output:
    format: pr
    pr:
      title_prefix: "nubi:"
      labels: [nubi, automated]
      draft: true
```

```bash
kubectl apply -f task.yaml
kubectl get taskspecs -w   # Watch progress
```

## Local Development

```bash
# Set up
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Create a local cluster
make cluster-up

# Build and import images
make build

# Run controller locally (reads .env for credentials)
make dev

# Run tests
make test            # Unit tests
make lint            # ruff + mypy
make test-integration # Integration tests (requires k3d)
```

Copy `.env.example` to `.env` and fill in your credentials for local development.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design document covering:

- Design principles (deterministic where possible, git as workspace, bounded autonomy)
- Pipeline stages in detail (executor, gates, reviewer, monitor)
- Security model (gVisor, NetworkPolicy, credential scoping, trust boundaries)
- Kubernetes controller design (event-driven reconciliation, no polling)
- CRD schema reference

## Tech Stack

| Component | Technology |
|---|---|
| Controller | [kopf](https://kopf.readthedocs.io/) (Python K8s operator framework) |
| Agents | [Strands Agents SDK](https://strandsagents.com/) |
| Sandbox | [gVisor](https://gvisor.dev/) (syscall-level isolation) |
| CRD Schema | [Pydantic v2](https://docs.pydantic.dev/) |
| Language | Python 3.12+ |
| CI | GitHub Actions (lint, typecheck, unit test, integration test, image build) |

## Project Structure

```
src/nubi/
  controller/     # kopf handlers, namespace lifecycle, sandbox job builder, credentials
  agents/         # Strands agent factories (executor, reviewer, monitor) + result models
  crd/            # Pydantic v2 TaskSpec schema + defaults
  tools/          # Agent tools (shell, files, git, gates, review, GitHub API)
  mcp/            # MCP server exposing Nubi as tools
manifests/        # CRD + Kustomize base (controller, RBAC, RuntimeClass)
images/           # Dockerfiles (controller, agent, MCP server)
tests/            # Unit + integration tests (k3d)
examples/         # Sample TaskSpec YAMLs
```

## License

[Apache 2.0](LICENSE)
