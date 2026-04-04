# Nubi — Agent Instructions

## Development Workflow (Forge)

This project uses the Forge multi-agent workflow. ALL code changes follow this process:

1. **Plan** — Discuss the task in plan mode. Do NOT write code during planning.
2. **Spec** — Write a task spec in `tasks/` with goal, contracts, and acceptance criteria.
3. **Test first** — Invoke @tester with the task spec. Tests are written BEFORE implementation.
4. **Implement** — Invoke @worker with the task spec. Worker makes the tests pass.
5. **Review** — Invoke @reviewer to check spec compliance and quality.

**IMPORTANT**: Never skip straight to implementation. Every change starts with a task spec in `tasks/`. If the user asks to build something, first write the spec, then orchestrate the subagents.

---

## Build, Test, Run

```bash
# Setup
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Type check
mypy src/nubi/

# Lint
ruff check src/ tests/
ruff format --check src/ tests/

# Run controller locally (outside cluster, for dev)
kopf run src/nubi/controller/handlers.py --verbose

# Build agent image
docker build -f images/agent/Dockerfile -t ghcr.io/kuuji/nubi-agent:latest .

# Build controller image
docker build -f images/controller/Dockerfile -t ghcr.io/kuuji/nubi-controller:latest .

# Apply CRD to cluster
kubectl apply -f manifests/crd.yaml
```

## Verification Loop

Before considering work done, every agent must:

1. `ruff check src/ tests/` — no lint errors
2. `ruff format --check src/ tests/` — formatting passes
3. `mypy src/nubi/` — no type errors
4. `pytest tests/ -v` — all tests pass
5. No secrets, credentials, or API keys in committed code

## Conventions

- **Language:** Python 3.12+
- **Package manager:** pip with pyproject.toml (no setup.py, no requirements.txt)
- **Type hints:** Required on all public functions and methods
- **Testing:** pytest. Tests live in `tests/` mirroring `src/nubi/` structure
- **Linting:** ruff (linting + formatting)
- **Type checking:** mypy with strict mode
- **Async:** Use async/await throughout — kopf handlers are async, Strands supports async
- **Imports:** Absolute imports from `nubi.*` (e.g., `from nubi.controller.namespace import create_task_namespace`)
- **Error handling:** Use specific exception types, not bare `except`. Define custom exceptions in `nubi/exceptions.py`
- **Kubernetes types:** Use `kubernetes-asyncio` for typed K8s API access
- **Pydantic:** Use Pydantic v2 for CRD schema validation and all structured data

## Commit Conventions

- Format: `<type>: <short description>`
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`
- Examples:
  - `feat: add kopf handler for TaskSpec creation`
  - `fix: handle missing namespace on Job completion`
  - `test: add integration tests for gate runner`

## Git Workflow

- Each task gets a feature branch: `feat/<task-short-name>`
- Planner creates the branch before spawning Tester
- Worker commits on the feature branch
- After approval, squash-merge to main
- Delete the feature branch after merge

## Project-Specific Rules

- **Git is the workspace.** No PVCs, no shared volumes. Agents communicate via git branches and CRD status. This principle applies to the project's own design — respect it.
- **Deterministic where possible.** If something can be a code check (lint, test, scan), don't make it an agent call.
- **Single agent image.** One container image (`nubi-agent`) with tool availability controlled by env vars. Don't create per-agent images.
- **kopf for the controller.** Don't introduce other operator frameworks. kopf handlers are the orchestration layer.
- **Strands for agents.** Agent definitions use the Strands Agents SDK. Tools are Python functions decorated with `@tool`.
- **Event-driven, not polling.** The controller watches Jobs via kopf field handlers. No polling loops, no timers.
