"""GitHub REST API tools for the monitor agent — no git clone needed."""

from __future__ import annotations

import base64
import logging
import time
from contextlib import suppress
from typing import Any

import httpx
from strands import tool

from nubi.agents.gate_result import GatesResult
from nubi.agents.monitor_result import MonitorConcern, MonitorDecision, MonitorResult
from nubi.agents.result import ExecutorResult
from nubi.agents.review_result import ReviewResult

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
        suites = [
            s for s in all_suites if s.get("app", {}).get("slug") == "github-actions"
        ]
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
# Pipeline Summary — read artifact files and post a PR comment
# ----------------------------------------------------------------------


_PIPELINE_SUMMARY_MARKER = "<!-- nubi-pipeline-summary -->"


def _read_artifact_json(path: str) -> dict[str, Any] | None:
    """Read a JSON artifact file from the task branch. Returns None on error."""
    try:
        content = read_branch_file(path)
        if content.startswith("Error:"):
            return None
        import json
        from typing import cast

        return cast(dict[str, Any], json.loads(content))
    except Exception:
        return None


def _gate_emoji(status: str) -> str:
    """Return emoji for a gate status."""
    if status == "passed":
        return "✅"
    if status == "failed":
        return "❌"
    if status == "skipped":
        return "⏭"
    return "❓"


def _decision_emoji(decision: str) -> str:
    """Return emoji for a decision."""
    if decision in ("approve", "APPROVE"):
        return "✅"
    if decision in ("request-changes", "REJECT"):
        return "❌"
    return "⚠"


def _build_executor_section(result: ExecutorResult | None, commit_sha: str) -> str:
    """Build the Executor section of the pipeline summary."""
    if result is None:
        return "### Executor\n| | |\n|---|---|\n| Status | ⚠ Skipped / Not available |\n"

    status_emoji = "✅" if result.status == "success" else "❌"
    sha = result.commit_sha or commit_sha
    short_sha = sha[:8] if sha else "N/A"
    summary = result.summary or "No summary available"
    summary_short = summary[:100] + ("..." if len(summary) > 100 else "")

    return (
        f"### Executor\n"
        f"| | |\n"
        f"|---|---|\n"
        f"| Status | {status_emoji} {result.status.title()} |\n"
        f"| Commit | `{short_sha}` |\n"
        f"| Summary | {summary_short} |\n"
    )


def _build_gates_section(gates: GatesResult | None, all_gates: list[dict[str, Any]]) -> str:
    """Build the Gates section with table and optional attempt details."""
    if gates is None and not all_gates:
        return "### Gates\n| Gate | Result | Details |\n|---|---|---|\n| — | ⚠ No gates data | |\n"

    lines = ["### Gates", "| Gate | Result | Details |", "|---|---|---|"]

    if gates:
        for gate in gates.gates:
            emoji = _gate_emoji(gate.status.value)
            details = gate.output[:80] if gate.output else gate.error[:80] if gate.error else ""
            if len(gate.output) > 80:
                details += "..."
            lines.append(f"| {gate.name} | {emoji} {gate.status.value} | {details} |")
    elif all_gates:
        # Fallback: parse raw list of gate dicts
        for gate_data in all_gates:
            name = gate_data.get("name", "?")
            status = gate_data.get("status", "unknown")
            emoji = _gate_emoji(status)
            details = gate_data.get("output", "")[:80]
            lines.append(f"| {name} | {emoji} {status} | {details} |")

    # Append collapsible section for failed attempts (attempt > 1)
    if gates and gates.attempt > 1:
        lines.append("")
        lines.append("<details>")
        lines.append(f"<summary>Gate details (attempt {gates.attempt} — failed)</summary>")
        lines.append("")
        lines.append("| Gate | Result | Details |")
        lines.append("|---|---|---|")
        for gate in gates.gates:
            emoji = _gate_emoji(gate.status.value)
            details = gate.error[:80] if gate.error else gate.output[:80]
            if len(details) > 80:
                details += "..."
            lines.append(f"| {gate.name} | {emoji} {gate.status.value} | {details} |")
        lines.append("")
        lines.append("</details>")

    return "\n".join(lines)


def _build_reviewer_section(review: ReviewResult | None) -> str:
    """Build the Reviewer section of the pipeline summary."""
    if review is None:
        return "### Reviewer\n| | |\n|---|---|\n| Decision | ⚠ Skipped |\n| Feedback | — |\n"

    emoji = _decision_emoji(review.decision.value)
    feedback = review.feedback[:200] if review.feedback else "No feedback"
    if len(review.feedback) > 200:
        feedback += "..."

    return (
        f"### Reviewer\n"
        f"| | |\n"
        f"|---|---|\n"
        f"| Decision | {emoji} {review.decision.value.title()} |\n"
        f"| Feedback | {feedback} |\n"
    )


def _build_monitor_section(monitor: MonitorResult | None) -> str:
    """Build the Monitor section of the pipeline summary."""
    if monitor is None:
        return "### Monitor\n| | |\n|---|---|\n| Decision | ⚠ Skipped |\n| CI Status | — |\n"

    emoji = _decision_emoji(monitor.decision.value)
    ci_emoji = "✅" if monitor.ci_status == "success" else "❌" if monitor.ci_status else ""
    ci_text = f"{ci_emoji} {monitor.ci_status}" if monitor.ci_status else "—"

    return (
        f"### Monitor\n"
        f"| | |\n"
        f"|---|---|\n"
        f"| Decision | {emoji} {monitor.decision.value.replace('-', ' ').title()} |\n"
        f"| CI Status | {ci_text} |\n"
    )


