"""Sandboxed shell execution tool for nubi agents."""

from __future__ import annotations

import subprocess

from strands import tool

_workspace: str = "/workspace"

MAX_OUTPUT_LINES = 200


def configure(workspace: str) -> None:
    """Set the workspace root for shell execution."""
    global _workspace
    _workspace = workspace


@tool
def run_shell(command: str, timeout: int = 60) -> str:
    """Execute a shell command in the workspace directory.

    Args:
        command: The shell command to execute.
        timeout: Maximum seconds to wait for the command to complete. Defaults to 60.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=_workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        lines = output.splitlines()
        if len(lines) > MAX_OUTPUT_LINES:
            output = f"[truncated — showing last {MAX_OUTPUT_LINES} of {len(lines)} lines]\n"
            output += "\n".join(lines[-MAX_OUTPUT_LINES:])
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output
    except subprocess.TimeoutExpired:
        return f"[error: command timed out after {timeout}s]"
