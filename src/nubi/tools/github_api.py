"""GitHub REST API tools for the monitor agent — no git clone needed."""

from __future__ import annotations

import base64
import json
import logging
import time
from contextlib import suppress
from typing import Any

import httpx
from strands import tool

from nubi.agents.gate_result import GatesResult, GateStatus
from nubi.agents.monitor_result import MonitorConcern, MonitorDecision, MonitorResult
from nubi.agents.result import ExecutorResult
from nubi.agents.review_result import ReviewDecision, ReviewResult

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


def _read_branch_file_raw(path: str) -> dict[str, Any] | None:
    """Read a JSON file from the task branch, returning parsed dict or None on error."""
    try:
        content = read_branch_file(path)
        if content.startswith("Error:"):
            return None
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None


def _artifact_path(task_id: str, filename: str) -> str:
    """Return the artifact file path for a given task_id and filename."""
    return f".nubi/{task_id}/{filename}"


def _format_gate_status(status: GateStatus) -> tuple[str, str]:
    """Return (emoji, label) for a gate status."""
    mapping = {
        GateStatus.PASSED: ("✅", "pass"),
        GateStatus.FAILED: ("❌", "fail"),
        GateStatus.SKIPPED: ("⏭", "skipped"),
    }
    return mapping.get(status, ("❓", "unknown"))


def _format_review_decision(decision: ReviewDecision) -> tuple[str, str]:
    """Return (emoji, label) for a review decision."""
    mapping = {
        ReviewDecision.APPROVE: ("✅", "Approve"),
        ReviewDecision.REQUEST_CHANGES: ("🔄", "Request Changes"),
        ReviewDecision.REJECT: ("❌", "Reject"),
    }
    return mapping.get(decision, ("❓", "Unknown"))


def _build_pipeline_summary_markdown(
    task_id: str,
    branch: str,
    executor: ExecutorResult | None,
    gates: GatesResult | None,
    review: ReviewResult | None,
    monitor: MonitorResult | None,
) -> str:
    """Build the pipeline summary markdown from artifact data.

    Args:
        task_id: The task identifier.
        branch: The full task branch name.
        executor: Executor result data.
        gates: Gates result data.
        review: Review result data.
        monitor: Monitor result data.

    Returns:
        Formatted markdown string.
    """
    lines: list[str] = []

    # Header
    lines.append("## Nubi Pipeline Summary")
    lines.append("")
    lines.append(f"**Task:** `{task_id}` · **Branch:** `{branch}`")
    lines.append("")

    # Executor section
    lines.append("### Executor")
    lines.append("| | |")
    lines.append("|---|---|")
    if executor:
        status_label = "Complete" if executor.status == "success" else "Failed"
        lines.append(f"| Status | ✅ {status_label} |")
        lines.append(f"| Commit | `{executor.commit_sha[:8]}` |")
        if executor.summary:
            summary_text = executor.summary[:100] + ("..." if len(executor.summary) > 100 else "")
            lines.append(f"| Summary | {summary_text} |")
    else:
        lines.append("| Status | ⏭ Skipped |")
    lines.append("")

    # Gates section
    lines.append("### Gates")
    lines.append("| Gate | Result | Details |")
    lines.append("|---|---|---|")

    if gates and gates.gates:
        for gate in gates.gates:
            emoji, label = _format_gate_status(gate.status)
            details_parts = []
            if gate.output:
                # Truncate output to avoid very long details
                output_preview = gate.output[:50].replace("\n", " ")
                if len(gate.output) > 50:
                    output_preview += "..."
                details_parts.append(output_preview)
            if gate.error:
                details_parts.append(f"error: {gate.error[:30]}")
            details = "; ".join(details_parts) if details_parts else "-"
            lines.append(f"| {gate.name} | {emoji} {label} | {details} |")
    else:
        lines.append("| - | ⏭ Skipped | - |")
    lines.append("")

    # Reviewer section
    lines.append("### Reviewer")
    lines.append("| | |")
    lines.append("|---|---|")
    if review:
        emoji, label = _format_review_decision(review.decision)
        lines.append(f"| Decision | {emoji} {label} |")
        if review.feedback:
            feedback_text = review.feedback[:100].replace("\n", " ")
            if len(review.feedback) > 100:
                feedback_text += "..."
            lines.append(f"| Feedback | {feedback_text} |")
    else:
        lines.append("| Decision | ⏭ Skipped |")
    lines.append("")

    # Monitor section
    lines.append("### Monitor")
    lines.append("| | |")
    lines.append("|---|---|")
    if monitor:
        decision_labels = {
            MonitorDecision.APPROVE: "Approve",
            MonitorDecision.FLAG: "Flag",
            MonitorDecision.CI_FAILED: "CI Failed",
            MonitorDecision.ESCALATE: "Escalate",
        }
        label = decision_labels.get(monitor.decision, monitor.decision.value)
        emoji = "✅" if monitor.decision == MonitorDecision.APPROVE else "🔄"
        lines.append(f"| Decision | {emoji} {label} |")
        if monitor.ci_status:
            ci_emoji = "✅" if monitor.ci_status == "success" else "❌"
            lines.append(f"| CI Status | {ci_emoji} {monitor.ci_status} |")
    else:
        lines.append("| Decision | ⏭ Skipped |")

    # Footer
    lines.append("")
    lines.append("---")
    nubi_link = "[Nubi](https://github.com/kuuji/nubi)"
    lines.append(f"*Generated by {nubi_link} · [pipeline artifacts](.nubi/{task_id}/)*")

    return "\n".join(lines)


