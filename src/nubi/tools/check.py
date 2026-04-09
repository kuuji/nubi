"""Subagent-based check tool — runs diagnostics in a separate context."""

from __future__ import annotations

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from strands import Agent, tool

from nubi.agents.logging_handler import LoggingCallbackHandler

logger = logging.getLogger(__name__)

_workspace: str = "/workspace"

# Max output lines before we consider the output "large" and need a subagent
_SHORT_OUTPUT_THRESHOLD = 20

CHECK_SYSTEM_PROMPT = """\
You are a diagnostic analysis agent. Your job is to:

1. The command has already been run. The output is provided below.
2. Read the output carefully.
3. If the output references specific files, you may use file_read to understand the code context.
4. Produce a structured summary with:
   - **Status**: PASS or FAIL
   - **Total issues**: count of distinct errors/warnings
   - **Issues** (list all, up to 20): each with file path, line number, and description
   - **Recommendation**: one-sentence fix guidance if there are failures

Keep your response concise. The parent agent will act on your findings.
Do NOT suggest code changes — only report what the tool found.\
"""


def configure(workspace: str) -> None:
    """Set the workspace root for check tool."""
    global _workspace
    _workspace = workspace


def _create_check_model() -> Any:
    """Create a model for the check subagent.

    Uses NUBI_CHECK_MODEL_ID if set, otherwise falls back to the default model.
    """
    from nubi.agents.executor import create_model

    provider = os.environ.get("NUBI_LLM_PROVIDER", "anthropic")
    api_key = os.environ.get("LLM_API_KEY", "")
    check_model_id = os.environ.get("NUBI_CHECK_MODEL_ID")

    return create_model(provider, api_key, model_id=check_model_id)


def _run_command(command: str, timeout: int) -> tuple[int, str]:
    """Run a shell command and return (exit_code, output)."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=_workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, f"Command timed out after {timeout}s"
    except Exception as exc:
        return 1, f"Command failed: {exc}"


def _analyze_with_subagent(command: str, exit_code: int, output: str) -> str:
    """Spawn a subagent to analyze command output."""
    from nubi.tools.files import file_read

    model = _create_check_model()

    subagent = Agent(
        model=model,
        tools=[file_read],
        system_prompt=CHECK_SYSTEM_PROMPT,
        callback_handler=LoggingCallbackHandler(),
    )

    prompt = (
        f"Analyze the output of this diagnostic command:\n\n"
        f"**Command:** `{command}`\n"
        f"**Exit code:** {exit_code}\n"
        f"**Output:**\n```\n{output}\n```\n\n"
        f"Provide your structured analysis."
    )

    result = subagent(prompt)
    return str(result)


def _run_single_check(command: str, timeout: int) -> str:
    """Run a single check with short-circuit for passing commands."""
    logger.info("run_check: running: %s", command)

    exit_code, output = _run_command(command, timeout)

    # Short-circuit: if command passes with minimal output, skip the subagent
    if exit_code == 0:
        output_lines = output.splitlines()
        if len(output_lines) <= _SHORT_OUTPUT_THRESHOLD:
            logger.info("run_check: PASS (short-circuit, %d lines)", len(output_lines))
            return f"**Status**: PASS\n**Command**: `{command}`\n**Output**:\n{output}"

    # Failures or large output: spawn subagent to analyze
    logger.info(
        "run_check: spawning subagent (exit_code=%d, output_lines=%d)",
        exit_code,
        len(output.splitlines()),
    )

    try:
        return _analyze_with_subagent(command, exit_code, output)
    except Exception as exc:
        error_msg = f"Subagent analysis failed for '{command}': {exc}"
        logger.error(error_msg)
        # Fall back to raw output (truncated)
        truncated = output[:3000] + ("..." if len(output) > 3000 else "")
        return f"**Status**: FAIL\n**Command**: `{command}`\n**Output**:\n{truncated}"


@tool
def run_check(command: str, timeout: int = 120) -> str:
    """Run a diagnostic command and return structured results.

    For passing commands, returns immediately. For failures, spawns a subagent
    to analyze the output in a separate context.

    Args:
        command: The diagnostic command to run (e.g. "mypy src/nubi/").
        timeout: Maximum seconds for the command. Defaults to 120.
    """
    return _run_single_check(command, timeout)


@tool
def run_checks(commands: list[str], timeout: int = 120) -> str:
    """Run multiple diagnostic commands in parallel and return all results.

    Each command runs concurrently. Passing commands return immediately;
    failures spawn subagents for analysis.

    Args:
        commands: List of diagnostic commands to run.
        timeout: Maximum seconds per command. Defaults to 120.
    """
    results: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_run_single_check, cmd, timeout): cmd for cmd in commands}
        for future in as_completed(futures):
            cmd = futures[future]
            try:
                results[cmd] = future.result()
            except Exception as exc:
                results[cmd] = f"**Status**: ERROR\n**Command**: `{cmd}`\n**Error**: {exc}"

    # Format all results in submission order
    parts: list[str] = []
    for cmd in commands:
        parts.append(f"## {cmd}\n{results.get(cmd, 'No result')}")

    return "\n\n".join(parts)
