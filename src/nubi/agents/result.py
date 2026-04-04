"""Executor result model and persistence."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

RESULT_FILE_PATH = ".nubi/result.json"


class ExecutorResult(BaseModel):
    """Structured result written by the executor agent to the task branch."""

    status: Literal["success", "failure"]
    commit_sha: str = ""
    summary: str = ""
    files_changed: list[str] = Field(default_factory=list)
    error: str = ""


def write_result(result: ExecutorResult, workspace: str) -> None:
    """Write executor result JSON to {workspace}/.nubi/result.json."""
    result_path = os.path.join(workspace, RESULT_FILE_PATH)
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    with open(result_path, "w") as f:
        f.write(result.model_dump_json(indent=2))
