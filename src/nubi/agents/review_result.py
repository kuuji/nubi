"""Review result models and persistence."""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

REVIEW_FILE_NAME = "review.json"


def review_file_path(task_id: str) -> str:
    """Return the path for the review result file."""
    return f".nubi/{task_id}/{REVIEW_FILE_NAME}"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request-changes"
    REJECT = "reject"


class ReviewIssue(BaseModel):
    severity: Literal["critical", "major", "minor", "suggestion"]
    file: str = ""
    line: int | None = None
    description: str = ""


class ReviewResult(BaseModel):
    decision: ReviewDecision
    feedback: str = ""
    summary: str = ""
    issues: list[ReviewIssue] = Field(default_factory=list)


def write_review_result(result: ReviewResult, workspace: str, task_id: str) -> None:
    """Write review result JSON to {workspace}/.nubi/{task_id}/review.json."""
    path = os.path.join(workspace, review_file_path(task_id))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(result.model_dump_json(indent=2))
