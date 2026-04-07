"""Review result models and persistence."""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

REVIEW_FILE_PATH = ".nubi/review.json"


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


def write_review_result(result: ReviewResult, workspace: str) -> None:
    """Write review result JSON to {workspace}/.nubi/review.json."""
    result_path = os.path.join(workspace, REVIEW_FILE_PATH)
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    with open(result_path, "w") as f:
        f.write(result.model_dump_json(indent=2))