def _find_existing_summary_comment(pr_number: int) -> int | None:
    """Find an existing pipeline summary comment by HTML marker.

    Returns the comment ID if found, None otherwise.
    """
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    page = 1
    while True:
        resp = httpx.get(
            url,
            headers=_headers(),
            params={"page": page, "per_page": 100},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        comments = resp.json()
        if not comments:
            return None
        for comment in comments:
            if "<!-- nubi-pipeline-summary -->" in comment.get("body", ""):
                return comment["id"]
        page += 1
        if page > 10:  # Safety limit
            return None


def _update_pr_comment(comment_id: int, body: str) -> None:
    """Update an existing PR comment."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/comments/{comment_id}"
    httpx.patch(
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
    """Post a pipeline summary comment on the PR.

    Reads the four artifact files (result.json, gates.json, review.json,
    monitor.json) from the branch and formats them into a structured markdown
    comment. If a summary comment already exists, updates it instead of
    creating a new one.

    Args:
        pr_url: The GitHub PR URL.
        repo: The repository in "owner/repo" format.
        branch: The task branch name (e.g., "nubi/add-rate-limiting-a1b2c3").
        token: GitHub API token.

    Returns:
        Success or error message.
    """
    # Declare global usage to avoid UnboundLocalError
    global _repo, _token, _task_branch

    # Configure temporarily for read_branch_file calls
    original_repo = _repo
    original_token = _token
    original_branch = _task_branch

    _repo = repo
    _token = token
    _task_branch = branch

    try:
        # Extract task_id from branch
        task_id = branch
        if branch.startswith("nubi/"):
            task_id = branch[len("nubi/") :]

        # Read artifact files
        executor_data = _read_branch_file_raw(_artifact_path(task_id, "result.json"))
        gates_data = _read_branch_file_raw(_artifact_path(task_id, "gates.json"))
        review_data = _read_branch_file_raw(_artifact_path(task_id, "review.json"))
        monitor_data = _read_branch_file_raw(_artifact_path(task_id, "monitor.json"))

        # Parse into models if data exists
        executor: ExecutorResult | None = None
        if executor_data:
            with suppress(Exception):
                executor = ExecutorResult.model_validate(executor_data)

        gates: GatesResult | None = None
        if gates_data:
            with suppress(Exception):
                gates = GatesResult.model_validate(gates_data)

        review: ReviewResult | None = None
        if review_data:
            with suppress(Exception):
                review = ReviewResult.model_validate(review_data)

        monitor: MonitorResult | None = None
        if monitor_data:
            with suppress(Exception):
                monitor = MonitorResult.model_validate(monitor_data)

        # Build markdown
        markdown = _build_pipeline_summary_markdown(
            task_id, branch, executor, gates, review, monitor
        )

        # Add HTML marker for update detection
        body = f"<!-- nubi-pipeline-summary -->\n{markdown}"

        # Get PR number
        pr_number = _pr_number_from_url(pr_url)
        if not pr_number:
            return f"Error: Could not extract PR number from {pr_url}"

        # Check for existing comment
        existing_id = _find_existing_summary_comment(pr_number)

        # Post or update comment
        if existing_id:
            _update_pr_comment(existing_id, body)
            return f"Pipeline summary updated on PR #{pr_number}"
        else:
            url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{pr_number}/comments"
            resp = httpx.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={"body": body},
                timeout=30,
            )
            if resp.status_code in (200, 201):
                return f"Pipeline summary posted on PR #{pr_number}"
            return f"Error posting comment: {resp.status_code} {resp.text}"

    finally:
        # Restore original config
        _repo = original_repo
        _token = original_token
        _task_branch = original_branch


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
