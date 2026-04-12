"""GitHub REST API tools for the monitor agent — no git clone needed."""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx
from strands import tool

from nubi.agents.monitor_result import MonitorConcern, MonitorDecision, MonitorResult

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
PIPELINE_SUMMARY_MARKER = "<!-- nubi-pipeline-summary -->"

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
        pr_summary: Markdown summary for the PR description. Write this as if you
            are explaining the changes to a teammate reviewing the PR. Include:
            - What was changed and why (from the task description)
            - Key implementation decisions visible in the diff
            - What was tested or validated (gate results, reviewer findings)
            - Any caveats or follow-up items
        concerns: Optional list of concerns found. Each dict should have:
            severity (critical/major/minor), area (process/output/security),
            description.
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


# ---------------------------------------------------------------------------
# Pipeline Summary
# ---------------------------------------------------------------------------


def _read_artifact(path: str) -> dict[str, Any] | None:
    """Read a JSON artifact file from the task branch.

    Returns None if the file doesn't exist or can't be parsed.
    """
    try:
        content = read_branch_file(path)
        if content.startswith("Error:"):
            return None
        return json.loads(content)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _format_gate_result(gate: dict[str, Any]) -> tuple[str, str, str]:
    """Format a single gate result into (status_icon, status_text, details)."""
    status = gate.get("status", "unknown")

    if status == "passed":
        icon = "✅"
        text = "pass"
        details = _extract_gate_details(gate)
    elif status == "failed":
        icon = "❌"
        text = "fail"
        details = _extract_gate_details(gate)
    elif status == "skipped":
        icon = "⏭"
        text = "skipped"
        details = _extract_gate_details(gate)
    elif status == "error":
        icon = "⚠️"
        text = "error"
        details = _extract_gate_details(gate)
    else:
        icon = "❓"
        text = status
        details = _extract_gate_details(gate)

    return icon, text, details


def _extract_gate_details(gate: dict[str, Any]) -> str:
    """Extract human-readable details from a gate result."""
    output = gate.get("output", "")
    error = gate.get("error", "")

    # For ruff - look for error summary
    if "error" in output.lower() and "warnings" not in output.lower():
        # Try to extract line count
        lines = [ln for ln in output.strip().split("\n") if ln]
        if lines:
            # Return first few relevant lines
            relevant = [ln for ln in lines if "error" in ln.lower() or "warning" in ln.lower()]
            if relevant[:3]:
                sample = "; ".join(relevant[:3])
                if len(sample) > 100:
                    sample = sample[:100] + "..."
                return sample

    # For pytest - look for pass/fail counts
    if "passed" in output or "failed" in output:
        for line in output.strip().split("\n"):
            if "passed" in line or "failed" in line:
                return line.strip()

    # For radon - look for complexity summary
    if "complexity" in output.lower() or gate.get("category") == "complexity":
        lines = output.strip().split("\n")
        for line in lines:
            is_complexity = "complexity" in line.lower()
            has_rank = "rank" in line.lower() or ("A" in line or "B" in line)
            if is_complexity and has_rank:
                return line.strip()
        if lines:
            return lines[0][:100]

    # For diff_size
    if gate.get("category") == "diff_size":
        return output.strip().split("\n")[-1][:100] if output.strip() else "OK"

    # Default - return error or first meaningful line
    if error:
        return error[:100] if len(error) > 100 else error
    if output and len(output) < 200:
        return output.strip()
    if output:
        return output.strip()[:100] + "..."
    return "OK"


def _build_executor_section(result: dict[str, Any] | None) -> str:
    """Build the Executor section of the pipeline summary."""
    if result is None:
        return "### Executor\n| | |\n|---|---|\n| Status | ⚠️ Not available |\n"

    decision = result.get("decision", "unknown")
    summary = result.get("summary", "")

    # Determine status
    if decision == "approve":
        status_icon = "✅"
        status_text = "Complete"
    elif decision == "flag":
        status_icon = "⚠️"
        status_text = "Flagged"
    else:
        status_icon = "❓"
        status_text = decision.title()

    # Count attempts - look for patterns like "attempt N"
    attempts = 1
    if "attempt" in result:
        attempts = result["attempt"]
    elif "attempt" in summary.lower():
        for word in summary.split():
            if word.lower() == "attempt" or word.isdigit():
                pass
        # Simple count from error fields
        attempts = 1

    commit_sha = result.get("commit_sha", "")[:7]
    commit_line = f"\\`{commit_sha}\\`" if commit_sha else "N/A"

    summary_truncated = summary[:100] + "..." if len(summary) > 100 else summary

    lines = [
        "### Executor",
        "| | |",
        "|---|---|",
        f"| Status | {status_icon} {status_text} |",
        f"| Attempts | {attempts} |",
        f"| Commit | {commit_line} |",
        f"| Summary | {summary_truncated} |",
    ]
    return "\n".join(lines)


