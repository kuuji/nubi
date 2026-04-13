"""GitHub REST API tools for the monitor agent — no git clone needed."""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, cast

import httpx
from strands import tool

from nubi.agents.monitor_result import MonitorConcern, MonitorDecision, MonitorResult

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

_repo: str = ""
_base_branch: str = ""
_task_branch: str = ""
_token: str = ""
_audit_result: MonitorResult | None = None

SUMMARY_MARKER = "<!-- nubi-pipeline-summary -->"


def configure(
    repo: str,
    base_branch: str,
    task_branch: str,
    token: str,
) -> None:
    """Configure GitHub API tools with repo/branch/token context."""
    global _repo, _base_branch, _task_branch, _token
    _repo = repo
    _base_branch = base_branch
    _task_branch = task_branch
    _token = token


def get_audit_result() -> MonitorResult | None:
    """Return the audit result captured by submit_audit, or None if not called."""
    return _audit_result


def reset_audit_result() -> None:
    """Reset the captured audit result. Used in tests."""
    global _audit_result
    _audit_result = None


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token}",
        "Accept": "application/vnd.github.v3+json",
    }


@tool
def read_branch_file(path: str) -> str:
    """Read a file from the task branch via GitHub API.

    Args:
        path: File path relative to repo root (e.g. ".nubi/result.json").
    """
    url = f"{GITHUB_API_BASE}/repos/{_repo}/contents/{path}"
    resp = httpx.get(url, headers=_headers(), params={"ref": _task_branch}, timeout=30)
    if resp.status_code != 200:
        return f"Error: GitHub API returned {resp.status_code} for {path}: {resp.text}"
    data = resp.json()
    content_b64 = data.get("content", "")
    return base64.b64decode(content_b64).decode()


@tool
def read_diff() -> str:
    """Read the diff between the base branch and the task branch via GitHub API."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/compare/{_base_branch}...{_task_branch}"
    headers = {**_headers(), "Accept": "application/vnd.github.v3.diff"}
    resp = httpx.get(url, headers=headers, timeout=60)
    if resp.status_code != 200:
        return f"Error: GitHub API returned {resp.status_code}: {resp.text}"
    diff = resp.text
    if len(diff) > 100_000:
        diff = diff[:100_000] + "\n... (diff truncated at 100KB)"
    return diff


@tool
def list_branch_files(path: str = "") -> str:
    """List files in a directory on the task branch via GitHub API.

    Args:
        path: Directory path relative to repo root. Empty string for repo root.
    """
    url = f"{GITHUB_API_BASE}/repos/{_repo}/contents/{path}"
    resp = httpx.get(url, headers=_headers(), params={"ref": _task_branch}, timeout=30)
    if resp.status_code != 200:
        return f"Error: GitHub API returned {resp.status_code} for {path}: {resp.text}"
    data = resp.json()
    if isinstance(data, list):
        entries = [f"{'d' if item['type'] == 'dir' else 'f'} {item['name']}" for item in data]
        return "\n".join(entries)
    return f"f {data.get('name', path)}"


@tool
def create_pull_request(title: str, body: str, draft: bool = False) -> str:
    """Create or update a GitHub pull request from the task branch to the base branch.

    If a PR already exists for this branch, updates its title and body instead.

    Args:
        title: PR title.
        body: PR description body (markdown).
        draft: If True, create as a draft PR.
    """
    url = f"{GITHUB_API_BASE}/repos/{_repo}/pulls"
    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "head": _task_branch,
        "base": _base_branch,
        "draft": draft,
    }
    resp = httpx.post(url, headers=_headers(), json=payload, timeout=30)
    if resp.status_code == 201:
        pr_data = resp.json()
        return f"PR created: {pr_data['html_url']}"
    if resp.status_code == 422:
        existing = _find_existing_pr()
        if existing:
            _update_pr(existing["number"], title, body)
            return f"PR updated: {existing['html_url']}"
        return f"PR validation error: {resp.text}"
    return f"Error creating PR: {resp.status_code} {resp.text}"


def _find_existing_pr() -> dict[str, Any] | None:
    """Find an existing open PR from the task branch."""
    owner = _repo.split("/")[0]
    url = f"{GITHUB_API_BASE}/repos/{_repo}/pulls"
    params = {"head": f"{owner}:{_task_branch}", "state": "open"}
    resp = httpx.get(url, headers=_headers(), params=params, timeout=30)
    if resp.status_code == 200:
        prs = resp.json()
        if prs:
            return prs[0]  # type: ignore[no-any-return]
    return None


def _update_pr(pr_number: int, title: str, body: str) -> None:
    """Update an existing PR's title and body."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/pulls/{pr_number}"
    httpx.patch(
        url,
        headers=_headers(),
        json={"title": title, "body": body},
        timeout=30,
    )


