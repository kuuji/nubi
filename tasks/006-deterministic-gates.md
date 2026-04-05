# Task 006: Deterministic Gates

## Goal

Implement deterministic gates as an executor loop phase. After each work cycle, the executor calls `discover_gates` (what gates apply) → `run_gates` (run them sequentially) → if failed: fix and retry. Gates run inside the executor pod, gated by the TaskSpec's `gate_policy`. Results are written to `.nubi/gates.json` and the controller verifies gates passed before allowing the stage to complete.

## Design Decisions

### Gate Flow

```
Executor does work
  → discover_gates (based on changed files, repo structure, gate_policy)
  → run_gates (sequential, stop-on-failure)
  → if any gate FAILED: incorporate feedback, do more work, loop
  → if all gates PASSED or SKIPPED: commit, push, done
```

`discover_gates` is called at the start of each gate-checking iteration. `run_gates` takes the discovered list and executes each one.

### Gate Categories

| Category | Fail behavior | Default tool | Deferred |
|---|---|---|---|
| `complexity` | FAIL | radon (Python) | No |
| `lint` | FAIL | ruff (Python), eslint (Node) | No |
| `test` | FAIL | pytest (Python), jest (Node) | No |
| `secret_scan` | — | — | Yes |
| `diff_size` | WARN | wc -l on changed files | No |

### Skipped vs Failed

If a tool is not installed in the repo, the gate is **SKIPPED** (not failed). This is deterministic: same repo → same tool present → same result.

### Gate Policy Steering

The `gate_policy` in TaskSpecSpec constrains what `discover_gates` can output:

- `allow`: list of gate categories that may be discovered (default: all known)
- `block`: list of categories to never run even if discovered
- `thresholds`: per-category limits (e.g., `max_cc` for complexity)

---

## Contracts

### 1. Gate Result Model — `src/nubi/agents/gate_result.py`

New file.

```python
GATES_FILE_PATH = ".nubi/gates.json"

class GateCategory(str, Enum):
    COMPLEXITY = "complexity"
    LINT = "lint"
    TEST = "test"
    SECRET_SCAN = "secret_scan"
    DIFF_SIZE = "diff_size"


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class GateResult(BaseModel):
    name: str                           # "ruff", "pytest", "radon"
    category: GateCategory
    status: GateStatus
    output: str = ""                    # tool stdout+stderr (truncated to 5000 chars)
    command: str = ""                   # what was executed
    duration_seconds: float = 0.0
    error: str = ""                     # if tool was missing or crashed


class GateDiscovery(BaseModel):
    name: str
    category: GateCategory
    applies_to: list[str] = Field(default_factory=list)  # glob patterns, e.g. ["*.py"]
    command: str = ""


class GatesResult(BaseModel):
    discovered: list[GateDiscovery]
    gates: list[GateResult]
    overall_passed: bool
    attempt: int = 1
```

Helper: `write_gates_result(result: GatesResult, workspace: str) -> None`
- Writes to `{workspace}/.nubi/gates.json`
- Creates `.nubi/` directory if needed
- Overwrites on each gate cycle

### 2. Gate Discovery Tool — `src/nubi/tools/gates.py`

New file.

```python
# Gate tool registry: maps category → tool discovery function
GATE_DISCOVERY_REGISTRY: dict[GateCategory, Callable[[list[str], str], GateDiscovery | None]]

# Per-language tool mappings
PYTHON_TOOLS = {
    "lint": ["ruff", "ruff check"],
    "test": ["pytest"],
    "complexity": ["radon --max-cc -j"],
}
NODE_TOOLS = {
    "lint": ["eslint"],
    "test": ["jest"],
}
TERRAFORM_TOOLS = {
    "lint": ["terraform validate"],
}
```

#### `discover_gates(workspace: str, gate_policy: GatePolicy, changed_files: list[str]) -> list[GateDiscovery]`

- Runs `git diff --name-only` against `origin/{base_branch}` to get changed files
- Scans workspace for project files (`pyproject.toml`, `package.json`, `*.tf`) to determine language stack
- Reads `.nubi/gates.yaml` if present (custom gate definitions)
- For each gate category in `GATE_DISCOVERY_REGISTRY`:
  - If category not in `gate_policy.allow` → skip
  - If category in `gate_policy.block` → skip
  - Calls registry function with changed_files and workspace
  - If function returns a discovery, adds to list
- Returns list of `GateDiscovery` objects

