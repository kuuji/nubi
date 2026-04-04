"""Workspace-scoped file tools for nubi agents."""

from __future__ import annotations

import os
from pathlib import Path

from strands import tool

_workspace: str = "/workspace"


def configure(workspace: str) -> None:
    """Set the workspace root for all file tools."""
    global _workspace
    _workspace = workspace


def _validate_path(path: str) -> Path:
    """Resolve path relative to workspace, rejecting traversal and absolute paths."""
    if os.path.isabs(path):
        raise ValueError(f"Absolute paths not allowed: {path}")
    resolved = (Path(_workspace).resolve() / path).resolve()
    if not str(resolved).startswith(str(Path(_workspace).resolve())):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


@tool
def file_read(path: str) -> str:
    """Read a file from the workspace.

    Args:
        path: File path relative to the workspace root.
    """
    resolved = _validate_path(path)
    return resolved.read_text()


@tool
def file_write(path: str, content: str) -> str:
    """Write content to a file in the workspace.

    Args:
        path: File path relative to the workspace root.
        content: The content to write to the file.
    """
    resolved = _validate_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content)
    return f"Wrote {len(content)} bytes to {path}"


@tool
def file_list(path: str = ".") -> str:
    """List files and directories in the workspace.

    Args:
        path: Directory path relative to workspace root. Defaults to workspace root.
    """
    resolved = _validate_path(path)
    if not resolved.is_dir():
        raise ValueError(f"Not a directory: {path}")
    entries = sorted(str(e.relative_to(resolved)) for e in resolved.iterdir())
    return "\n".join(entries)