def _pr_number_from_url(pr_url: str) -> int | None:
    """Extract PR number from a GitHub PR URL."""
    # https://github.com/owner/repo/pull/123
    parts = pr_url.rstrip("/").split("/")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return None


def update_pr_from_url(pr_url: str, title: str, body: str) -> None:
    """Update a PR's title and body given its URL."""
    pr_number = _pr_number_from_url(pr_url)
    if pr_number:
        _update_pr(pr_number, title, body)


def mark_pr_ready(pr_url: str) -> None:
    """Mark a draft PR as ready for review via the GraphQL API."""
    pr_number = _pr_number_from_url(pr_url)
    if not pr_number:
        return
    # Get the PR node ID first
    url = f"{GITHUB_API_BASE}/repos/{_repo}/pulls/{pr_number}"
    resp = httpx.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return
    node_id = resp.json().get("node_id")
    if not node_id:
        return
    # Use GraphQL to mark ready (REST API doesn't support this)
    graphql_url = "https://api.github.com/graphql"
    query = """
    mutation($id: ID!) {
        markPullRequestReadyForReview(input: {pullRequestId: $id}) {
            pullRequest { number }
        }
    }
    """
    httpx.post(
        graphql_url,
        headers=_headers(),
        json={"query": query, "variables": {"id": node_id}},
        timeout=30,
    )


def _task_id_from_branch() -> str:
    """Extract task ID from the configured task branch."""
    if _task_branch.startswith("nubi/"):
        return _task_branch[len("nubi/") :]
    return _task_branch


def poll_ci_checks(
    timeout_seconds: int = 600,
    poll_interval: int = 30,
) -> tuple[str, str]:
    """Poll GitHub Checks API until CI completes or timeout.

    Returns (status, feedback) where:
    - status: "success", "failure", or "timed_out"
    - feedback: details of failed checks on failure, empty on success
    """
    # Get HEAD SHA of the task branch
    url = f"{GITHUB_API_BASE}/repos/{_repo}/git/ref/heads/{_task_branch}"
    resp = httpx.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return "failure", f"Could not resolve branch HEAD: {resp.status_code}"
    head_sha = resp.json()["object"]["sha"]

    deadline = time.time() + timeout_seconds
    logger.info("Polling CI checks for %s@%s (sha=%s)", _repo, _task_branch, head_sha[:8])

    while time.time() < deadline:
        check_url = f"{GITHUB_API_BASE}/repos/{_repo}/commits/{head_sha}/check-suites"
        resp = httpx.get(check_url, headers=_headers(), timeout=30)
        if resp.status_code != 200:
            time.sleep(poll_interval)
            continue

        suites = resp.json().get("check_suites", [])
        if not suites:
            time.sleep(poll_interval)
            continue

        # Check if all suites have concluded
        all_concluded = all(s.get("conclusion") is not None for s in suites)
        if not all_concluded:
            time.sleep(poll_interval)
            continue

        # All concluded — check for failures
        failed_suites = [
            s for s in suites if s.get("conclusion") not in ("success", "neutral", "skipped")
        ]
        if not failed_suites:
            logger.info("All CI checks passed")
            return "success", ""

        # Get details of failed check runs
        feedback = _get_failed_check_runs_feedback(head_sha)
        logger.warning("CI checks failed: %s", feedback[:200])
        return "failure", feedback

    logger.warning("CI check polling timed out after %ds", timeout_seconds)
    return "timed_out", f"CI checks did not complete within {timeout_seconds}s"


