"""Git tools for nubi agents — clone, diff, log, commit, push, status."""

from __future__ import annotations

import re
import subprocess

from strands import tool

_workspace: str = "/workspace"

_OWNER_REPO_SEGMENT = r"[A-Za-z0-9._-]+"
_GITHUB_URL_RE = re.compile(
    rf"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>{_OWNER_REPO_SEGMENT})/"
    rf"(?P<repo>{_OWNER_REPO_SEGMENT}?)(?:\.git)?/?$"
)
_OWNER_REPO_RE = re.compile(rf"^(?P<owner>{_OWNER_REPO_SEGMENT})/(?P<repo>{_OWNER_REPO_SEGMENT})$")


def normalize_repo(repo: str) -> str:
    """Normalize a repo identifier to owner/repo format.

    Accepts: 'owner/repo', 'https://github.com/owner/repo',
    'https://github.com/owner/repo.git', etc.

    Rejects SSH URLs, non-GitHub hosts, and anything that doesn't
    look like a single owner/repo pair.
    """
    repo = repo.strip()
    m = _GITHUB_URL_RE.match(repo)
    if m:
        return f"{m.group('owner')}/{m.group('repo')}"
    # Bare owner/repo form — allow an optional .git suffix.
    bare = repo.removesuffix(".git")
    if _OWNER_REPO_RE.match(bare):
        return bare
    raise ValueError(f"Invalid repo format: {repo!r} — expected 'owner/repo' or a GitHub URL")


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
    repo = normalize_repo(repo)
    # safe.directory is handled via GIT_CONFIG_* env vars set in the container spec.
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    result = subprocess.run(
        ["git", "clone", url, workspace],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Sanitize output to avoid leaking the token in logs/tracebacks
        sanitized = result.stderr.replace(token, "***")
        raise RuntimeError(f"git clone failed: {sanitized}")
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
def git_commit(message: str, files: list[str] | None = None) -> str:
    """Stage changes and create a commit.

    Args:
        message: The commit message.
        files: Specific files/directories to stage. If omitted, stages all changes.
    """
    if files:
        _git("add", "--", *files)
    else:
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
