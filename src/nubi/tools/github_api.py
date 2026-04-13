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

# HTML marker used to identify/update the pipeline summary comment
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


def _headers(token: str | None = None) -> dict[str, str]:
    effective_token = token if token is not None else _token
    return {
        "Authorization": f"Bearer {effective_token}",
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


def _task_id_from_branch(task_branch: str | None = None) -> str:
    """Extract task ID from a task branch name."""
    branch = task_branch if task_branch is not None else _task_branch
    if branch.startswith("nubi/"):
        return branch[len("nubi/") :]
    return branch


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
    content_b64 = base64.b64decode(content_bytes).decode()

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


# ---------------------------------------------------------------------------
# Pipeline summary comment
# ---------------------------------------------------------------------------


def _artifact_base(task_id: str) -> str:
    """Return the base path for artifact files on the task branch."""
    return f".nubi/{task_id}"


def _read_artifact(path: str, repo: str, branch: str, token: str) -> dict[str, Any] | None:
    """Read a JSON artifact file from a branch, or None if missing."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{path}"
    resp = httpx.get(
        url,
        headers=_headers(token),
        params={"ref": branch},
        timeout=30,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    content_b64 = data.get("content", "")
    try:
        return json.loads(base64.b64decode(content_b64).decode())  # type: ignore[no-any-return]
    except Exception:
        return None


def _gate_status_icon(status: str | None) -> str:
    """Return an emoji icon for a gate status."""
    mapping = {
        "passed": "✅",
        "failed": "❌",
        "skipped": "⏭",
    }
    return mapping.get(status or "", "❓")


def _gate_result_label(status: str | None) -> str:
    """Return a human-readable label for a gate status."""
    mapping = {
        "passed": "pass",
        "failed": "fail",
        "skipped": "skipped",
    }
    return mapping.get(status or "", "unknown")


def _decision_icon(decision: str | None) -> str:
    """Return an emoji icon for a decision."""
    mapping = {
        "approve": "✅",
        "approved": "✅",
        "flag": "🚩",
        "flagged": "🚩",
        "ci-failed": "❌",
        "ci_failed": "❌",
        "escalate": "⚠️",
        "skipped": "⏭",
        "not_reviewed": "⏭",
    }
    return mapping.get(decision or "", "❓")


def _format_gate_details(gate: dict[str, Any]) -> str:
    """Format a single gate result into a one-line details string."""
    name = gate.get("name", gate.get("gate_name", "unknown"))
    status = gate.get("status")
    output = gate.get("output", "")

    # Extract a concise summary from gate output
    if isinstance(output, str):
        output = output.strip()
        # For diff_size, show the stat line
        if name == "diff_size":
            lines = output.splitlines()
            for line in lines:
                if "|" in line and ("+" in line or "-" in line):
                    return line.strip()
            return output[:120] if len(output) > 120 else output
        # For other gates, take first non-empty line
        first_line = output.splitlines()[0] if output.splitlines() else ""
        return first_line[:120] if len(first_line) > 120 else first_line
    return _gate_result_label(status)


def _format_gates_table(gates: list[dict[str, Any]]) -> str:
    """Format gate results as a markdown table."""
    if not gates:
        return "| Gate | Result | Details |\n|---|---|---|"

    lines = ["| Gate | Result | Details |", "|---|---|---|"]
    for gate in gates:
        name = gate.get("name", gate.get("gate_name", "unknown"))
        status = gate.get("status")
        icon = _gate_status_icon(status)
        label = _gate_result_label(status)
        result = f"{icon} {label}"
        details = _format_gate_details(gate)
        # Escape pipe characters in details
        details = details.replace("|", "\\|")
        lines.append(f"| {name} | {result} | {details} |")
    return "\n".join(lines)


def _build_executor_section(result_data: dict[str, Any] | None) -> str:
    """Build the Executor section of the pipeline summary."""
    lines = ["### Executor", "| | |", "|---|---|", "| Status |"]

    if result_data is None:
        lines.append("| ⏭ Not available |")
        lines.append("| Attempts | — |")
        lines.append("| Commit | — |")
        lines.append("| Summary | No result artifact found |")
    else:
        # Status
        overall_passed = result_data.get("overall_passed", False)
        status_icon = "✅ Complete" if overall_passed else "❌ Failed"
        lines[-1] = f"| Status | {status_icon} |"

        # Attempts — count from gates if available
        gates = result_data.get("gates", [])
        attempt = result_data.get("attempt") or (
            max((g.get("attempt", 1) for g in gates), default=1)
        )
        lines.append(f"| Attempts | {attempt} |")

        # Commit
        commit_sha = result_data.get("commit_sha", "")
        commit_short = commit_sha[:8] if commit_sha else "—"
        lines.append(f"| Commit | `{commit_short}` |")

        # Summary
        summary = result_data.get("summary", "")
        if len(summary) > 200:
            summary = summary[:200] + "..."
        lines.append(f"| Summary | {summary or '—'} |")

    return "\n".join(lines)


def _build_gates_section(result_data: dict[str, Any] | None) -> str:
    """Build the Gates section of the pipeline summary."""
    lines = ["### Gates", ""]

    if result_data is None:
        lines.append("| Gate | Result | Details |")
        lines.append("|---|---|---|")
        lines.append("| — | ⏭ No data | — |")
        return "\n".join(lines)

    gates = result_data.get("gates", [])
    if not gates:
        lines.append("| Gate | Result | Details |")
        lines.append("|---|---|---|")
        lines.append("| — | ⏭ No gates run | — |")
        return "\n".join(lines)

    # Group gates by attempt
    by_attempt: dict[int, list[dict[str, Any]]] = {}
    for gate in gates:
        attempt = gate.get("attempt", 1)
        by_attempt.setdefault(attempt, []).append(gate)

    # Latest attempt gates first
    latest_attempt = max(by_attempt.keys())
    latest_gates = by_attempt.get(latest_attempt, [])

    # Main table: latest attempt
    lines.append(_format_gates_table(latest_gates))

    # Collapsible section for earlier attempts (if any failed)
    failed_attempts = [
        (att, gate_list)
        for att, gate_list in sorted(by_attempt.items())
        if att < latest_attempt and any(g.get("status") == "failed" for g in gate_list)
    ]

    if failed_attempts:
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>Gate details (attempt 1 — failed)</summary>")
        lines.append("")
        for att, att_gates in failed_attempts:
            lines.append(f"**Attempt {att}**")
            lines.append("")
            lines.append(_format_gates_table(att_gates))
            lines.append("")
        lines.append("</details>")

    return "\n".join(lines)


def _build_reviewer_section(review_data: dict[str, Any] | None) -> str:
    """Build the Reviewer section of the pipeline summary."""
    lines = ["### Reviewer", "| | |", "|---|---|", "| Decision |"]

    if review_data is None:
        lines.append("| ⏭ Skipped |")
        lines.append("| Feedback | No review artifact found |")
    else:
        decision = review_data.get("decision", "skipped")
        lines[-1] = f"| Decision | {_decision_icon(decision)} {decision.title()} |"

        feedback = review_data.get("feedback") or review_data.get("summary") or ""
        if feedback:
            # Take first paragraph (first few sentences)
            feedback_lines = feedback.strip().split("\n")
            first_para = " ".join(
                line.strip() for line in feedback_lines if line.strip()
            )
            if len(first_para) > 300:
                first_para = first_para[:300] + "..."
            lines.append(f"| Feedback | {first_para} |")
        else:
            lines.append("| Feedback | — |")

    return "\n".join(lines)


def _build_monitor_section(monitor_data: dict[str, Any] | None) -> str:
    """Build the Monitor section of the pipeline summary."""
    lines = ["### Monitor", "| | |", "|---|---|", "| Decision |"]

    if monitor_data is None:
        lines.append("| ⏭ Skipped |")
        lines.append("| CI Status | — |")
    else:
        decision = monitor_data.get("decision", "skipped")
        lines[-1] = f"| Decision | {_decision_icon(decision)} {decision.title()} |"

        ci_status = monitor_data.get("ci_status", "")
        if ci_status:
            ci_icon = "✅" if ci_status == "success" else "❌" if ci_status else "⏭"
            lines.append(f"| CI Status | {ci_icon} {ci_status.title()} |")
        else:
            lines.append("| CI Status | — |")

    return "\n".join(lines)


def build_pipeline_summary_markdown(
    task_id: str,
    branch: str,
    result_data: dict[str, Any] | None,
    gates_data: dict[str, Any] | None,
    review_data: dict[str, Any] | None,
    monitor_data: dict[str, Any] | None,
) -> str:
    """Build the full pipeline summary markdown comment.

    Args:
        task_id: The task identifier.
        branch: The full task branch name (e.g., "nubi/task-id").
        result_data: Parsed result.json artifact (executor output).
        gates_data: Parsed gates.json artifact.
        review_data: Parsed review.json artifact.
        monitor_data: Parsed monitor.json artifact.

    Returns:
        Complete markdown string for the PR comment.
    """
    owner = _repo.split("/")[0] if _repo else "owner"
    artifact_link = f".nubi/{task_id}/"

    sections = [
        "## Nubi Pipeline Summary",
        "",
        f"**Task:** `{task_id}` · **Branch:** `{branch}`",
        "",
        _build_executor_section(result_data),
        "",
        _build_gates_section(gates_data or result_data),
        "",
        _build_reviewer_section(review_data),
        "",
        _build_monitor_section(monitor_data),
        "",
        "---",
        f"*Generated by [Nubi](https://github.com/{owner}/nubi) · "
        f"[pipeline artifacts]({artifact_link})*",
        "",
        PIPELINE_SUMMARY_MARKER,
    ]

    return "\n".join(sections)


def _find_existing_summary_comment(pr_number: int, repo: str, token: str) -> int | None:
    """Find the ID of an existing pipeline summary comment on a PR.

    Returns the comment ID if found, None otherwise.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{pr_number}/comments"
    resp = httpx.get(url, headers=_headers(token), timeout=30)
    if resp.status_code != 200:
        return None

    for comment in resp.json():
        if PIPELINE_SUMMARY_MARKER in comment.get("body", ""):
            return comment["id"]
    return None


def _post_new_comment(pr_number: int, repo: str, token: str, body: str) -> bool:
    """Post a new comment on a PR."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{pr_number}/comments"
    resp = httpx.post(
        url,
        headers=_headers(token),
        json={"body": body},
        timeout=30,
    )
    if resp.status_code == 201:
        logger.info("Posted pipeline summary comment on PR #%d", pr_number)
        return True
    logger.error(
        "Failed to post pipeline summary comment: %d %s",
        resp.status_code,
        resp.text,
    )
    return False


def _update_existing_comment(
    comment_id: int, repo: str, token: str, body: str
) -> bool:
    """Update an existing PR comment."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/issues/comments/{comment_id}"
    resp = httpx.patch(
        url,
        headers=_headers(token),
        json={"body": body},
        timeout=30,
    )
    if resp.status_code == 200:
        logger.info("Updated pipeline summary comment #%d", comment_id)
        return True
    logger.error(
        "Failed to update pipeline summary comment #%d: %d %s",
        comment_id,
        resp.status_code,
        resp.text,
    )
    return False


def post_pipeline_summary(
    pr_url: str,
    repo: str,
    branch: str,
    token: str,
) -> bool:
    """Post (or update) a structured pipeline summary comment on a PR.

    Reads the four pipeline artifact files from the task branch
    (result.json, gates.json, review.json, monitor.json) and posts
    a formatted markdown comment with tables for each pipeline stage.

    On subsequent runs, finds and updates the existing comment using
    an HTML marker rather than posting a duplicate.

    Args:
        pr_url: The GitHub PR URL (e.g. "https://github.com/owner/repo/pull/123").
        repo: The repository in "owner/name" format.
        branch: The task branch name (e.g. "nubi/task-id").
        token: GitHub API token.

    Returns:
        True if the comment was posted/updated successfully, False otherwise.
    """
    pr_number = _pr_number_from_url(pr_url)
    if not pr_number:
        logger.warning("Could not extract PR number from URL: %s", pr_url)
        return False

    task_id = _task_id_from_branch(branch)
    base = _artifact_base(task_id)

    # Read all artifact files
    result_data = _read_artifact(f"{base}/result.json", repo, branch, token)
    gates_data = _read_artifact(f"{base}/gates.json", repo, branch, token)
    review_data = _read_artifact(f"{base}/review.json", repo, branch, token)
    monitor_data = _read_artifact(f"{base}/monitor.json", repo, branch, token)

    # Build the markdown body
    markdown_body = build_pipeline_summary_markdown(
        task_id=task_id,
        branch=branch,
        result_data=result_data,
        gates_data=gates_data,
        review_data=review_data,
        monitor_data=monitor_data,
    )

    # Check for existing comment
    existing_id = _find_existing_summary_comment(pr_number, repo, token)
    if existing_id:
        return _update_existing_comment(existing_id, repo, token, markdown_body)
    return _post_new_comment(pr_number, repo, token, markdown_body)