def format_pipeline_summary_markdown(
    executor_result: ExecutorResult | None,
    gates_result: GatesResult | None,
    review_result: ReviewResult | None,
    monitor_result: MonitorResult | None,
    task_id: str,
    branch: str,
    all_gates: list[dict[str, Any]] | None = None,
) -> str:
    """Format the pipeline data into a markdown pipeline summary comment."""
    executor_sha = (
        executor_result.commit_sha[:8] if executor_result and executor_result.commit_sha else ""
    )

    executor_md = _build_executor_section(executor_result, executor_sha)
    gates_md = _build_gates_section(gates_result, all_gates or [])
    reviewer_md = _build_reviewer_section(review_result)
    monitor_md = _build_monitor_section(monitor_result)

    md = "\n".join(
        [
            _PIPELINE_SUMMARY_MARKER,
            "## Nubi Pipeline Summary",
            "",
            f"**Task:** `{task_id}` · **Branch:** `{branch}`",
            "",
            executor_md,
            "",
            gates_md,
            "",
            reviewer_md,
            "",
            monitor_md,
            "",
            "---",
            "*Generated by [Nubi](https://github.com/kuuji/nubi)",
            f" · [pipeline artifacts](.nubi/{task_id}/)*",
        ]
    )

    return md


def _find_existing_pipeline_comment(pr_number: int) -> int | None:
    """Find the ID of an existing pipeline summary comment on a PR.

    Returns the comment ID if found, None otherwise.
    """
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.get(
        url,
        headers=_headers(),
        params={"per_page": 100},
        timeout=30,
    )
    if resp.status_code != 200:
        return None

    for comment in resp.json():
        if _PIPELINE_SUMMARY_MARKER in comment.get("body", ""):
            return int(comment["id"])
    return None


def _post_pr_comment(pr_number: int, body: str) -> bool:
    """Post a new comment on a PR. Returns True on success."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.post(
        url,
        headers=_headers(),
        json={"body": body},
        timeout=30,
    )
    return resp.status_code in (200, 201)


def _update_pr_comment(comment_id: int, body: str) -> bool:
    """Update an existing PR comment. Returns True on success."""
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/comments/{comment_id}"
    resp = httpx.patch(
        url,
        headers=_headers(),
        json={"body": body},
        timeout=30,
    )
    return resp.status_code == 200


def post_pipeline_summary(
    pr_url: str,
    repo: str,
    branch: str,
    token: str,
) -> str:
    """Post (or update) a structured pipeline summary comment on a PR.

    Reads artifact files (.nubi/{task_id}/result.json, gates.json, review.json,
    monitor.json) from the task branch and formats them into a markdown comment.

    If a pipeline summary comment already exists, updates it instead of creating
    a new one.

    Args:
        pr_url: The GitHub PR URL.
        repo: Repository in "owner/repo" format.
        branch: The full task branch name (e.g. "nubi/task-id").
        token: GitHub personal access token.

    Returns:
        A status string describing what happened.
    """
    configure(repo=repo, base_branch="", task_branch=branch, token=token)

    pr_number = _pr_number_from_url(pr_url)
    if not pr_number:
        return f"Error: could not parse PR number from {pr_url}"

    # Extract task_id from branch name
    task_id = branch
    if branch.startswith("nubi/"):
        task_id = branch[len("nubi/") :]

    # Read artifact files
    try:
        result_data = _read_artifact_json(f".nubi/{task_id}/result.json")
        gates_data = _read_artifact_json(f".nubi/{task_id}/gates.json")
        review_data = _read_artifact_json(f".nubi/{task_id}/review.json")
        monitor_data = _read_artifact_json(f".nubi/{task_id}/monitor.json")
    except Exception as e:
        return f"Error reading artifact files: {e}"

    # Parse into models
    executor_result: ExecutorResult | None = None
    if result_data:
        with suppress(Exception):
            executor_result = ExecutorResult.model_validate(result_data)

    gates_result: GatesResult | None = None
    all_gates: list[dict[str, Any]] = []
    if gates_data:
        try:
            gates_result = GatesResult.model_validate(gates_data)
        except Exception:
            # Fallback: treat as raw list
            all_gates = gates_data if isinstance(gates_data, list) else []

    review_result: ReviewResult | None = None
    if review_data:
        with suppress(Exception):
            review_result = ReviewResult.model_validate(review_data)

    monitor_result: MonitorResult | None = None
    if monitor_data:
        with suppress(Exception):
            monitor_result = MonitorResult.model_validate(monitor_data)

    # Format markdown
    body = format_pipeline_summary_markdown(
        executor_result=executor_result,
        gates_result=gates_result,
        review_result=review_result,
        monitor_result=monitor_result,
        task_id=task_id,
        branch=branch,
        all_gates=all_gates,
    )

    # Check for existing comment
    existing_id = _find_existing_pipeline_comment(pr_number)

    if existing_id:
        success = _update_pr_comment(existing_id, body)
        if success:
            return f"Pipeline summary updated: comment {existing_id}"
        return f"Error updating comment {existing_id}"

    success = _post_pr_comment(pr_number, body)
    if success:
        return "Pipeline summary posted"
    return "Error posting pipeline summary comment"
