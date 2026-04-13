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

        all_suites = resp.json().get("check_suites", [])
        # Only consider GitHub Actions suites — other apps (e.g. Claude) may
        # stay queued forever and block the poll.
        suites = [s for s in all_suites if s.get("app", {}).get("slug") == "github-actions"]
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


# --- Pipeline Summary ---


PIPELINE_SUMMARY_MARKER = "<!-- nubi-pipeline-summary -->"
NUBI_URL = "https://github.com/kuuji/nubi"


def _artifact_path(task_id: str, filename: str) -> str:
    """Return the GitHub API path for an artifact file."""
    return f".nubi/{task_id}/{filename}"


def _read_artifact(task_id: str, filename: str) -> dict[str, Any] | None:
    """Read a JSON artifact file from the task branch. Returns None if missing."""
    path = _artifact_path(task_id, filename)
    url = f"{GITHUB_API_BASE}/repos/{_repo}/contents/{path}"
    resp = httpx.get(url, headers=_headers(), params={"ref": _task_branch}, timeout=30)
    if resp.status_code != 200:
        return None
    data = resp.json()
    content_b64 = data.get("content", "")
    # Cast needed because json.loads returns Any
    return cast(dict[str, Any], json.loads(base64.b64decode(content_b64).decode()))


def _gate_status_icon(status: str) -> str:
    """Return emoji icon for a gate status."""
    mapping: dict[str, str] = {
        "passed": "✅ pass",
        "failed": "❌ fail",
        "skipped": "⏭ skipped",
    }
    return mapping.get(status, f"❓ {status}")


def _gate_details(gate: dict[str, Any]) -> str:
    """Format gate output into a brief details string."""
    status: str = gate.get("status", "")
    output: str = gate.get("output", "")
    error: str = gate.get("error", "")

    if error:
        return error[:200]

    if status == "skipped":
        reason: str = gate.get("skipped_reason", "")
        return reason if reason else "skipped"

    if not output:
        return status

    # Truncate output to a reasonable length
    lines = output.strip().split("\n")
    if len(lines) > 3:
        return "\n".join(lines[:3]) + " ..."
    return output[:200] if len(output) > 200 else output


def _decision_icon(decision: str) -> str:
    """Return emoji icon for a review/decision."""
    mapping: dict[str, str] = {
        "approve": "✅ Approve",
        "flag": "⚠️ Flag",
        "reject": "❌ Reject",
        "skipped": "⏭ Skipped",
    }
    return mapping.get(decision, decision)


def _ci_status_icon(status: str) -> str:
    """Return emoji icon for CI status."""
    mapping: dict[str, str] = {
        "success": "✅ All checks passed",
        "failure": "❌ Failed",
        "timed_out": "⏱ Timed out",
        "pending": "⏳ Pending",
    }
    return mapping.get(status, status)


def _executor_status_display(status: str, error: str) -> str:
    """Format executor status for display."""
    if status == "success":
        return "✅ Complete"
    if error:
        return f"❌ Failed: {error}"
    return f"❌ {status}"


def _format_executor(data: dict[str, Any]) -> str:
    """Format the executor section of the summary."""
    status: str = data.get("status", "unknown")
    commit_sha_raw: str = data.get("commit_sha", "")
    commit_sha: str = commit_sha_raw[:8] if commit_sha_raw else "N/A"
    summary: str = data.get("summary", "")
    error: str = data.get("error", "")

    status_display = _executor_status_display(status, error)
    summary_display = summary if summary else error if error else ""

    return f"""### Executor
| | |
|---|---|
| Status | {status_display} |
| Commit | `{commit_sha}` |
| Summary | {summary_display}"""


def _format_gates(data: dict[str, Any]) -> str:
    """Format the gates section of the summary."""
    gates: list[dict[str, Any]] = data.get("gates", [])
    if not gates:
        return "### Gates\n| | |\n|---|---|\n| Status | No gates run |"

    lines = ["### Gates", "| Gate | Result | Details |", "|---|---|---|"]

    for gate in gates:
        name: str = gate.get("name", "?")
        gate_status: str = gate.get("status", "unknown")
        icon = _gate_status_icon(gate_status)
        details = _gate_details(gate)
        details_truncated = details[:100] + "..." if len(details) > 100 else details
        lines.append(f"| {name} | {icon} | {details_truncated} |")

    return "\n".join(lines)


def _format_reviewer(data: dict[str, Any] | None) -> str:
    """Format the reviewer section of the summary."""
    if data is None:
        return """### Reviewer
| | |
|---|---|
| Decision | ⏭ Skipped |"""

    decision: str = data.get("decision", "unknown")
    feedback: str = data.get("feedback", "")
    icon = _decision_icon(decision)

    # Truncate feedback if too long
    feedback_display = feedback[:200] + "..." if len(feedback) > 200 else feedback

    return f"""### Reviewer
| | |
|---|---|
| Decision | {icon} |
| Feedback | {feedback_display}"""


