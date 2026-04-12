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


# ---------------------------------------------------------------------------
# Pipeline summary
# ---------------------------------------------------------------------------


def _read_artifact_json(path: str) -> dict[str, Any] | None:
    """Read a JSON artifact file from the task branch. Returns None on failure."""
    try:
        content = read_branch_file(path)
        if content.startswith("Error:") or not content.strip():
            return None
        return json.loads(content)
    except (json.JSONDecodeError, Exception):
        return None


def format_pipeline_summary(
    task_id: str,
    task_branch: str,
    result_data: dict[str, Any] | None,
    gates_data: dict[str, Any] | None,
    review_data: dict[str, Any] | None,
    monitor_data: dict[str, Any] | None,
) -> str:
    """Format pipeline data into a markdown summary comment.

    Args:
        task_id: The task identifier.
        task_branch: The full task branch name (e.g. nubi/add-feature-abc123).
        result_data: Parsed result.json (executor output).
        gates_data: Parsed gates.json.
        review_data: Parsed review.json.
        monitor_data: Parsed monitor.json.

    Returns:
        Markdown-formatted pipeline summary string.
    """
    lines: list[str] = []

    # Header
    lines.append("## Nubi Pipeline Summary\n")
    lines.append(f"**Task:** `{task_id}` · **Branch:** `{task_branch}`\n")

    # --- Executor section ---
    lines.append("### Executor\n")

    if result_data is None:
        lines.append("| | |\n|---|---|\n| Status | ⚠️ No artifact |\n")
    else:
        status = "success" if result_data.get("status") == "success" else "❌ Failed"
        commit = result_data.get("commit_sha", "")[:8]
        summary_text = result_data.get("summary", "")
        attempt = result_data.get("attempt", 1)
        files_changed = result_data.get("files_changed", [])
        files_str = f"{len(files_changed)} file(s)" if files_changed else ""

        lines.append("| | |\n|---|---|\n")
        lines.append(f"| Status | {status} |\n")
        lines.append(f"| Attempts | {attempt} |\n")
        if commit:
            lines.append(f"| Commit | `{commit}` |\n")
        if summary_text:
            lines.append(f"| Summary | {summary_text} |\n")
        if files_str:
            lines.append(f"| Files | {files_str} |\n")

    lines.append("\n### Gates\n")

    if gates_data is None:
        lines.append("| Gate | Result | Details |\n|---|---|---|\n")
        lines.append("| — | ⚠️ No artifact | — |\n")
    else:
        gates_lines = ["| Gate | Result | Details |", "|---|---|---|"]

        discovered = gates_data.get("discovered", [])
        gates_list = gates_data.get("gates", [])

        # Build a map of gate name -> result for the latest attempt
        latest: dict[str, dict[str, Any]] = {}
        for gate in gates_list:
            name = gate.get("name", "unknown")
            latest[name] = gate

        for gate_info in discovered:
            name = gate_info.get("name", "unknown")
            gate = latest.get(name, {})

            status = gate.get("status", "unknown")
            error = gate.get("error", "")
            output = gate.get("output", "")

            if status == "passed":
                icon = "✅"
                # Build concise details from output
                details = _gate_details(name, output)
            elif status == "failed":
                icon = "❌"
                details = error[:120] if error else "failed"
                if len(error) > 120:
                    details += "..."
            elif status == "skipped":
                icon = "⏭"
                details = error[:120] if error else "skipped"
            else:
                icon = "⚠️"
                details = status

            gates_lines.append(f"| {name} | {icon} {status} | {details} |")

        lines.extend(gates_lines)
        lines.append("")

        # Collapsible details for failed/skipped attempts
        failed_attempts = []
        for gate in gates_list:
            if gate.get("status") not in ("passed", "skipped"):
                continue
            gate_name = gate.get("name", "?")
            # If the gate didn't fail, check if there was a prior failed attempt
            # (gates.json stores all attempts)
            if gate.get("status") == "passed" and gate.get("attempt", 1) > 1:
                failed_attempts.append(gate_name)

        if failed_attempts:
            lines.append(
                f"<details><summary>Gate details (attempt 1 — failed)</summary>\n\n"
            )
            lines.append("| Gate | Result | Details |\n|---|---|---|\n")
            for gate in gates_list:
                if gate.get("status") == "passed":
                    continue
                name = gate.get("name", "?")
                status = gate.get("status", "failed")
                error = gate.get("error", "")
                if status == "failed":
                    icon = "❌"
                elif status == "skipped":
                    icon = "⏭"
                else:
                    icon = "⚠️"
                details = error[:120] if error else status
                lines.append(f"| {name} | {icon} {status} | {details} |\n")
            lines.append("</details>\n")

    lines.append("\n### Reviewer\n")

    if review_data is None:
        lines.append("| | |\n|---|---|\n| Decision | ⏭ Skipped |\n")
    else:
        decision = review_data.get("decision", "unknown")
        feedback = review_data.get("feedback", "")
        summary_text = review_data.get("summary", "")

        if decision == "approve":
            icon = "✅"
        elif decision == "flag":
            icon = "❌"
        elif decision == "skipped":
            icon = "⏭"
        else:
            icon = "⚠️"

        lines.append("| | |\n|---|---|\n")
        lines.append(f"| Decision | {icon} {decision.title()} |\n")
        if feedback:
            # Truncate feedback for table
            fb_short = feedback[:200]
            if len(feedback) > 200:
                fb_short += "..."
            lines.append(f"| Feedback | {fb_short} |\n")
        elif summary_text:
            lines.append(f"| Summary | {summary_text[:200]} |\n")

    lines.append("\n### Monitor\n")

    if monitor_data is None:
        lines.append("| | |\n|---|---|\n| Decision | ⏭ Skipped |\n")
    else:
        decision = monitor_data.get("decision", "unknown")
        ci_status = monitor_data.get("ci_status", "")

        if decision == "approve":
            icon = "✅"
        elif decision == "flag":
            icon = "❌"
        elif decision in ("ci-failed", "escalate"):
            icon = "❌"
        else:
            icon = "⚠️"

        lines.append("| | |\n|---|---|\n")
        lines.append(f"| Decision | {icon} {decision.replace('-', ' ').title()} |\n")

        if ci_status:
            if ci_status == "success":
                ci_icon = "✅"
            elif ci_status in ("failure", "timed_out"):
                ci_icon = "❌"
            else:
                ci_icon = "⚠️"
            lines.append(f"| CI Status | {ci_icon} {ci_status.replace('_', ' ').title()} |\n")

    # Footer
    lines.append(
        "\n---\n"
        "*Generated by [Nubi](https://github.com/kuuji/nubi) · "
        f"[pipeline artifacts](.nubi/{task_id}/)*"
    )

    return "\n".join(lines)


