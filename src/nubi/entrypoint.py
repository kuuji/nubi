"""Agent container entrypoint — clone, run agent, report results."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from nubi.agents.executor import create_executor_agent
from nubi.agents.result import ExecutorResult, write_result
from nubi.tools import get_tools
from nubi.tools.git import git_clone

logger = logging.getLogger(__name__)


def _get_head_sha(workspace: str) -> str:
    """Get the current HEAD commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=workspace,
    )
    return result.stdout.strip()


def _get_changed_files(workspace: str, branch: str) -> list[str]:
    """Get list of files changed relative to the original branch."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"origin/{branch}..HEAD"],
        capture_output=True,
        text=True,
        cwd=workspace,
    )
    return [f for f in result.stdout.strip().splitlines() if f]


def main() -> int:
    """Run the executor agent end-to-end."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    workspace = os.environ.get("NUBI_WORKSPACE", "/workspace")
    task_id = os.environ["NUBI_TASK_ID"]
    repo = os.environ["NUBI_REPO"]
    branch = os.environ["NUBI_BRANCH"]
    description = os.environ["NUBI_DESCRIPTION"]
    tools_csv = os.environ.get("NUBI_TOOLS", "shell,git,file_read,file_write")
    provider = os.environ.get("NUBI_LLM_PROVIDER", "anthropic")
    token = os.environ["GITHUB_TOKEN"]
    api_key = os.environ["LLM_API_KEY"]

    task_branch = f"nubi/{task_id}"
    logger.info(
        "Executor starting: task=%s repo=%s base=%s branch=%s",
        task_id,
        repo,
        branch,
        task_branch,
    )

    try:
        git_clone(repo, branch, token, workspace)

        # Create task branch off the base branch — agent never pushes to main
        subprocess.run(
            ["git", "checkout", "-b", task_branch],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )

        allowed_tools = [t.strip() for t in tools_csv.split(",") if t.strip()]
        tools = get_tools(allowed_tools, workspace)

        agent = create_executor_agent(
            tools=tools,
            description=description,
            repo=repo,
            base_branch=branch,
            task_branch=task_branch,
            provider=provider,
            api_key=api_key,
        )

        response = agent(f"Complete this task:\n\n{description}")

        head_sha = _get_head_sha(workspace)
        files_changed = _get_changed_files(workspace, branch)

        result = ExecutorResult(
            status="success",
            commit_sha=head_sha,
            summary=str(response),
            files_changed=files_changed,
        )
        write_result(result, workspace)

        # Commit and push the result file
        subprocess.run(["git", "add", ".nubi/result.json"], cwd=workspace, check=True)
        subprocess.run(
            ["git", "commit", "-m", "nubi: add executor result"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "push", "origin", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )

        logger.info("Executor completed successfully: sha=%s", head_sha)
        return 0

    except Exception:
        logger.exception("Executor failed")
        try:
            result = ExecutorResult(
                status="failure",
                error=str(sys.exc_info()[1]),
            )
            write_result(result, workspace)
            subprocess.run(["git", "add", ".nubi/result.json"], cwd=workspace)
            subprocess.run(
                ["git", "commit", "-m", "nubi: add executor failure result"],
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "push", "origin", "HEAD"],
                cwd=workspace,
                capture_output=True,
                text=True,
            )
        except Exception:
            logger.exception("Failed to write failure result")
        return 1


if __name__ == "__main__":
    sys.exit(main())