def _get_failed_check_runs_feedback(commit_sha: str) -> str:
    """Fetch details of failed check runs for a commit."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/commits/{commit_sha}/check-runs"
    resp = httpx.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return "Could not fetch check run details"

    check_runs = resp.json().get("check_runs", [])
    failed = [cr for cr in check_runs if cr.get("conclusion") == "failure"]

    if not failed:
        return "Check suite failed but no individual check run failures found"

    parts: list[str] = []
    for cr in failed:
        name = cr.get("name", "unknown")
        output = cr.get("output", {})
        summary = output.get("summary", "") or output.get("title", "")
        text = output.get("text", "")
        detail = summary or text
        if len(detail) > 4000:
            detail = detail[:4000] + "..."
        parts.append(f"### {name}\n{detail}" if detail else f"### {name}\nNo details")

    return "\n\n".join(parts)


def write_monitor_result_to_branch(result: MonitorResult) -> bool:
    """Write .nubi/{task_id}/monitor.json to the task branch via GitHub Contents API.

    Returns True on success, False on failure.
    """
    from nubi.agents.monitor_result import monitor_file_path

    task_id = _task_id_from_branch()
    file_path = monitor_file_path(task_id)
    url = f"{GITHUB_API_BASE}/repos/{_repo}/contents/{file_path}"

    content_bytes = result.model_dump_json(indent=2).encode()
    content_b64 = base64.b64encode(content_bytes).decode()

    # Check if file already exists (need sha for update)
    get_resp = httpx.get(url, headers=_headers(), params={"ref": _task_branch}, timeout=30)
    payload: dict[str, Any] = {
        "message": "nubi: add monitor audit result",
        "content": content_b64,
        "branch": _task_branch,
    }
    if get_resp.status_code == 200:
        payload["sha"] = get_resp.json()["sha"]

    put_resp = httpx.put(url, headers=_headers(), json=payload, timeout=30)
    if put_resp.status_code in (200, 201):
        logger.info("Wrote monitor result to %s@%s", _repo, _task_branch)
        return True
    logger.error("Failed to write monitor result: %d %s", put_resp.status_code, put_resp.text)
    return False


@tool
def submit_audit(
    decision: str,
    summary: str,
    pr_summary: str = "",
    concerns: list[dict[str, object]] | None = None,
) -> str:
    """Submit your final audit decision.

    You MUST call this tool exactly once at the end of your audit.

    Args:
        decision: One of "approve" or "flag".
        summary: One-sentence summary of the audit outcome.
        pr_summary: Markdown summary for the PR description. Write this as if you are
            explaining the changes to a teammate reviewing the PR. Include:
            - What was changed and why (from the task description)
            - Key implementation decisions visible in the diff
            - What was tested or validated (gate results, reviewer findings)
            - Any caveats or follow-up items
        concerns: Optional list of concerns found. Each dict should have:
            severity (critical/major/minor), area (process/output/security), description.
    """
    global _audit_result

    try:
        parsed_decision = MonitorDecision(decision)
    except ValueError:
        return f"Invalid decision: {decision!r}. Must be one of: approve, flag."

    parsed_concerns = []
    for item in concerns or []:
        parsed_concerns.append(MonitorConcern.model_validate(item))

    _audit_result = MonitorResult(
        decision=parsed_decision,
        summary=summary,
        pr_summary=pr_summary,
        concerns=parsed_concerns,
    )

    return f"Audit submitted: {parsed_decision.value}"


def _format_gate_status(status: str) -> str:
    """Return emoji for a gate status."""
    mapping = {
        "passed": "✅ pass",
        "failed": "❌ fail",
        "skipped": "⏭ skipped",
    }
    return mapping.get(status.lower(), f"? {status}")


def _format_review_decision(decision: str) -> str:
    """Return emoji + label for review decision."""
    mapping = {
        "approve": "✅ Approve",
        "request-changes": "🔁 Request Changes",
        "reject": "❌ Reject",
    }
    return mapping.get(decision.lower(), f"? {decision}")


def _format_monitor_decision(decision: str) -> str:
    """Return emoji + label for monitor decision."""
    mapping = {
        "approve": "✅ Approve",
        "flag": "🚩 Flag",
        "ci-failed": "❌ CI Failed",
        "escalate": "⚠ Escalate",
    }
    return mapping.get(decision.lower(), f"? {decision}")


def _read_json_file(path: str) -> dict[str, Any] | None:
    """Read and parse a JSON file from the task branch. Returns None on failure."""
    try:
        content = read_branch_file(path)
        if content.startswith("Error:"):
            return None
        # json.loads returns Any; cast to the expected dict type to satisfy mypy
        return cast("dict[str, Any]", json.loads(content))
    except Exception:
        return None


def _build_executor_section(result_data: dict[str, Any] | None) -> str:
    """Build the Executor section markdown."""
    if result_data is None:
        return "### Executor\n| | |\n|---|---|\n| Status | ⚠ Not found |\n"

    status = result_data.get("status", "unknown")
    status_emoji = "✅" if status == "success" else "❌"
    commit = result_data.get("commit_sha", "")[:8]
    summary = result_data.get("summary", "No summary")

    lines = [
        "### Executor",
        "| |",
        "|---|---|",
        f"| Status | {status_emoji} {status.capitalize()} |",
        f"| Commit | `{commit}` |",
        f"| Summary | {summary} |",
    ]
    return "\n".join(lines)


def _build_gates_section(gates_data: dict[str, Any] | None) -> str:
    """Build the Gates section markdown."""
    if gates_data is None:
        return "### Gates\n| Gate | Result | Details |\n|---|---|---|\n| — | ⚠ Not found | |\n"

    all_gates = gates_data.get("gates", [])
    if not all_gates:
        return "### Gates\n| Gate | Result | Details |\n|---|---|---|\n| — | ⚠ No gates run | |\n"

    lines = ["### Gates", "| Gate | Result | Details |", "|---|---|---|"]

    # Filter to unique gates for display (first attempt, unique name)
    seen_names: set[str] = set()
    for gate in all_gates:
        name = gate.get("name", "?")
        category = gate.get("category", "")
        if name in seen_names:
            continue
        seen_names.add(name)

        status = gate.get("status", "unknown")
        output = gate.get("output", "")
        # Truncate output for the summary table
        detail = output.split("\n", 1)[0] if output else ""
        if len(detail) > 60:
            detail = detail[:60] + "..."
        if status == "skipped" and detail:
            detail = detail[:60]
        display_name = f"{name} ({category})" if category != name else name
        lines.append(f"| {display_name} | {_format_gate_status(status)} | {detail} |")

    # Collapsible section for non-passing attempts
    attempts = gates_data.get("attempt", 1)
    non_passing = [g for g in all_gates if g.get("status") not in ("passed", "skipped")]

    if attempts > 1 or non_passing:
        lines.append("")
        if attempts > 1:
            lines.append("<details>")
            lines.append(f"<summary>Gate details (attempt {attempts} — failed)</summary>")
        else:
            lines.append("<details>")
            lines.append("<summary>Gate details (with failures)</summary>")

        lines.append("")
        lines.append("| Gate | Result | Details |")
        lines.append("|---|---|---|")
        for gate in all_gates:
            name = gate.get("name", "?")
            category = gate.get("category", "")
            status = gate.get("status", "unknown")
            output = gate.get("output", "")
            detail = output.split("\n", 1)[0] if output else ""
            if len(detail) > 80:
                detail = detail[:80] + "..."
            display_name = f"{name} ({category})" if category != name else name
            lines.append(f"| {display_name} | {_format_gate_status(status)} | {detail} |")

        lines.append("")
        lines.append("</details>")

    return "\n".join(lines)


def _build_review_section(review_data: dict[str, Any] | None) -> str:
    """Build the Reviewer section markdown."""
    if review_data is None:
        return (
            "### Reviewer\n| | |\n|---|---|\n"
            "| Decision | ⚠ Skipped |\n| Feedback | Not available |\n"
        )

    decision = review_data.get("decision", "unknown")
    feedback = review_data.get("feedback", "")
    if not feedback:
        feedback = "No feedback provided."

    lines = [
        "### Reviewer",
        "| |",
        "|---|---|",
        f"| Decision | {_format_review_decision(decision)} |",
        f"| Feedback | {feedback[:300]} |",
    ]
    return "\n".join(lines)


def _build_monitor_section(monitor_data: dict[str, Any] | None) -> str:
    """Build the Monitor section markdown."""
    if monitor_data is None:
        return (
            "### Monitor\n| | |\n|---|---|\n"
            "| Decision | ⚠ Not found |\n| CI Status | ⚠ Not available |\n"
        )

    decision = monitor_data.get("decision", "unknown")
    ci_status = monitor_data.get("ci_status", "")
    if ci_status == "success":
        ci_emoji = "✅"
    elif ci_status in ("failure", "timed_out"):
        ci_emoji = "❌"
    else:
        ci_emoji = "⚠"
    ci_label = ci_status if ci_status else "Not available"

    lines = [
        "### Monitor",
        "| |",
        "|---|---|",
        f"| Decision | {_format_monitor_decision(decision)} |",
        f"| CI Status | {ci_emoji} {ci_label} |",
    ]
    return "\n".join(lines)


def format_pipeline_summary(
    pr_url: str,
    repo: str,
    branch: str,
) -> str:
    """Read artifact files from the task branch and format as a pipeline summary comment.

    Args:
        pr_url: GitHub PR URL.
        repo: Repository in "owner/name" format.
        branch: Task branch name (e.g. "nubi/task-id").

    Returns:
        Markdown-formatted pipeline summary string.
    """
    # Extract task_id from branch name
    task_id = branch
    if branch.startswith("nubi/"):
        task_id = branch[len("nubi/") :]

    base = f".nubi/{task_id}"
    result_data = _read_json_file(f"{base}/result.json")
    gates_data = _read_json_file(f"{base}/gates.json")
    review_data = _read_json_file(f"{base}/review.json")
    monitor_data = _read_json_file(f"{base}/monitor.json")

    lines = [
        "## Nubi Pipeline Summary",
        "",
        f"**Task:** `{task_id}` · **Branch:** `{branch}`",
        "",
        _build_executor_section(result_data),
        "",
        _build_gates_section(gates_data),
        "",
        _build_review_section(review_data),
        "",
        _build_monitor_section(monitor_data),
        "",
        "---",
        f"*Generated by [Nubi](https://github.com/{repo}) · [pipeline artifacts]({base}/)*",
        "",
        SUMMARY_MARKER,
    ]

    return "\n".join(lines)


def _find_existing_summary_comment(pr_number: int) -> int | None:
    """Find the ID of an existing pipeline summary comment on a PR.

    Returns the comment ID if found, or None.
    """
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return None

    comments: list[dict[str, Any]] = resp.json()
    for comment in comments:
        if SUMMARY_MARKER in comment.get("body", ""):
            # comment["id"] is always an int on GitHub — cast to satisfy mypy
            return cast("int | None", comment.get("id"))
    return None


def _post_or_update_comment(pr_number: int, body: str) -> None:
    """Post a new comment or update an existing one if already present."""
    existing_id = _find_existing_summary_comment(pr_number)
    if existing_id:
        # Update existing comment
        url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/comments/{existing_id}"
        httpx.patch(
            url,
            headers=_headers(),
            json={"body": body},
            timeout=30,
        )
    else:
        # Post new comment
        url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
        httpx.post(
            url,
            headers=_headers(),
            json={"body": body},
            timeout=30,
        )


def post_pipeline_summary(
    pr_url: str,
    repo: str,
    branch: str,
    token: str,
) -> str:
    """Post a structured pipeline summary comment on the PR.

    Reads the four artifact files from the task branch (result.json, gates.json,
    review.json, monitor.json) and formats them into a markdown comment with tables
    for each pipeline stage. If a summary comment already exists, updates it.

    Args:
        pr_url: GitHub PR URL.
        repo: Repository in "owner/name" format.
        branch: Task branch name (e.g. "nubi/task-id").
        token: GitHub token for authentication.

    Returns:
        A string indicating success or the error encountered.
    """
    # Configure globals for this call (read_branch_file uses _repo/_task_branch/_token)
    global _repo, _task_branch, _token
    prev_repo = _repo
    prev_branch = _task_branch
    prev_token = _token

    _repo = repo
    _task_branch = branch
    _token = token

    try:
        pr_number = _pr_number_from_url(pr_url)
        if not pr_number:
            return f"Error: Could not extract PR number from {pr_url}"

        summary = format_pipeline_summary(pr_url, repo, branch)
        _post_or_update_comment(pr_number, summary)
        return "Pipeline summary posted to PR."
    except httpx.HTTPError as e:
        return f"Error posting pipeline summary: {e}"
    finally:
        _repo = prev_repo
        _task_branch = prev_branch
        _token = prev_token
