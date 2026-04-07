# Integration Tests

Controller state machine tests using a real K8s cluster with mocked LLM and GitHub API.

## Test Layers

| Layer | What's real | What's mocked | Speed |
|-------|-----------|--------------|-------|
| Unit (`tests/`) | Nothing | Everything | <10s |
| **Integration** (`tests/integration/`) | **K8s cluster, CRD, kopf handlers, Jobs** | **LLM (fake agent), GitHub API (scenario store)** | **~60s** |
| E2E (`scripts/e2e.sh`) | Everything | Nothing | 15+ min |

## Prerequisites

- [k3d](https://k3d.io/) installed
- Docker running
- kubectl configured
- No API keys needed — LLM and GitHub are mocked

## Quick Start

```bash
# One-time setup (creates cluster, applies CRD, builds fake agent image)
./scripts/integration-setup.sh

# Run integration tests
pytest tests/integration/ -v

# Teardown when done (optional)
k3d cluster delete nubi-integration
```

## How It Works

### Architecture

```
pytest process
├── kopf.operator() running as asyncio background task
│   ├── on_taskspec_created → creates namespace + Job (real K8s)
│   ├── on_job_status_change → annotates TaskSpec (real K8s)
│   ├── on_executor_completion → reads results from ScenarioResultStore (mocked)
│   └── on_reviewer_completion → reads results from ScenarioResultStore (mocked)
│
├── ScenarioResultStore (replaces GitHub API)
│   └── Pre-canned ExecutorResult, GatesResult, ReviewResult per task
│
└── Test assertions
    └── Poll TaskSpec phase via K8s API until terminal state
```

### What's Mocked

1. **LLM calls** — The agent container is a minimal alpine image that sleeps 2 seconds and exits. No Python, no LLM SDK, no API calls.

2. **GitHub API** — The controller normally reads `.nubi/result.json`, `gates.json`, and `review.json` from GitHub. In integration tests, these functions are patched to read from a `ScenarioResultStore` that the test populates with pre-built results.

3. **GitHub token** — The `_read_github_token` helper is patched to return a dummy token.

### What's Real

- K8s cluster (k3d)
- CRD registration and validation
- Namespace creation with ResourceQuota and NetworkPolicy
- Scoped Secret creation (credential projection)
- Job creation, lifecycle, and completion detection
- kopf event handlers and annotation-based event relay
- TaskSpec status patching and phase transitions

## Scenarios

| # | Test | Expected Phase | What It Tests |
|---|------|---------------|---------------|
| 1 | `test_executor_gates_reviewer_approve` | Done | Happy path |
| 2 | `test_request_changes_then_approve` | Done | Full retry loop |
| 3 | `test_gate_fail_then_pass` | Done | Gate retry |
| 4 | `test_gate_fail_escalates` | Escalated | Max retries |
| 5 | `test_reject_escalates` | Escalated | Reviewer rejection |
| 6 | `test_job_failure` | Failed | Job error handling |
| 7 | `test_review_disabled_goes_to_done` | Done | Review-disabled path |

## Adding New Scenarios

### 1. Register canned results

```python
scenario_store.set_executor_result(task_name, ExecutorResult(status="success", ...))
scenario_store.set_gates_result(task_name, GatesResult(overall_passed=True, ...))
scenario_store.set_review_result(task_name, ReviewResult(decision=ReviewDecision.APPROVE, ...))
```

### 2. For multi-attempt scenarios

Use the `attempt` parameter:

```python
# Attempt 1: reviewer requests changes
scenario_store.set_review_result(task_name, request_changes_result, attempt=1)
# Attempt 2: reviewer approves
scenario_store.set_review_result(task_name, approve_result, attempt=2)
```

### 3. For Job failure scenarios

Don't register any results — the controller will get a `ResultError` when trying to read them, which triggers the Failed phase.

### 4. Create the TaskSpec and assert

```python
await create_taskspec(task_name, review_enabled=True)
phase = await await_phase(task_name, "Done", timeout=30)
assert phase == "Done"
```

## Troubleshooting

### Cluster not found
```
SKIPPED: k3d cluster 'nubi-integration' not found. Run: ./scripts/integration-setup.sh
```
Run the setup script first.

### Tests timing out
- Check k3d is running: `k3d cluster list`
- Check fake agent image is imported: `docker images | grep nubi-fake-agent`
- Check CRD is applied: `kubectl get crd taskspecs.nubi.io --context k3d-nubi-integration`

### Stale state from previous runs
```bash
# Delete all integration test TaskSpecs
kubectl delete taskspec -n nubi-system -l app.kubernetes.io/created-by=nubi-integration --context k3d-nubi-integration

# Delete leftover namespaces
kubectl get ns --context k3d-nubi-integration | grep nubi-integ | awk '{print $1}' | xargs kubectl delete ns --context k3d-nubi-integration
```

### Inspecting state during debugging
```bash
kubectl get taskspec -n nubi-system --context k3d-nubi-integration -o wide
kubectl get jobs -A --context k3d-nubi-integration
kubectl get pods -A --context k3d-nubi-integration
```
