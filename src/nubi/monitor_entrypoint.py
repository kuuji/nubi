"""Monitor container entrypoint — audit workflow via GitHub API, create PR if approved."""

from __future__ import annotations

import base64
import logging
import os
import sys

from nubi.agents.monitor import create_monitor_agent
from nubi.agents.monitor_result import MonitorDecision, MonitorResult
from nubi.tools.github_api import (
    configure as configure_github,
)
from nubi.tools.github_api import (
    create_pull_request,
    get_audit_result,
    list_branch_files,
    mark_pr_ready,
    poll_ci_checks,
    post_pipeline_summary,
    read_branch_file,
    read_diff,
    submit_audit,
    update_pr_from_url,
    write_monitor_result_to_branch,
)

logger = logging.getLogger(__name__)

# Tools passed to the agent — keep references so ruff doesn't remove them
_MONITOR_TOOLS = [read_branch_file, read_diff, list_branch_files, submit_audit]


def main() -> int:
    """Run the monitor agent end-to-end. Always returns 0 (graceful degradation)."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    try:
        task_id = os.environ["NUBI_TASK_ID"]
        repo = os.environ["NUBI_REPO"]
        branch = os.environ["NUBI_BRANCH"]
        description = os.environ["NUBI_DESCRIPTION"]
        provider = os.environ.get("NUBI_LLM_PROVIDER", "anthropic")
        token = os.environ["GITHUB_TOKEN"]
        api_key = os.environ["LLM_API_KEY"]

        task_branch = f"nubi/{task_id}"

        # Decode pod logs if provided
        pod_logs_b64 = os.environ.get("NUBI_POD_LOGS", "")
        pod_logs = ""
        if pod_logs_b64:
            try:
                pod_logs = base64.b64decode(pod_logs_b64).decode(errors="replace")
            except Exception:
                logger.warning("Failed to decode NUBI_POD_LOGS, continuing without logs")

        # PR config from spec
        pr_title_prefix = os.environ.get("NUBI_PR_TITLE_PREFIX", "nubi:")

        logger.info(
            "Monitor starting: task=%s repo=%s base=%s branch=%s",
            task_id,
            repo,
            branch,
            task_branch,
        )

        # Configure GitHub API tools
        configure_github(
            repo=repo,
            base_branch=branch,
            task_branch=task_branch,
            token=token,
        )

        agent = create_monitor_agent(
            tools=list(_MONITOR_TOOLS),
            description=description,
            repo=repo,
            base_branch=branch,
            task_branch=task_branch,
            pod_logs=pod_logs,
            provider=provider,
            api_key=api_key,
        )

        # Create draft PR first so the agent audits the PR diff (correctly
        # scoped by GitHub to only the changes this PR would introduce),
        # not a branch compare that can include unrelated commits.
        pr_title = f"{pr_title_prefix} {description[:60]}"
        pr_body = ""  # Placeholder — updated after audit
        pr_url = ""

        result = create_pull_request(title=pr_title, body=pr_body, draft=True)
        logger.info("PR creation result: %s", result)
        if "PR created:" in result:
            pr_url = result.split("PR created: ", 1)[1].strip()
        elif "PR updated:" in result:
            pr_url = result.split("PR updated: ", 1)[1].strip()

        logger.info("Running monitor agent...")
        agent(
            f"Audit the pipeline workflow for task branch {task_branch} "
            f"against {branch} for the following task:\n\n{description}"
        )

        audit = get_audit_result()
        if audit is None:
            logger.warning("Agent did not call submit_audit on first pass; re-prompting...")
            agent(
                "You must call the submit_audit tool now with your decision. "
                "Choose approve or flag based on your analysis."
            )
            audit = get_audit_result()

        if audit is None:
            logger.warning("Agent did not call submit_audit after retry; defaulting to approve")
            audit = MonitorResult(
                decision=MonitorDecision.APPROVE,
                summary="Monitor agent did not produce a structured audit; defaulting to approve.",
            )

        logger.info("Audit decision: %s", audit.decision.value)
        audit.pr_url = pr_url

        # Update PR with audit body and mark ready if approved
        if pr_url:
            pr_body = _build_pr_body(description, audit)
            update_pr_from_url(pr_url, pr_title, pr_body)

            if audit.decision == MonitorDecision.APPROVE:
                mark_pr_ready(pr_url)

            # Poll CI checks
            ci_timeout = int(os.environ.get("NUBI_CI_TIMEOUT", "600"))
            ci_poll = int(os.environ.get("NUBI_CI_POLL_INTERVAL", "30"))
            logger.info("Polling CI checks (timeout=%ds)...", ci_timeout)
            ci_status, ci_feedback = poll_ci_checks(
                timeout_seconds=ci_timeout,
                poll_interval=ci_poll,
            )
            logger.info("CI status: %s", ci_status)
            if ci_status == "timed_out":
                logger.warning("CI checks timed out — not retrying")
                audit = MonitorResult(
                    decision=MonitorDecision.ESCALATE,
                    summary="CI checks timed out — needs human investigation",
                    pr_url=pr_url,
                    ci_status=ci_status,
                    ci_feedback=ci_feedback,
                )
            elif ci_status != "success":
                audit = MonitorResult(
                    decision=MonitorDecision.CI_FAILED,
                    summary=f"CI checks {ci_status}",
                    pr_url=pr_url,
                    ci_status=ci_status,
                    ci_feedback=ci_feedback,
                )

            # Re-update PR body with final decision and CI results
            pr_body = _build_pr_body(description, audit)
            update_pr_from_url(pr_url, pr_title, pr_body)

        # Write monitor result to the task branch
        write_monitor_result_to_branch(audit)

        # Post pipeline summary comment on the PR
        if pr_url:
            logger.info("Posting pipeline summary comment...")
            summary_result = post_pipeline_summary(
                pr_url=pr_url,
                repo=repo,
                base_branch=branch,
                token=token,
                task_id=task_id,
            )
            logger.info("Pipeline summary: %s", summary_result)

        logger.info(
            "Monitor completed: decision=%s summary=%s",
            audit.decision.value,
            audit.summary[:200],
        )
        return 0

    except Exception:
        logger.exception("Monitor failed — graceful degradation, exiting 0")
        return 0


def _build_pr_body(description: str, audit: MonitorResult) -> str:
    """Build the PR description body."""
    lines: list[str] = []

    # Decision badge
    decision_labels = {
        "approve": "Approved",
        "flag": "Flagged",
        "ci-failed": "CI Failed",
        "escalate": "Escalated",
    }
    label = decision_labels.get(audit.decision.value, audit.decision.value)
    lines.append(f"> **Pipeline decision:** {label}")
    lines.append("")

    # Use the monitor's rich PR summary if available, otherwise fall back
    if audit.pr_summary:
        pr_text = audit.pr_summary.strip()
        if not pr_text.startswith("#"):
            lines.append("## Summary")
        lines.append(pr_text)
    else:
        lines.extend(["## Summary", audit.summary or description])

    if audit.concerns:
        lines.extend(["", "## Concerns"])
        for c in audit.concerns:
            lines.append(f"- **[{c.severity}/{c.area}]** {c.description}")

    if audit.ci_feedback:
        lines.extend(["", "## CI Feedback", audit.ci_feedback])

    lines.extend(
        [
            "",
            "---",
            "*Generated by [Nubi](https://github.com/kuuji/nubi) pipeline*",
        ]
    )

    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
