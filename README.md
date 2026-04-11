# Nubi

A Kubernetes-native controller that orchestrates AI agent workflows. Describe what you want to an AI assistant, and Nubi turns it into a sandboxed pipeline of code generation, deterministic validation, agentic review, and PR creation — all inside your cluster.

### Why Kubernetes?

Kubernetes already solves sandboxing (gVisor RuntimeClass), resource limits (ResourceQuota), network isolation (NetworkPolicy), scheduling (Jobs), and cleanup (namespace GC). Instead of reinventing these primitives, Nubi builds on a battle-tested platform — the controller is just a kopf operator, and every agent run is a standard K8s Job.

### Why MCP?

The pipeline expects a structured `TaskSpec` CRD as input, but humans don't think in YAML. The MCP server bridges that gap — you describe what you want in conversation, and the MCP server creates the CRD for you. It runs inside the cluster with permissions scoped to creating TaskSpecs only, but any MCP-compatible client (Claude Code, Claude Desktop, or anything else) can connect to it.

## How It Works

You describe a task to your AI assistant (Claude Code, Claude Desktop, or any MCP client). The MCP server translates your request into a `TaskSpec` CRD and applies it. From there, the controller runs the full pipeline autonomously:

<p align="center">
  <img src="docs/pipeline.svg" alt="Nubi pipeline diagram" width="700" />
</p>

Each agent runs as a Kubernetes Job in a gVisor-sandboxed pod with scoped credentials, restricted networking, and resource limits. Git branches are the shared workspace — no PVCs, no shared volumes, no pod-to-pod communication.

You can also apply TaskSpecs directly with `kubectl` or through GitOps (ArgoCD).

## Features

- **Conversational input via MCP** — describe tasks to Claude Code, Claude Desktop, or any MCP-compatible assistant; the MCP server creates the TaskSpec for you
- **Sandboxed execution** — gVisor runtime, restricted Pod Security Standards, deny-all NetworkPolicy, no K8s API access from agent pods
- **Deterministic gates** — lint, test, complexity checks run as code, not LLM calls
- **Agentic review** — a separate read-only agent evaluates the executor's work
- **Bounded retry loops** — gate failures and review feedback loop back to the executor, with configurable limits and escalation
- **Git-native workspace** — each task gets a branch; artifacts live in `.nubi/{task-id}/`; the audit trail is the commit history
- **Model-agnostic** — works with any OpenAI-compatible API (OpenRouter, Anthropic, local models via ollama)
- **Also works with kubectl and GitOps** — apply TaskSpec YAMLs directly, or commit them to a repo and let ArgoCD handle it

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

## Status

Nubi is functional but early. Here's where things stand:

### What works

- **Full pipeline** — executor → gates → reviewer → monitor → PR creation has been run end-to-end against real repos with real LLMs (tested with Kimi K2 via OpenRouter)
- **Controller state machine** — all phase transitions, retry loops, and escalation paths are implemented and tested
- **Deterministic gates** — lint (ruff/eslint), complexity (radon), test execution (pytest/jest), and diff size checks, with auto-discovery based on changed file types
- **Reviewer and monitor agents** — reviewer feedback loops back to executor, monitor creates PRs and polls CI checks, CI failure kicks back to executor
- **gVisor sandboxing** — RuntimeClass, restricted PSS, shell allowlist, NetworkPolicy, no K8s API access from agent pods
- **MCP server** — FastMCP with streamable HTTP, 5 tools (create task, list tasks, get status, get logs, get results)
- **Integration tests** — 8 scenarios running against real k3d clusters in CI
- **420 unit tests** passing (71% line coverage), mypy strict, ruff clean

### What hasn't been tested extensively

- **Multi-tenant use** — the controller works with multiple concurrent TaskSpecs, but it hasn't been stress-tested at scale
- **Non-Python projects** — gates auto-discover eslint/jest for Node projects, but most testing has been done with Python repos
- **Long-running tasks** — timeout enforcement works, but edge cases around very large repos or complex multi-file changes haven't been explored
- **Production deployment** — Kustomize manifests exist and work, but this hasn't been run in a production cluster yet

### What's next

- Better CI feedback — pass actual check run output to executor on retry, don't retry on timeouts
- Langfuse integration for tracing and cost tracking
- Planner as an MCP skill — interactive task scoping through conversation before submitting
- See [TODO.md](TODO.md) for the full backlog

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
