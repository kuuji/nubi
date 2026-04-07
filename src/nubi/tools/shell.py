"""Sandboxed shell execution tool for nubi agents."""

from __future__ import annotations

import re
import subprocess

from strands import tool

_workspace: str = "/workspace"

MAX_OUTPUT_LINES = 200

# Commands the agent is allowed to run. Anything not on this list is blocked.
ALLOWED_COMMANDS: set[str] = {
    # File operations
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "find",
    "grep",
    "egrep",
    "sort",
    "uniq",
    "diff",
    "cp",
    "mv",
    "mkdir",
    "rm",
    "touch",
    "chmod",
    "basename",
    "dirname",
    "realpath",
    "readlink",
    "stat",
    "file",
    "tree",
    "xargs",
    "tee",
    "tr",
    "cut",
    "sed",
    "awk",
    # Python
    "python",
    "python3",
    "pip",
    "pip3",
    "pytest",
    "ruff",
    "radon",
    "mypy",
    "black",
    "isort",
    # Node
    "node",
    "npm",
    "npx",
    "eslint",
    "jest",
    "tsc",
    # Git
    "git",
    # Text processing
    "echo",
    "printf",
    "true",
    "false",
    "test",
    "[",
    # Other safe utilities
    "date",
    "env",
    "which",
    "whoami",
    "id",
    "pwd",
    "cd",
}

# Patterns that are always blocked, even inside allowed commands.
BLOCKED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bcurl\b"),
    re.compile(r"\bwget\b"),
    re.compile(r"\bnc\b"),
    re.compile(r"\bncat\b"),
    re.compile(r"\bsocat\b"),
    re.compile(r"\bssh\b"),
    re.compile(r"\bscp\b"),
    re.compile(r"\bsftp\b"),
    re.compile(r"\btelnet\b"),
    re.compile(r"\bnslookup\b"),
    re.compile(r"\bdig\b"),
    re.compile(r"\bapt-get\b"),
    re.compile(r"\bapt\b"),
    re.compile(r"\byum\b"),
    re.compile(r"\bapk\b"),
    re.compile(r"/dev/tcp/"),
    re.compile(r"/dev/udp/"),
]

# Shell operators used to chain commands
_CMD_SPLIT_RE = re.compile(r"[|;&]+")


def _extract_commands(command: str) -> list[str]:
    """Extract the binary names from a shell command string.

    Handles pipes, &&, ||, ; chains. Strips leading env vars and paths.
    """
    parts = _CMD_SPLIT_RE.split(command)
    commands = []
    for part in parts:
        tokens = part.strip().split()
        if not tokens:
            continue
        # Skip leading env var assignments (FOO=bar cmd)
        idx = 0
        while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("="):
            idx += 1
        if idx < len(tokens):
            # Strip path prefix: /usr/bin/git → git
            cmd = tokens[idx].rsplit("/", 1)[-1]
            commands.append(cmd)
    return commands


def _validate_command(command: str) -> str | None:
    """Check if a command is allowed. Returns error message or None if allowed."""
    # Check blocked patterns first
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(command):
            return f"Blocked: command contains disallowed pattern '{pattern.pattern}'"

    # Extract and check each command in the pipeline
    cmds = _extract_commands(command)
    for cmd in cmds:
        if cmd not in ALLOWED_COMMANDS:
            return (
                f"Blocked: '{cmd}' is not in the allowed command list. "
                f"Allowed commands include: git, python, pytest, ruff, ls, grep, cat, etc."
            )

    return None


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
    error = _validate_command(command)
    if error:
        return f"[sandbox] {error}"

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
