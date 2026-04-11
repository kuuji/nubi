"""Monitor result models and persistence."""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

MONITOR_FILE_NAME = "monitor.json"


def monitor_file_path(task_id: str) -> str:
    """Return the path for the monitor result file."""
    return f".nubi/{task_id}/{MONITOR_FILE_NAME}"


class MonitorDecision(StrEnum):
    APPROVE = "approve"
    FLAG = "flag"
    CI_FAILED = "ci-failed"
    ESCALATE = "escalate"


class MonitorConcern(BaseModel):
    severity: Literal["critical", "major", "minor"]
    area: Literal["process", "output", "security"]
    description: str = ""


class MonitorResult(BaseModel):
    decision: MonitorDecision
    summary: str = ""
    pr_summary: str = ""
    concerns: list[MonitorConcern] = Field(default_factory=list)
    pr_url: str = ""
    ci_status: str = ""
    ci_feedback: str = ""


def write_monitor_result(result: MonitorResult, workspace: str, task_id: str) -> None:
    """Write monitor result JSON to {workspace}/.nubi/{task_id}/monitor.json."""
    path = os.path.join(workspace, monitor_file_path(task_id))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(result.model_dump_json(indent=2))
