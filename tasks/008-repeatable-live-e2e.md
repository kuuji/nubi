# Task 008: Make Live E2E Repeatable and Self-Cleaning

## Goal

Improve the existing live end-to-end harness so it still exercises the real GitHub + LLM path, but does so in a more repeatable, assertion-driven way and cleans up after itself by default.

## Why

The current `scripts/e2e.sh test` flow is useful for ad-hoc debugging, but it is not reliable enough as a repeatable live smoke test:

- It generates a unique task name but hardcodes `metadata.name: e2e-test`, so runs are not isolated.
- It mostly prints logs instead of asserting expected states.
- It does not verify the remote GitHub branch and file contents in a structured way.
- Cleanup is incomplete and too broad in places.

For this project, the live GitHub + LLM integration is the critical path, so the e2e harness should explicitly validate that path and remove its own artifacts afterward.

## Scope

Focus on `scripts/e2e.sh` and any tests needed to keep its behavior stable. Do not introduce a separate deterministic harness in this task.

## Contracts

### 1. Unique per-run task identity

- `scripts/e2e.sh`

The `test` command must create a unique task name and use it consistently for:
- TaskSpec metadata name
- expected task namespace
- expected task branch (`nubi/<task-name>`)
- expected remote file name/content marker

### 2. Assertion-driven live verification

- `scripts/e2e.sh`

The `test` command must fail with a non-zero exit code when any critical step fails.

It must assert at least:
- TaskSpec created successfully
- task namespace created
- executor Job reaches a terminal state within a timeout
- TaskSpec reaches a terminal phase
- successful run requires `status.phase == Done`
- `status.workspace.branch` matches the expected task branch
- `status.workspace.headSHA` is non-empty
- the expected remote branch exists on GitHub
- the expected remote file exists on that branch with the expected content

### 3. Artifact capture for debugging

- `scripts/e2e.sh`

The harness must collect useful artifacts for each run, such as:
- rendered TaskSpec YAML
- TaskSpec YAML/status output
- executor logs
- job/pod descriptions or summaries

Artifacts should be stored in a per-run directory and reported at the end of the run.

### 4. Cleanup by default

- `scripts/e2e.sh`

After the run, the harness must clean up its own artifacts in external systems by default:
- delete the TaskSpec
- delete the task namespace if it still exists
- delete the remote task branch on GitHub

Cleanup must be scoped to resources created by the run, not broad pattern deletion of unrelated `nubi-*` namespaces.

Optional keep/debug flags are allowed, but default behavior must clean up.

### 5. Safer, narrower clean command

- `scripts/e2e.sh`

The `clean` command must only remove e2e-created resources, not every `nubi-*` namespace.

### 6. Regression coverage

- tests under `tests/`

Add tests that cover the script’s critical repeatability behavior at the unit level, including:
- unique task naming reflected in rendered TaskSpec content
- cleanup scoping and remote branch cleanup intent
- failure when terminal conditions or expected verification state are missing

The tests can use mocked command execution; they do not need to run a real cluster or GitHub.

## Acceptance Criteria

1. `scripts/e2e.sh test` uses a unique per-run TaskSpec name instead of hardcoded `e2e-test`.
2. The harness verifies TaskSpec terminal status and remote branch/file results, not just logs.
3. The harness exits non-zero on verification failure.
4. The harness captures per-run artifacts and reports where they were written.
5. The harness deletes the TaskSpec, task namespace, and remote task branch by default.
6. `scripts/e2e.sh clean` only targets resources created by the live e2e flow.
7. Added/updated tests cover the new behavior.
8. Full verification passes: `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/nubi/`, `pytest tests/ -v`.
