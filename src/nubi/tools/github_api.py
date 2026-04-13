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


# ----------------------------------------------------------------------
# Pipeline Summary
# ----------------------------------------------------------------------


def _read_artifact(path: str, default: Any = None) -> Any:
    """Read and parse a JSON artifact file from the task branch.

    Returns the parsed dict or `default` if the file cannot be read.
    """
    try:
        raw = _read_branch_file_raw(path)
        return json.loads(raw) if raw else default
    except Exception:
        return default


def _read_branch_file_raw(path: str) -> str:
    """Read raw content of a file from the task branch via GitHub Contents API."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/contents/{path}"
    resp = httpx.get(url, headers=_headers(), params={"ref": _task_branch}, timeout=30)
    if resp.status_code != 200:
        return ""
    data = resp.json()
    content_b64 = data.get("content", "")
    return base64.b64decode(content_b64).decode()


def _status_emoji(status: str) -> str:
    """Return an emoji for a given status string."""
    mapping = {
        "success": "✅",
        "passed": "✅",
        "failed": "❌",
        "skipped": "⏭",
        "approve": "✅",
        "rejected": "❌",
        "reject": "❌",
        "request-changes": "❌",
        "timed_out": "⏭",
        "ci-failed": "❌",
        "escalate": "⚠️",
    }
    return mapping.get(status.lower(), "❓")


def _gate_emoji(status: str) -> str:
    """Return a pass/fail/skip emoji for a gate status."""
    mapping = {
        "passed": "✅ pass",
        "failed": "❌ fail",
        "skipped": "⏭ skipped",
    }
    return mapping.get(status.lower(), f"{status}")


def _human_readable_status(status: str) -> str:
    """Return a human-readable label for a status string.

    Replaces underscores with spaces and applies title case.
    """
    return status.replace("_", " ").title()


def _build_pipeline_summary_markdown(
    task_id: str,
    result_data: dict[str, Any] | None,
    gates_data: dict[str, Any] | None,
    review_data: dict[str, Any] | None,
    monitor_data: dict[str, Any] | None,
    ci_status: str,
) -> str:
    """Build the markdown body for the pipeline summary comment."""
    lines: list[str] = []

    lines.append("## Nubi Pipeline Summary\n")
    lines.append(f"**Task:** `{task_id}` · **Branch:** `{_task_branch}`")
    lines.append("")

    # ---- Executor ----
    lines.append("### Executor")
    lines.append("| | |")
    lines.append("|---|---|")
    if result_data:
        status_val = result_data.get("status", "unknown")
        commit = result_data.get("commit_sha", "")
        commit_short = commit[:7] if commit else "N/A"
        summary_text = result_data.get("summary", "")
        lines.append(
            f"| Status | {_status_emoji(status_val)} {_human_readable_status(status_val)} |"
        )
        lines.append(f"| Commit | `{commit_short}` |")
        lines.append(f"| Summary | {summary_text} |")
    else:
        lines.append("| Status | ❓ Not found |")
    lines.append("")

    # ---- Gates ----
    lines.append("### Gates")
    if gates_data and gates_data.get("gates"):
        gates = gates_data["gates"]
        lines.append("| Gate | Result | Details |")
        lines.append("|---|---|---|")
        for gate in gates:
            name = gate.get("name", "?")
            status = gate.get("status", "?")
            output = gate.get("output", "")
            # Truncate long output
            if output and len(output) > 200:
                output = output[:200] + "..."
            lines.append(f"| {name} | {_gate_emoji(status)} | {output} |")

        # Show attempt count if > 1
        attempt = gates_data.get("attempt", 1)
        if attempt > 1:
            lines.append(f"\n**Attempts:** {attempt}")
    else:
        lines.append("No gate data found.")
    lines.append("")

    # ---- Reviewer ----
    lines.append("### Reviewer")
    lines.append("| | |")
    lines.append("|---|---|")
    if review_data:
        decision = review_data.get("decision", "unknown")
        feedback = review_data.get("feedback", "")
        if feedback and len(feedback) > 300:
            feedback = feedback[:300] + "..."
        lines.append(f"| Decision | {_status_emoji(decision)} {_human_readable_status(decision)} |")
        lines.append(f"| Feedback | {feedback} |")
    else:
        lines.append("| Decision | ⏭ Skipped |")
        lines.append("| Feedback | — |")
    lines.append("")

    # ---- Monitor ----
    lines.append("### Monitor")
    lines.append("| | |")
    lines.append("|---|---|")
    if monitor_data:
        decision = monitor_data.get("decision", "unknown")
        lines.append(f"| Decision | {_status_emoji(decision)} {_human_readable_status(decision)} |")
    else:
        lines.append("| Decision | ❓ Not found |")
    ci_emoji = _status_emoji(ci_status) if ci_status else "❓"
    ci_label = _human_readable_status(ci_status) if ci_status else "Unknown"
    lines.append(f"| CI Status | {ci_emoji} {ci_label} |")
    lines.append("")

    # Footer
    lines.append("---")
    lines.append(
        f"*Generated by [Nubi](https://github.com/kuuji/nubi) · "
        f"[pipeline artifacts](.nubi/{task_id}/)*"
    )
    lines.append("<!-- nubi-pipeline-summary -->")

    return "\n".join(lines)


def post_pipeline_summary(
    pr_url: str,
    repo: str,
    branch: str,
    token: str,
) -> str:
    """Post (or update) a structured pipeline summary comment on a PR.

    Reads the four artifact files from the branch (.nubi/{task_id}/) and
    posts a markdown comment summarizing executor, gates, reviewer, and
    monitor stages.

    On retries, finds and updates the existing comment using the
    ``<!-- nubi-pipeline-summary -->`` HTML marker.

    Args:
        pr_url: The PR URL (e.g. https://github.com/owner/repo/pull/123).
        repo: GitHub repository in "owner/repo" form.
        branch: The task branch name (e.g. "nubi/task-id").
        token: GitHub personal access token.

    Returns:
        A status string describing the result.
    """
    global _repo, _task_branch, _token

    _repo = repo
    _task_branch = branch
    _token = token

    pr_number = _pr_number_from_url(pr_url)
    if not pr_number:
        return f"Error: Could not extract PR number from URL: {pr_url}"

    # Extract task_id from branch name
    task_id = _task_id_from_branch_from_branch(branch)

    # Read artifact files
    base = f".nubi/{task_id}"
    result_data = _read_artifact(f"{base}/result.json")
    gates_data = _read_artifact(f"{base}/gates.json")
    review_data = _read_artifact(f"{base}/review.json")
    monitor_data = _read_artifact(f"{base}/monitor.json")

    # Build markdown body
    body = _build_pipeline_summary_markdown(
        task_id=task_id,
        result_data=result_data,
        gates_data=gates_data,
        review_data=review_data,
        monitor_data=monitor_data,
        ci_status="success",  # CI status is determined in monitor_entrypoint
    )

    # Find existing comment to update
    existing_id = _find_existing_pipeline_comment(pr_number)
    if existing_id:
        return _update_pr_comment(pr_number, existing_id, body)
    else:
        return _post_pr_comment(pr_number, body)


def _task_id_from_branch_from_branch(branch: str) -> str:
    """Extract task ID from a branch name."""
    if branch.startswith("nubi/"):
        return branch[len("nubi/") :]
    return branch


def _find_existing_pipeline_comment(pr_number: int) -> int | None:
    """Find the ID of an existing pipeline summary comment on a PR."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return None
    for comment in resp.json():
        if "<!-- nubi-pipeline-summary -->" in comment.get("body", ""):
            return comment["id"]
    return None


def _post_pr_comment(pr_number: int, body: str) -> str:
    """Post a new comment on a PR."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.post(
        url,
        headers=_headers(),
        json={"body": body},
        timeout=30,
    )
    if resp.status_code == 201:
        return f"Pipeline summary posted: comment {resp.json()['id']}"
    return f"Error posting comment: {resp.status_code} {resp.text}"


def _update_pr_comment(pr_number: int, comment_id: int, body: str) -> str:
    """Update an existing PR comment."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/comments/{comment_id}"
    resp = httpx.patch(
        url,
        headers=_headers(),
        json={"body": body},
        timeout=30,
    )
    if resp.status_code == 200:
        return f"Pipeline summary updated: comment {comment_id}"
    return f"Error updating comment: {resp.status_code} {resp.text}"