def _build_gates_section(gates_data: dict[str, Any] | None) -> str:
    """Build the Gates section of the pipeline summary."""
    not_available = "— | ⚠️ Not available | —"
    if gates_data is None:
        return f"### Gates\n| Gate | Result | Details |\n|---|---|---|\n| {not_available} |\n"

    gates = gates_data.get("gates", [])
    if not gates:
        no_gates = "— | ⚠️ No gates run | —"
        return f"### Gates\n| Gate | Result | Details |\n|---|---|---|\n| {no_gates} |\n"

    lines = [
        "### Gates",
        "| Gate | Result | Details |",
        "|---|---|---|",
    ]

    for gate in gates:
        icon, text, details = _format_gate_result(gate)
        name = gate.get("name", "unknown")
        lines.append(f"| {name} | {icon} {text} | {details} |")

    # Add failed attempts in collapsible details if any failed
    failed_gates = [g for g in gates if g.get("status") == "failed"]
    if failed_gates:
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>Gate details (failed)</summary>")
        lines.append("")
        lines.append("| Gate | Result | Details |")
        lines.append("|---|---|---|")
        for gate in failed_gates:
            icon, text, details = _format_gate_result(gate)
            name = gate.get("name", "unknown")
            lines.append(f"| {name} | {icon} {text} | {details} |")
        lines.append("</details>")

    return "\n".join(lines)


def _build_reviewer_section(review: dict[str, Any] | None) -> str:
    """Build the Reviewer section of the pipeline summary."""
    if review is None:
        return "### Reviewer\n| | |\n|---|---|\n| Decision | ⏭ Skipped |\n"

    # Check for skipped state
    issues = review.get("issues", [])
    if not review.get("decision") and not issues:
        return "### Reviewer\n| | |\n|---|---|\n| Decision | ⏭ Skipped |\n"

    decision = review.get("decision", "unknown")
    feedback = review.get("feedback", "")

    # Determine icon and text
    if decision == "approve":
        icon = "✅"
        text = "Approve"
    elif decision == "request_changes":
        icon = "⚠️"
        text = "Request Changes"
    elif decision == "comment":
        icon = "💬"
        text = "Comment"
    else:
        icon = "❓"
        text = decision.title() if decision else "Unknown"

    # Truncate feedback
    feedback_short = feedback[:200] + "..." if len(feedback) > 200 else feedback
    feedback_escaped = feedback_short.replace("\n", " ").replace("|", "\\|")

    lines = [
        "### Reviewer",
        "| | |",
        "|---|---|",
        f"| Decision | {icon} {text} |",
        f"| Feedback | {feedback_escaped} |",
    ]
    return "\n".join(lines)


def _build_monitor_section(monitor: dict[str, Any] | None) -> str:
    """Build the Monitor section of the pipeline summary."""
    if monitor is None:
        return "### Monitor\n| | |\n|---|---|\n| Decision | ⚠️ Not available |\n"

    decision = monitor.get("decision", "unknown")
    ci_status = monitor.get("ci_status", "")

    # Determine decision icon and text
    if decision == "approve":
        dec_icon = "✅"
        dec_text = "Approve"
    elif decision == "flag":
        dec_icon = "⚠️"
        dec_text = "Flagged"
    elif decision == "ci-failed":
        dec_icon = "❌"
        dec_text = "CI Failed"
    elif decision == "escalate":
        dec_icon = "🔺"
        dec_text = "Escalated"
    else:
        dec_icon = "❓"
        dec_text = decision.title()

    # Determine CI status icon
    if ci_status == "success":
        ci_icon = "✅"
        ci_text = "All checks passed"
    elif ci_status == "failure" or ci_status == "ci-failed":
        ci_icon = "❌"
        ci_text = "Checks failed"
    elif ci_status == "timed_out":
        ci_icon = "⏱️"
        ci_text = "Timed out"
    elif ci_status == "pending":
        ci_icon = "⏳"
        ci_text = "Pending"
    else:
        ci_icon = "❓"
        ci_text = ci_status or "Unknown"

    lines = [
        "### Monitor",
        "| | |",
        "|---|---|",
        f"| Decision | {dec_icon} {dec_text} |",
        f"| CI Status | {ci_icon} {ci_text} |",
    ]
    return "\n".join(lines)