def _format_monitor(data: dict[str, Any] | None, ci_status: str = "") -> str:
    """Format the monitor section of the summary."""
    if data is None:
        return "### Monitor\n| | |\n|---|---|\n| Decision | ⏭ Skipped |"

    decision: str = data.get("decision", "unknown")
    icon = _decision_icon(decision)
    ci_icon = _ci_status_icon(ci_status) if ci_status else ""

    ci_line = f"\n| CI Status | {ci_icon} |" if ci_status else ""

    return f"""### Monitor
| | |
|---|---|
| Decision | {icon} |{ci_line}"""


def format_pipeline_summary(
    task_id: str,
    branch: str,
    executor_data: dict[str, Any] | None,
    gates_data: dict[str, Any] | None,
    review_data: dict[str, Any] | None,
    monitor_data: dict[str, Any] | None,
    ci_status: str = "",
) -> str:
    """Format pipeline data into a markdown summary comment.

    Parameters:
        task_id: The task identifier.
        branch: The full task branch name (e.g. "nubi/add-rate-limiting-a1b2c3").
        executor_data: The result.json artifact data.
        gates_data: The gates.json artifact data.
        review_data: The review.json artifact data (None if skipped).
        monitor_data: The monitor.json artifact data.
        ci_status: CI check status string.
    """
    lines: list[str] = [
        "## Nubi Pipeline Summary",
        "",
        f"**Task:** `{task_id}` · **Branch:** `{branch}`",
        "",
    ]

    if executor_data:
        lines.append(_format_executor(executor_data))
    else:
        lines.append("### Executor\n| | |\n|---|---|\n| Status | ⏭ Skipped |")

    lines.append("")

    if gates_data:
        lines.append(_format_gates(gates_data))
    else:
        lines.append("### Gates\n| | |\n|---|---|\n| Status | No gates run |")

    lines.append("")
    lines.append(_format_reviewer(review_data))
    lines.append("")
    lines.append(_format_monitor(monitor_data, ci_status))
    lines.extend(
        [
            "",
            "---",
            PIPELINE_SUMMARY_MARKER,
            f"*Generated by [Nubi]({NUBI_URL}) · [pipeline artifacts](.nubi/{task_id}/)*",
        ]
    )

    return "\n".join(lines)


def _find_existing_summary_comment(pr_number: int) -> int | None:
    """Find an existing pipeline summary comment by the HTML marker.

    Returns the comment ID if found, None otherwise.
    """
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return None

    for comment in resp.json():
        body: str = comment.get("body", "")
        if PIPELINE_SUMMARY_MARKER in body:
            comment_id: int = comment["id"]
            return comment_id
    return None


def _post_comment(pr_number: int, body: str) -> bool:
    """Post a new comment on a PR. Returns True on success."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.post(
        url,
        headers=_headers(),
        json={"body": body},
        timeout=30,
    )
    if resp.status_code in (200, 201):
        return True
    logger.error("Failed to post comment: %d %s", resp.status_code, resp.text)
    return False


def _update_comment(comment_id: int, body: str) -> bool:
    """Update an existing comment. Returns True on success."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/comments/{comment_id}"
    resp = httpx.patch(
        url,
        headers=_headers(),
        json={"body": body},
        timeout=30,
    )
    if resp.status_code == 200:
        return True
    logger.error("Failed to update comment: %d %s", resp.status_code, resp.text)
    return False


def post_pipeline_summary(
    pr_url: str,
    repo: str,
    branch: str,
    token: str,
) -> str:
    """Post a structured pipeline summary comment on the PR.

    Reads the artifact files from the branch (.nubi/{task_id}/result.json,
    gates.json, review.json, monitor.json) and formats them into a markdown
    comment with tables for each pipeline stage.

    On retries, finds and updates the existing comment using a marker.

    Args:
        pr_url: The GitHub PR URL.
        repo: The repository in "owner/repo" format.
        branch: The task branch name (e.g. "nubi/add-rate-limiting-a1b2c3").
        token: GitHub API token.

    Returns:
        A status string indicating success or failure.
    """
    # Configure a temporary context for reading artifacts
    configure(repo=repo, base_branch="", task_branch=branch, token=token)

    pr_number = _pr_number_from_url(pr_url)
    if not pr_number:
        return "Error: Could not extract PR number from URL"

    # Extract task_id from branch name
    task_id = branch.split("/")[-1] if "/" in branch else branch

    # Read all artifact files
    executor_data = _read_artifact(task_id, "result.json")
    gates_data = _read_artifact(task_id, "gates.json")
    review_data = _read_artifact(task_id, "review.json")
    monitor_data = _read_artifact(task_id, "monitor.json")

    # Get CI status from monitor data if available
    ci_status = ""
    if monitor_data:
        ci_status_arg: str = monitor_data.get("ci_status", "")
        ci_status = ci_status_arg

    # Format the summary
    body = format_pipeline_summary(
        task_id=task_id,
        branch=branch,
        executor_data=executor_data,
        gates_data=gates_data,
        review_data=review_data,
        monitor_data=monitor_data,
        ci_status=ci_status,
    )

    # Check for existing comment and update or create
    existing_id = _find_existing_summary_comment(pr_number)
    if existing_id:
        if _update_comment(existing_id, body):
            return f"Pipeline summary updated: comment #{existing_id}"
        return "Error: Failed to update existing comment"
    else:
        if _post_comment(pr_number, body):
            return "Pipeline summary posted"
        return "Error: Failed to post comment"