def _gate_details(name: str, output: str) -> str:
    """Extract a concise detail string from gate output."""
    if not output:
        return ""
    try:
        data = json.loads(output)
        if name == "radon":
            # Find the highest complexity
            max_complex = 0
            items = data if isinstance(data, list) else [data]
            for item in items:
                funcs = item if isinstance(item, list) else []
                for f in funcs:
                    if isinstance(f, dict) and "complexity" in f:
                        max_complex = max(max_complex, f["complexity"])
            if max_complex > 0:
                return f"max complexity: {max_complex}"
        elif name == "pytest":
            if isinstance(data, dict):
                passed = data.get("passed", 0)
                failed = data.get("failed", 0)
                return f"{passed} passed, {failed} failed"
            # Plain text output
            lines = output.strip().split("\n")
            for line in lines[-3:]:
                if "passed" in line or "failed" in line:
                    return line.strip()
        elif name == "ruff":
            if isinstance(data, list):
                return f"{len(data)} error(s)"
        elif name == "diff_size":
            lines = output.strip().split("\n")
            for line in lines:
                if "+" in line or "-" in line:
                    return line.strip()
        return output[:100]
    except (json.JSONDecodeError, TypeError):
        return output[:100]


def post_pipeline_summary(
    pr_url: str,
    repo: str,
    branch: str,
    token: str,
) -> str:
    """Post (or update) a structured pipeline summary comment on the PR.

    Reads the pipeline artifact files from the task branch and posts a markdown
    summary as a PR comment. On subsequent calls (retries), updates the existing
    comment instead of creating a new one.

    Args:
        pr_url: The GitHub PR URL.
        repo: Repository in "owner/name" format.
        branch: Full task branch name (e.g. "nubi/add-feature-abc123").
        token: GitHub API token.

    Returns:
        A status string indicating success or failure.
    """
    # Configure temporarily for the duration of this call
    # (read_branch_file and _task_id_from_branch rely on module globals)
    global _repo, _base_branch, _task_branch, _token
    prev_repo, prev_base, prev_branch, prev_token = _repo, _base_branch, _task_branch, _token
    _repo = repo
    _base_branch = ""  # not needed for artifact reads
    _task_branch = branch
    _token = token

    try:
        task_id = _task_id_from_branch()
        if not task_id:
            return f"Error: Could not extract task ID from branch {branch}"

        # Read all four artifact files
        result_data = _read_artifact_json(f".nubi/{task_id}/result.json")
        gates_data = _read_artifact_json(f".nubi/{task_id}/gates.json")
        review_data = _read_artifact_json(f".nubi/{task_id}/review.json")
        monitor_data = _read_artifact_json(f".nubi/{task_id}/monitor.json")

        # Format the markdown
        body = format_pipeline_summary(
            task_id=task_id,
            task_branch=branch,
            result_data=result_data,
            gates_data=gates_data,
            review_data=review_data,
            monitor_data=monitor_data,
        )

        # Add the HTML marker so we can find/update this comment on retry
        marker = "<!-- nubi-pipeline-summary -->"
        full_body = f"{marker}\n\n{body}"

        # Extract PR number
        pr_number = _pr_number_from_url(pr_url)
        if not pr_number:
            return f"Error: Could not extract PR number from {pr_url}"

        # Check for an existing comment with our marker
        existing = _find_existing_pipeline_comment(pr_number, marker)
        if existing:
            # Update existing comment
            comment_url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/comments/{existing}"
            resp = httpx.patch(
                comment_url,
                headers=_headers(),
                json={"body": full_body},
                timeout=30,
            )
            if resp.status_code == 200:
                return "Pipeline summary updated"
            return f"Error updating comment: {resp.status_code} {resp.text}"

        # Post new comment
        comment_url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
        resp = httpx.post(
            comment_url,
            headers=_headers(),
            json={"body": full_body},
            timeout=30,
        )
        if resp.status_code == 201:
            return "Pipeline summary posted"
        return f"Error posting comment: {resp.status_code} {resp.text}"

    finally:
        # Restore previous configuration
        _repo, _base_branch, _task_branch, _token = (
            prev_repo,
            prev_base,
            prev_branch,
            prev_token,
        )


def _find_existing_pipeline_comment(pr_number: int, marker: str) -> int | None:
    """Find a PR comment containing the given HTML marker.

    Returns the comment ID if found, otherwise None.
    """
    url = f"{GITHUB_API_BASE}/repos/{_repo}/issues/{pr_number}/comments"
    resp = httpx.get(url, headers=_headers(), params={"per_page": 100}, timeout=30)
    if resp.status_code != 200:
        return None

    comments = resp.json()
    for comment in comments:
        if marker in comment.get("body", ""):
            return comment.get("id")
    return None