def format_pipeline_summary_markdown(
    task_id: str,
    result: dict[str, Any] | None = None,
    gates: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
    monitor: dict[str, Any] | None = None,
) -> str:
    """Format pipeline artifact data into a markdown pipeline summary comment.

    Args:
        task_id: The task identifier (used in the branch name).
        result: The executor result.json artifact.
        gates: The gates.json artifact.
        review: The review.json artifact.
        monitor: The monitor.json artifact.

    Returns:
        A markdown-formatted string for the PR comment.
    """
    task_short = task_id[:20] + "..." if len(task_id) > 20 else task_id
    branch_name = f"nubi/{task_id}"

    lines = [
        PIPELINE_SUMMARY_MARKER,
        "",
        "## Nubi Pipeline Summary",
        "",
        f"**Task:** \\`{task_short}\\` · **Branch:** \\`{branch_name}\\`",
        "",
        _build_executor_section(result),
        "",
        _build_gates_section(gates),
        "",
        _build_reviewer_section(review),
        "",
        _build_monitor_section(monitor),
        "",
        "---",
        "*Generated by [Nubi](https://github.com/kuuji/nubi) · "
        f"[pipeline artifacts](.nubi/{task_id}/)*",
    ]

    return "\n".join(lines)


def _find_existing_pipeline_comment(pr_number: int) -> dict[str, Any] | None:
    """Find an existing pipeline summary comment on a PR.

    Returns the comment dict if found, None otherwise.
    """
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return None

    comments = resp.json()
    for comment in comments:
        if PIPELINE_SUMMARY_MARKER in comment.get("body", ""):
            return comment
    return None


def _update_comment(comment_id: int, body: str) -> None:
    """Update an existing PR comment."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/comments/{comment_id}"
    httpx.patch(
        url,
        headers=_headers(),
        json={"body": body},
        timeout=30,
    )


def _post_comment(pr_number: int, body: str) -> None:
    """Post a new PR comment."""
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
    """Post a pipeline summary comment on a PR.

    Reads the four pipeline artifact files from the task branch,
    formats them as a markdown table, and posts as a PR comment.
    On subsequent runs, finds and updates the existing comment.

    Args:
        pr_url: The GitHub PR URL (e.g. https://github.com/owner/repo/pull/123).
        repo: The repository in owner/repo format.
        branch: The base branch name (e.g. main).
        token: GitHub API token.

    Returns:
        "posted" if a new comment was created, "updated" if an existing comment
        was updated, or an error message on failure.
    """
    # Configure for this run
    configure(repo=repo, base_branch=branch, task_branch=branch, token=token)

    pr_number = _pr_number_from_url(pr_url)
    if not pr_number:
        return f"Error: Could not parse PR number from {pr_url}"

    # Extract task_id from branch name (expects format "nubi/{task_id}")
    task_id = _task_id_from_branch()

    # Read artifact files
    result = _read_artifact(f".nubi/{task_id}/result.json")
    gates = _read_artifact(f".nubi/{task_id}/gates.json")
    review = _read_artifact(f".nubi/{task_id}/review.json")
    monitor = _read_artifact(f".nubi/{task_id}/monitor.json")

    # Format the summary
    body = format_pipeline_summary_markdown(
        task_id=task_id,
        result=result,
        gates=gates,
        review=review,
        monitor=monitor,
    )

    # Check for existing comment
    existing = _find_existing_pipeline_comment(pr_number)
    if existing:
        _update_comment(existing["id"], body)
        return "updated"

    # Post new comment
    _post_comment(pr_number, body)
    return "posted"
