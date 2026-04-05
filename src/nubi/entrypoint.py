"""Agent container entrypoint — clone, run agent, run gates, report results."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any

from nubi.agents.executor import create_executor_agent
from nubi.agents.gate_result import GatePolicy, GatesResult, write_gates_result
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


def _run_gates_loop(
    agent: Any,
    workspace: str,
    description: str,
    base_branch: str,
    gate_policy: GatePolicy,
    max_attempts: int,
) -> GatesResult | None:
    """Run executor + gates in a loop until gates pass or max_attempts reached.

    Each iteration:
    1. Agent does work
    2. discover_gates finds applicable gates
    3. run_gates executes them
    4. If all passed: return result
    5. If any failed: incorporate feedback, retry
    """
    from nubi.tools.gates import discover_gates, run_gates

    attempt = 1

    while attempt <= max_attempts:
        logger.info("=== Gate loop attempt %d/%d ===", attempt, max_attempts)

        logger.info("Running agent to do work...")
        response = agent(f"Complete this task:\n\n{description}")
        logger.info("Agent completed. Response preview: %s", str(response)[:500])

        # Commit any uncommitted changes the agent may have made
        subprocess.run(
            ["git", "add", "-A"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        commit_result = subprocess.run(
            ["git", "commit", "-m", f"nubi: executor attempt {attempt}"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        if commit_result.returncode == 0:
            logger.info("Committed agent changes: %s", commit_result.stdout[:200])
        else:
            logger.info("No changes to commit (or commit failed): %s", commit_result.stderr[:200])

        changed = _get_changed_files(workspace, base_branch)
        logger.info("Changed files (%d): %s", len(changed), changed)

        if not changed:
            logger.warning("Agent produced no code changes on attempt %d", attempt)
            gates_result = GatesResult(
                discovered=[],
                gates=[],
                overall_passed=False,
                attempt=attempt,
            )
            write_gates_result(gates_result, workspace)
            if attempt < max_attempts:
                logger.info("Retrying with feedback to produce code...")
                attempt += 1
                continue
            return None

        discovered = discover_gates(workspace, gate_policy, changed)
        logger.info("Discovered gates: %s", [d.name for d in discovered])

        gates_result = run_gates(discovered, workspace, gate_policy, attempt=attempt)

        for gate in gates_result.gates:
            logger.info(
                "Gate '%s' (%s): %s - %s",
                gate.name,
                gate.category.value,
                gate.status.value,
                gate.output[:200] if gate.output else "",
            )

        write_gates_result(gates_result, workspace)

        if gates_result.overall_passed:
            logger.info("All gates passed on attempt %d", attempt)
            return gates_result

        logger.info("Gates failed on attempt %d: retrying", attempt)
        attempt += 1

    logger.warning("Gates did not pass after %d attempts", max_attempts)
    return None


def main() -> int:
    """Run the executor agent end-to-end with gate verification."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    workspace = os.environ.get("NUBI_WORKSPACE", "/workspace")
    task_id = os.environ["NUBI_TASK_ID"]
    repo = os.environ["NUBI_REPO"]
    branch = os.environ["NUBI_BRANCH"]
    description = os.environ["NUBI_DESCRIPTION"]
    tools_csv = os.environ.get("NUBI_TOOLS", "shell,git,file_read,file_write,gate")
    provider = os.environ.get("NUBI_LLM_PROVIDER", "anthropic")
    token = os.environ["GITHUB_TOKEN"]
    api_key = os.environ["LLM_API_KEY"]
    max_attempts = int(os.environ.get("NUBI_MAX_ATTEMPTS", "3"))
    gate_timeout = int(os.environ.get("NUBI_GATE_TIMEOUT", "300"))

    task_branch = f"nubi/{task_id}"
    logger.info(
        "Executor starting: task=%s repo=%s base=%s branch=%s max_attempts=%d",
        task_id,
        repo,
        branch,
        task_branch,
        max_attempts,
    )

    gate_policy = GatePolicy(gate_timeout=gate_timeout)

    try:
        git_clone(repo, branch, token, workspace)

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

        gates_result = _run_gates_loop(
            agent,
            workspace,
            description,
            branch,
            gate_policy,
            max_attempts,
        )

        head_sha = _get_head_sha(workspace)
        files_changed = _get_changed_files(workspace, branch)

        if gates_result is None or not gates_result.overall_passed:
            result = ExecutorResult(
                status="failure",
                commit_sha=head_sha,
                summary=f"Gates failed after {max_attempts} attempts",
                files_changed=files_changed,
            )
        else:
            result = ExecutorResult(
                status="success",
                commit_sha=head_sha,
                summary="Task completed with all gates passing",
                files_changed=files_changed,
            )

        write_result(result, workspace)

        subprocess.run(["git", "add", ".nubi/"], cwd=workspace, check=True)
        subprocess.run(
            ["git", "commit", "-m", "nubi: add executor result and gates"],
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
            "Executor completed: sha=%s gates_passed=%s",
            head_sha,
            gates_result.overall_passed if gates_result else False,
        )
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
