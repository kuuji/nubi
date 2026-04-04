"""Git tools for nubi agents — clone, diff, log, commit, push, status."""

from __future__ import annotations

import subprocess

from strands import tool

_workspace: str = "/workspace"


def configure(workspace: str) -> None:
    """Set the workspace root for git operations."""
    global _workspace
    _workspace = workspace


def _git(*args: str) -> str:
    """Run a git command in the workspace, return combined output."""
    result = subprocess.run(
        ["git", *args],
        cwd=_workspace,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed (exit {result.returncode}): {output.strip()}")
    return output.strip()


def git_clone(repo: str, branch: str, token: str, workspace: str) -> None:
    """Clone a GitHub repo into workspace and checkout/create the branch.

    Not a @tool — called by the entrypoint before the agent starts.
    """
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    subprocess.run(
        ["git", "clone", url, workspace],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(["git", "config", "user.email", "nubi@nubi.io"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "nubi"], cwd=workspace, check=True)
    result = subprocess.run(
        ["git", "checkout", branch],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )


@tool
def git_diff() -> str:
    """Show unstaged and staged changes in the workspace.

    Returns the output of git diff and git diff --cached.
    """
    unstaged = _git("diff")
    staged = _git("diff", "--cached")
    parts = []
    if unstaged:
        parts.append(f"Unstaged changes:\n{unstaged}")
    if staged:
        parts.append(f"Staged changes:\n{staged}")
    return "\n\n".join(parts) if parts else "No changes."


@tool
def git_log(max_count: int = 10) -> str:
    """Show recent commit history.

    Args:
        max_count: Maximum number of commits to show. Defaults to 10.
    """
    return _git("log", "--oneline", "-n", str(max_count))


@tool
def git_commit(message: str) -> str:
    """Stage all changes and create a commit.

    Args:
        message: The commit message.
    """
    _git("add", "-A")
    return _git("commit", "-m", message)


@tool
def git_push() -> str:
    """Push commits to the remote repository."""
    return _git("push", "origin", "HEAD")


@tool
def git_status() -> str:
    """Show the working tree status."""
    return _git("status")
