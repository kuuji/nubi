"""Reviewer container entrypoint — clone, review diff, submit result, push."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading

from nubi.agents.review_result import ReviewDecision, ReviewResult, write_review_result
from nubi.agents.reviewer import create_reviewer_agent
from nubi.tools import get_tools
from nubi.tools.git import git_clone
from nubi.tools.review import get_review_result

logger = logging.getLogger(__name__)


def main() -> int:
    """Run the reviewer agent end-to-end."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    timeout = int(os.environ.get("NUBI_TIMEOUT", "0"))
    if timeout > 0:

        def _timeout_handler() -> None:
            logger.error("Reviewer timed out after %ds", timeout)
            os.kill(os.getpid(), signal.SIGTERM)

        timer = threading.Timer(timeout, _timeout_handler)
        timer.daemon = True
        timer.start()

    workspace = os.environ.get("NUBI_WORKSPACE", "/workspace")
    task_id = os.environ["NUBI_TASK_ID"]
    repo = os.environ["NUBI_REPO"]
    branch = os.environ["NUBI_BRANCH"]
    description = os.environ["NUBI_DESCRIPTION"]
    review_focus_csv = os.environ.get("NUBI_REVIEW_FOCUS", "")
    tools_csv = os.environ.get("NUBI_TOOLS", "shell,git_read,file_read,file_list,review")
    provider = os.environ.get("NUBI_LLM_PROVIDER", "anthropic")
    token = os.environ["GITHUB_TOKEN"]
    api_key = os.environ["LLM_API_KEY"]

    task_branch = f"nubi/{task_id}"
    review_focus = [f.strip() for f in review_focus_csv.split(",") if f.strip()]

    logger.info(
        "Reviewer starting: task=%s repo=%s base=%s branch=%s",
        task_id,
        repo,
        branch,
        task_branch,
    )

    try:
        git_clone(repo, branch, token, workspace)

        # Fetch and checkout the task branch (executor already pushed it)
        subprocess.run(
            ["git", "fetch", "origin", task_branch],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "checkout", task_branch],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )

        allowed_tools = [t.strip() for t in tools_csv.split(",") if t.strip()]
        tools = get_tools(allowed_tools, workspace)

        agent = create_reviewer_agent(
            tools=tools,
            description=description,
            repo=repo,
            base_branch=branch,
            task_branch=task_branch,
            review_focus=review_focus,
            provider=provider,
            api_key=api_key,
        )

        logger.info("Running reviewer agent...")
        agent(
            f"Review the code changes on branch {task_branch} "
            f"against {branch} for the following task:\n\n{description}"
        )

        review = get_review_result()
        if review is None:
            logger.warning("Agent did not call submit_review on first pass; re-prompting...")
            agent(
                "You must call the submit_review tool now with your decision. "
                "Choose approve, request-changes, or reject based on your analysis."
            )
            review = get_review_result()

        if review is None:
            logger.warning("Agent did not call submit_review after retry; defaulting to reject")
            review = ReviewResult(
                decision=ReviewDecision.REJECT,
                feedback="Reviewer agent did not produce a structured review result.",
                summary="Review failed — no decision submitted",
            )

        logger.info("Review decision: %s", review.decision.value)

        write_review_result(review, workspace, task_id)

        subprocess.run(["git", "add", f".nubi/{task_id}/"], cwd=workspace, check=True)
        subprocess.run(
            ["git", "commit", "-m", "nubi: add reviewer result"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )

        push_result = subprocess.run(
            ["git", "push", "--force-with-lease", "origin", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        if push_result.returncode != 0:
            logger.error(
                "Git push failed (exit %d): stdout=%s stderr=%s",
                push_result.returncode,
                push_result.stdout,
                push_result.stderr,
            )
            raise RuntimeError(f"git push failed: {push_result.stderr}")

        logger.info(
            "Reviewer completed: decision=%s summary=%s",
            review.decision.value,
            review.summary[:200],
        )
        return 0

    except Exception:
        logger.exception("Reviewer failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