**Tool discovery functions** (one per language stack):

- `_discover_python_gates(changed: list[str], workspace: str) -> list[GateDiscovery]`
  - Checks for `pyproject.toml` → Python project
  - Checks for `ruff`, `pytest`, `radon` in PATH via `which`
  - Returns discovery for each tool found

- `_discover_node_gates(changed: list[str], workspace: str) -> list[GateDiscovery]`
  - Checks for `package.json` → Node project
  - Checks for `eslint`, `jest` in PATH
  - Returns discovery for each tool found

- `_discover_diff_size_gate(changed: list[str], workspace: str) -> GateDiscovery`
  - Always applicable if any files changed
  - Returns `GateDiscovery(name="diff_size", category=DIFF_SIZE, applies_to=["*"])`

**Complexity threshold** defaults from `gate_policy.thresholds`:
- `max_cc`: 10 (cyclomatic complexity)
- `max_cognitive`: 15

#### Gate Policy Schema (added to CRD)

```python
class GateThreshold(BaseModel):
    max_cc: int = 10
    max_cognitive: int = 15
    diff_lines_max: int = 500


class GatePolicy(BaseModel):
    allow: list[GateCategory] = Field(default_factory=list)  # empty = all allowed
    block: list[GateCategory] = Field(default_factory=list)
    thresholds: GateThreshold = Field(default_factory=GateThreshold)
    gate_timeout: int = 300  # seconds per gate
```

### 3. Gate Execution Tool — `src/nubi/tools/gates.py`

#### `run_gates(discovered: list[GateDiscovery], workspace: str, gate_policy: GatePolicy) -> GatesResult`

Sequential gate execution:

```python
def run_gates(discovered: list[GateDiscovery], workspace: str, gate_policy: GatePolicy) -> GatesResult:
    results: list[GateResult] = []
    for disc in discovered:
        gate_result = _run_single_gate(disc, workspace, gate_policy.gate_timeout)
        results.append(gate_result)
        if gate_result.status == GateStatus.FAILED:
            break  # stop-on-failure

    all_passed = all(r.status in (GateStatus.PASSED, GateStatus.SKIPPED) for r in results)
    return GatesResult(discovered=discovered, gates=results, overall_passed=all_passed)
```

#### `_run_single_gate(discovery: GateDiscovery, workspace: str, timeout: int) -> GateResult`

For each gate category:

**complexity → radon:**
```bash
radon --max-cc {thresholds.max_cc} -j {workspace}
```
Parses JSON output. Any function exceeding `max_cc` → FAILED.

**lint → ruff:**
```bash
ruff check {workspace} --output-format=concise
```
Non-zero exit → FAILED. Output includes the violations.

**lint → ruff (fallback if not found):**
```bash
ruff check {workspace} 2>&1 || true
```
If ruff not found → SKIPPED.

**test → pytest:**
```bash
pytest {workspace} -v --tb=short 2>&1
```
Non-zero exit → FAILED.

