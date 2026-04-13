"""GitHub REST API tools for the monitor agent — no git clone needed."""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx
from strands import tool

from nubi.agents.gate_result import gates_file_path
from nubi.agents.monitor_result import (
    MonitorConcern,
    MonitorDecision,
    MonitorResult,
    monitor_file_path,
)
from nubi.agents.result import result_file_path
from nubi.agents.review_result import review_file_path

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

# HTML marker used to identify and update pipeline summary comments
_PIPELINE_SUMMARY_MARKER = "<!-- nubi-pipeline-summary -->"

_repo: str = ""
_base_branch: str = ""
_task_branch: str = ""
_token: str = ""
_audit_result: MonitorResult | None = None


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


def _read_artifact_file(file_path: str) -> dict[str, Any] | None:
    """Read a JSON artifact file from the task branch. Returns None if missing."""
    try:
        url = f"{GITHUB_API_BASE}/repos/{_repo}/contents/{file_path}"
        resp = httpx.get(url, headers=_headers(), params={"ref": _task_branch}, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        content_b64 = data.get("content", "")
        file_content = base64.b64decode(content_b64).decode()
        parsed: dict[str, Any] = json.loads(file_content)
        return parsed
    except (json.JSONDecodeError, KeyError):
        return None


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


def _gate_status_icon(status: str) -> str:
    """Return emoji icon for a gate status."""
    icons = {
        "passed": "✅",
        "failed": "❌",
        "skipped": "⏭",
    }
    return icons.get(status.lower(), "❓")


def _find_existing_summary_comment(pr_number: int) -> dict[str, Any] | None:
    """Find an existing pipeline summary comment by marker."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return None

    comments: list[dict[str, Any]] = resp.json()
    for comment in comments:
        if _PIPELINE_SUMMARY_MARKER in comment.get("body", ""):
            return comment
    return None


def _post_comment(pr_number: int, body: str, existing_comment_id: int | None = None) -> bool:
    """Post or update a PR comment. Returns True on success."""
    if existing_comment_id is not None:
        # Update existing comment
        url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/comments/{existing_comment_id}"
        resp = httpx.patch(url, headers=_headers(), json={"body": body}, timeout=30)
    else:
        # Create new comment
        url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
        resp = httpx.post(url, headers=_headers(), json={"body": body}, timeout=30)

    if resp.status_code in (200, 201):
        return True
    logger.error("Failed to post PR comment: %d %s", resp.status_code, resp.text)
    return False


def _build_executor_section(executor_data: dict[str, Any] | None) -> str:
    """Build the Executor section of the pipeline summary."""
    if executor_data is None:
        return "### Executor\n| | |\n|---|---|\n| Status | ⏭ Skipped |\n"

    status = executor_data.get("status", "unknown")
    status_icon = "✅" if status == "success" else "❌"
    commit_sha = executor_data.get("commit_sha", "")[:7]
    summary = executor_data.get("summary", "")
    attempts = executor_data.get("attempts", 1)

    lines = [
        "### Executor",
        "| | |",
        "|---|---|",
        f"| Status | {status_icon} {status.capitalize()} |",
        f"| Attempts | {attempts} |",
    ]
    if commit_sha:
        lines.append(f"| Commit | `{commit_sha}` |")
    if summary:
        lines.append(f"| Summary | {summary[:500]} |")

    return "\n".join(lines) + "\n"


def _build_gates_section(gates_data: dict[str, Any] | None) -> str:
    """Build the Gates section of the pipeline summary."""
    if gates_data is None:
        return "### Gates\n| | |\n|---|---|\n| Status | ⏭ Skipped |\n"

    lines = [
        "### Gates",
        "| Gate | Result | Details |",
        "|---|---|---|",
    ]

    gates = gates_data.get("gates", [])
    if not gates:
        lines.append("| — | ⏭ No gates run | — |")
    else:
        for gate in gates:
            gate_name = gate.get("name", "unknown")
            status = gate.get("status", "unknown")
            icon = _gate_status_icon(status)
            output = gate.get("output", "")

            # Create a concise details string
            details = output.strip() if output else ""
            if len(details) > 100:
                details = details[:100] + "..."
            if not details:
                error = gate.get("error", "")
                if error:
                    details = error[:100]
            if not details:
                details = "—"

            lines.append(f"| {gate_name} | {icon} {status} | {details} |")

    # Add attempt info if multiple attempts
    attempt = gates_data.get("attempt", 1)
    if attempt > 1:
        lines.append(f"\n*Attempt {attempt}*")

    return "\n".join(lines) + "\n"


def _build_reviewer_section(review_data: dict[str, Any] | None) -> str:
    """Build the Reviewer section of the pipeline summary."""
    if review_data is None:
        return "### Reviewer\n| | |\n|---|---|\n| Decision | ⏭ Skipped |\n"

    decision = review_data.get("decision", "unknown")
    feedback = review_data.get("feedback", "")

    if decision == "approve":
        icon = "✅"
    elif decision == "request-changes":
        icon = "🔄"
    elif decision == "reject":
        icon = "❌"
    else:
        icon = "❓"

    lines = [
        "### Reviewer",
        "| | |",
        "|---|---|",
        f"| Decision | {icon} {decision} |",
    ]

    if feedback:
        # Truncate feedback
        fb = feedback[:500]
        if len(feedback) > 500:
            fb += "..."
        lines.append(f"| Feedback | {fb} |")

    return "\n".join(lines) + "\n"


def _build_monitor_section(monitor_data: dict[str, Any] | None, ci_status: str) -> str:
    """Build the Monitor section of the pipeline summary."""
    lines = [
        "### Monitor",
        "| | |",
        "|---|---|",
    ]

    if monitor_data is None:
        lines.append("| Decision | ⏭ Skipped |")
    else:
        decision = monitor_data.get("decision", "unknown")
        if decision == "approve":
            icon = "✅"
        elif decision == "flag":
            icon = "⚠️"
        elif decision == "ci-failed":
            icon = "❌"
        elif decision == "escalate":
            icon = "🔺"
        else:
            icon = "❓"
        lines.append(f"| Decision | {icon} {decision} |")

    # CI status
    if ci_status == "success":
        ci_icon = "✅"
    elif ci_status in ("failure", "timed_out"):
        ci_icon = "❌"
    else:
        ci_icon = "⏳"
    lines.append(f"| CI Status | {ci_icon} {ci_status} |")

    return "\n".join(lines) + "\n"


def _format_pipeline_summary(
    task_id: str,
    executor_data: dict[str, Any] | None,
    gates_data: dict[str, Any] | None,
    review_data: dict[str, Any] | None,
    monitor_data: dict[str, Any] | None,
    ci_status: str,
) -> str:
    """Format the complete pipeline summary markdown."""
    lines = [
        "## Nubi Pipeline Summary",
        "",
        f"**Task:** `{task_id}` · **Branch:** `{_task_branch}`",
        "",
        _build_executor_section(executor_data),
        _build_gates_section(gates_data),
        _build_reviewer_section(review_data),
        _build_monitor_section(monitor_data, ci_status),
        "---",
        "*Generated by [Nubi](https://github.com/kuuji/nubi) · "
        "[pipeline artifacts](.nubi/" + f"{task_id}/)*",
        "",
        _PIPELINE_SUMMARY_MARKER,
    ]

    return "\n".join(lines)


def post_pipeline_summary(pr_url: str, repo: str, branch: str, token: str) -> bool:
    """Post a structured pipeline summary comment on a PR.

    Reads the four artifact files (result.json, gates.json, review.json,
    monitor.json) from the task branch and formats them into a markdown
    comment summarizing the entire pipeline run.

    On retries, finds and updates an existing comment using an HTML marker.

    Args:
        pr_url: The GitHub PR URL.
        repo: Repository in "owner/name" format.
        branch: The task branch name (e.g., "nubi/task-id").
        token: GitHub API token.

    Returns:
        True on success, False on failure.
    """
    # Extract PR number from URL
    pr_number = _pr_number_from_url(pr_url)
    if pr_number is None:
        logger.error("Could not extract PR number from URL: %s", pr_url)
        return False

    global _repo, _task_branch, _token

    # Configure module-level state for artifact reading
    old_repo = _repo
    old_branch = _task_branch
    old_token = _token

    try:
        _repo = repo
        _task_branch = branch
        _token = token

        task_id = _task_id_from_branch()

        # Read all artifact files
        executor_data = _read_artifact_file(result_file_path(task_id))
        gates_data = _read_artifact_file(gates_file_path(task_id))
        review_data = _read_artifact_file(review_file_path(task_id))
        monitor_data = _read_artifact_file(monitor_file_path(task_id))

        # Get CI status from monitor data or environment
        ci_status = ""
        if monitor_data:
            ci_status = monitor_data.get("ci_status", "")

        # Format the summary markdown
        summary_body = _format_pipeline_summary(
            task_id=task_id,
            executor_data=executor_data,
            gates_data=gates_data,
            review_data=review_data,
            monitor_data=monitor_data,
            ci_status=ci_status,
        )

        # Check for existing comment to update
        existing_comment = _find_existing_summary_comment(pr_number)
        existing_comment_id = existing_comment["id"] if existing_comment else None

        # Post or update the comment
        return _post_comment(pr_number, summary_body, existing_comment_id)

    finally:
        # Restore previous state
        _repo = old_repo
        _task_branch = old_branch
        _token = old_token


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
