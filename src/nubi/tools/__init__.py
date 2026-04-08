"""Tool registry — filters tools by NUBI_TOOLS env var."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nubi.tools.files import configure as configure_files
from nubi.tools.files import file_list, file_read, file_write
from nubi.tools.gates import discover_gates, run_gates
from nubi.tools.git import configure as configure_git
from nubi.tools.git import git_commit, git_diff, git_log, git_push, git_status
from nubi.tools.github_api import create_pull_request, read_branch_file, read_diff, submit_audit
from nubi.tools.github_api import list_branch_files as github_list_branch_files
from nubi.tools.review import submit_review
from nubi.tools.shell import configure as configure_shell
from nubi.tools.shell import run_shell

TOOL_GROUPS: dict[str, list[Callable[..., Any]]] = {
    "shell": [run_shell],
    "git": [git_diff, git_log, git_commit, git_push, git_status],
    "git_read": [git_diff, git_log, git_status],
    "file_read": [file_read],
    "file_write": [file_write],
    "file_list": [file_list],
    "gate": [discover_gates, run_gates],
    "review": [submit_review],
    "monitor": [
        read_branch_file,
        read_diff,
        github_list_branch_files,
        create_pull_request,
        submit_audit,
    ],
}


def get_tools(allowed: list[str], workspace: str) -> list[Callable[..., Any]]:
    """Return tool functions filtered by allowed tool names.

    Configures each tool module's workspace before returning.

    Args:
        allowed: List of tool group names (e.g., ["shell", "git", "file_read"]).
        workspace: Workspace root path to configure tools with.
    """
    configure_shell(workspace)
    configure_git(workspace)
    configure_files(workspace)

    tools: list[Callable[..., Any]] = []
    for name in allowed:
        if name in TOOL_GROUPS:
            tools.extend(TOOL_GROUPS[name])
    return tools
