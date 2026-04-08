"""Executor result model and persistence."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

RESULT_FILE_NAME = "result.json"


def result_file_path(task_id: str) -> str:
    """Return the path for the executor result file."""
    return f".nubi/{task_id}/{RESULT_FILE_NAME}"


class ExecutorResult(BaseModel):
    """Structured result written by the executor agent to the task branch."""

    status: Literal["success", "failure"]
    commit_sha: str = ""
    summary: str = ""
    files_changed: list[str] = Field(default_factory=list)
    error: str = ""


def write_result(result: ExecutorResult, workspace: str, task_id: str) -> None:
    """Write executor result JSON to {workspace}/.nubi/{task_id}/result.json."""
    path = os.path.join(workspace, result_file_path(task_id))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(result.model_dump_json(indent=2))