**diff_size:**
```bash
git diff --stat origin/{branch}..HEAD | tail -1
```
Parses total lines changed. Exceeds `thresholds.diff_lines_max` → FAILED (WARN level, doesn't block).

**Tool not found → SKIPPED:**
```python
result = subprocess.run(["which", tool_name], capture_output=True)
if result.returncode != 0:
    return GateResult(name=tool_name, category=category, status=SKIPPED,
                      output=f"{tool_name} not found in PATH", command=cmd)
```

**Timeout → FAILED:**
```python
subprocess.run(cmd, timeout=timeout, ...)
```
Catches `subprocess.TimeoutExpired`.

### 4. Gate Tools Registered — `src/nubi/tools/__init__.py`

Add `"gate": [discover_gates, run_gates]` to `TOOL_GROUPS`.

### 5. GateAwareExecutor Loop — `src/nubi/entrypoint.py`

Update `main()` to gate-aware loop:

```python
def _run_gates_loop(agent: Agent, workspace: str, description: str) -> GatesResult | None:
    """Run executor + gates in a loop until gates pass or max_attempts/total_timeout hit."""
    max_attempts = int(os.environ.get("NUBI_MAX_ATTEMPTS", "3"))
    attempt = 1

    while attempt <= max_attempts:
        # Do work
        agent(f"Complete this task:\n\n{description}")

        # Discover gates
        discovered = discover_gates(workspace, GatePolicy(), changed_files)

        # Run gates
        result = run_gates(discovered, workspace, GatePolicy())

        if result.overall_passed:
            return result

        # Gates failed — incorporate feedback, retry
        attempt += 1

    return None  # gates never passed
```

**Env vars added:**
- `NUBI_MAX_ATTEMPTS` — from `gate_policy.max_attempts` (default 3)
- `NUBI_GATE_TIMEOUT` — per-gate timeout (default 300s)

**Entrypoint flow updated:**
1. Clone repo, create task branch
2. Get tools (includes discover_gates, run_gates)
3. Run gates-aware loop (above)
4. Write `GatesResult` to `.nubi/gates.json`
5. Commit and push
6. Return success/failure based on gates result

**Important:** `GatesResult` is written on each gate cycle. Last write wins — controller reads the final state.

### 6. Executor System Prompt — `src/nubi/agents/executor.py`

Update `EXECUTOR_SYSTEM_PROMPT` to include gate awareness:

```python
EXECUTOR_SYSTEM_PROMPT = """\
...

## Gates
After each work cycle, you MUST call discover_gates and run_gates to verify your work.

Gate categories:
- complexity: cyclomatic complexity per function (max {max_cc})
- lint: code style and correctness (ruff, eslint)
- test: test suite pass/fail (pytest, jest)

If any gate FAILS:
1. Read the gate output to understand what failed
2. Fix the issues
3. Call discover_gates and run_gates again
4. Repeat until all gates pass or you run out of attempts

You have {max_attempts} gate attempts maximum. Use them wisely.

## Quality Standards
- Keep cyclomatic complexity under {max_cc} per function
- No lint errors
- Tests must pass
- Do not introduce security vulnerabilities or hardcoded secrets
"""
```

### 7. CRD Schema Updates — `src/nubi/crd/schema.py`

Add `GateCategory`, `GateStatus`, `GatePolicy`, `GateThreshold`, `GateResult`, `GatingStageStatus`.

Add `gate_policy: GatePolicy` field to `TaskSpecSpec`.

Add `gating: GatingStageStatus` field to `StageStatuses`.

### 8. Controller Update — `src/nubi/controller/handlers.py`

Update `on_job_status_change` for executor stage:

After reading `ExecutorResult`, also read `.nubi/gates.json`:

```python
from nubi.agents.gate_result import GatesResult, GATES_FILE_PATH

# After reading executor result...
# Read gates
try:
    gates_result = await read_gates_result(repo, task_branch, token)
except:
    # If gates.json doesn't exist, gates were never run — fail
    patch.status["phase"] = Phase.FAILED
    return

if not gates_result.overall_passed:
    # Check attempt count vs max_attempts
    max_retries = spec.loop_policy.max_retries
    if gates_result.attempt >= max_retries:
        patch.status["phase"] = Phase.ESCALATED
    else:
        # Retry: spawn executor job again
        patch.status["stages"]["executor"]["attempts"] += 1
        # Create new executor job...
    return

# All gates passed — advance to next phase
patch.status["phase"] = Phase.DONE
```

### 9. Gates Result Reader — `src/nubi/controller/results.py`

Add `async def read_gates_result(repo: str, branch: str, token: str) -> GatesResult`.

Same pattern as `read_executor_result`: GET `https://api.github.com/repos/{repo}/contents/{GATES_FILE_PATH}?ref={branch}`.

### 10. Dependencies — `pyproject.toml`

Add `radon` to dependencies (for cyclomatic complexity analysis).

---

## Acceptance Criteria

1. `discover_gates` returns applicable gates based on changed files and repo structure
2. `discover_gates` respects `gate_policy.allow` and `gate_policy.block`
3. `discover_gates` skips gates for tools not installed (SKIPPED, not FAILED)
4. `run_gates` executes gates sequentially with stop-on-failure
5. `run_gates` returns structured `GatesResult` with per-gate status, output, command, duration
6. Gate timeouts are enforced — slow gates don't hang the executor forever
7. `GatesResult` is written to `.nubi/gates.json` on each gate cycle
8. Controller reads `.nubi/gates.json` and enforces gate pass before DONE
9. Gate failures trigger executor retry up to `max_attempts`
10. `max_attempts` exceeded → phase set to ESCALATED (not FAILED)
11. Executor system prompt includes gate awareness and thresholds
12. `discover_gates` and `run_gates` are registered as tools available via `NUBI_TOOLS`
13. All verification passes: `ruff check`, `ruff format --check`, `mypy`, `pytest`
