# Task 004: Executor Agent — Full Loop

## Goal

Implement the executor agent and close the controller loop. The executor is a Strands agent that clones a repo, does work based on a task description, commits results to the branch, and pushes. The controller handler reads the result via GitHub API and updates CRD status.

## Contracts

### Result Model (`src/nubi/agents/result.py`)

- `ExecutorResult` Pydantic model with fields: `status` (Literal["success", "failure"]), `commit_sha` (str), `summary` (str), `files_changed` (list[str]), `error` (str)
- `RESULT_FILE_PATH = ".nubi/result.json"` constant
- `write_result(result: ExecutorResult, workspace: str) -> None` helper that writes JSON to `{workspace}/.nubi/result.json`

### Tools (`src/nubi/tools/`)

**shell.py:**
- `run_shell(command: str, timeout: int = 60) -> str` — @tool decorated
- Runs `subprocess.run(command, shell=True, cwd=workspace, timeout=timeout, capture_output=True)`
- Truncates combined stdout+stderr to last 200 lines
- Returns output as plain string (Strands auto-wraps the return value)
- Workspace path injected via module-level `_workspace` variable set by `configure(workspace)`

**git.py:**
- `git_clone(repo: str, branch: str, token: str, workspace: str) -> None` — plain function (NOT @tool). Clones repo, creates branch if it doesn't exist.
- `git_diff() -> str` — @tool, runs `git diff` + `git diff --cached`
- `git_log(max_count: int = 10) -> str` — @tool, runs `git log --oneline -n {max_count}`
- `git_commit(message: str) -> str` — @tool, stages all changes, commits
- `git_push() -> str` — @tool, pushes to origin
- `git_status() -> str` — @tool, runs `git status`
- All @tool functions operate on workspace path set via `configure(workspace)`

**files.py:**
- `file_read(path: str) -> str` — @tool, reads file relative to workspace
- `file_write(path: str, content: str) -> str` — @tool, writes file, creates parent dirs
- `file_list(path: str = ".") -> str` — @tool, lists directory contents
- All paths validated: resolved path must be within workspace root. Reject `..` traversal and absolute paths.

**`__init__.py`:**
- `get_tools(allowed: list[str], workspace: str) -> list[Callable]` — returns filtered tool functions
- Tool groups: `"shell"` → [run_shell], `"git"` → [git_diff, git_log, git_commit, git_push, git_status], `"file_read"` → [file_read], `"file_write"` → [file_write], `"file_list"` → [file_list]

### Agent (`src/nubi/agents/executor.py`)

- `create_model(provider: str, api_key: str) -> Model` — factory for Strands model providers. Supports "anthropic", "bedrock", "openai". Raises `ValueError` for unknown provider.
- `create_executor_agent(tools: list, description: str, repo: str, branch: str, provider: str = "anthropic", api_key: str = "") -> Agent` — creates and returns a configured Strands Agent
- `EXECUTOR_SYSTEM_PROMPT` — constant string template with `{description}`, `{repo}`, `{branch}` placeholders

### Entrypoint (`src/nubi/entrypoint.py`)

- `main() -> int` — container entrypoint function
  1. Reads env vars: NUBI_TASK_ID, NUBI_REPO, NUBI_BRANCH, NUBI_DESCRIPTION, NUBI_TOOLS, NUBI_LLM_PROVIDER, GITHUB_TOKEN, LLM_API_KEY
  2. Calls `git_clone(repo, branch, token, "/workspace")`
  3. Calls `get_tools(allowed_tools, "/workspace")`
  4. Creates executor agent via `create_executor_agent(...)`
  5. Invokes agent with task description
  6. Collects results (HEAD SHA, changed files)
  7. Writes `ExecutorResult` to `.nubi/result.json`
  8. Commits and pushes the result file
  9. Returns 0 on success, 1 on failure
- Handles errors gracefully: writes failure result before exiting

### Controller: Result Reader (`src/nubi/controller/results.py`)

- `async def read_executor_result(repo: str, branch: str, token: str) -> ExecutorResult`
- Makes GET request to `https://api.github.com/repos/{repo}/contents/.nubi/result.json?ref={branch}`
- Parses base64-encoded content from GitHub API response
- Returns `ExecutorResult` instance
- Uses `aiohttp` for async HTTP
- Raises `ResultError` (new exception in `exceptions.py`) on failure

### Controller: Handler Update (`src/nubi/controller/handlers.py`)

- `on_job_status_change` handler implemented:
  1. Extracts task-id and stage from Job labels
  2. Determines success/failure from Job status conditions
  3. For executor stage: reads GitHub token from master secret, calls `read_executor_result()`
  4. Updates CRD status: `stages.executor.status`, `stages.executor.commitSHA`, `stages.executor.summary`
  5. Sets phase to `Done` on success, `Failed` on failure (v0.1 — no gates/reviewers yet)

### Sandbox Update (`src/nubi/controller/sandbox.py`)

- Add `NUBI_LLM_PROVIDER` env var to Job spec (plain env var, default "anthropic")

### Dependencies (`pyproject.toml`)

- Add `strands-agents` to dependencies

### Exceptions (`src/nubi/exceptions.py`)

- Add `ResultError(NubiError)` for result reading failures

## Acceptance Criteria

1. `ExecutorResult` model serializes/deserializes correctly
2. All tools return proper Strands tool result format
3. File tools reject path traversal attempts (absolute paths, `..` sequences)
4. Shell tool truncates output to 200 lines max
5. `git_clone` creates branch if it doesn't exist
6. `get_tools` correctly filters tools by allowed list
7. `create_model` creates correct provider for each supported provider string
8. `create_executor_agent` returns a configured Agent with system prompt containing task details
9. Entrypoint runs full flow: clone → agent → result → commit → push
10. `read_executor_result` parses GitHub API response correctly
11. `on_job_status_change` updates CRD status correctly on success and failure
12. All existing tests still pass
13. `ruff check`, `ruff format --check`, `mypy` all pass
