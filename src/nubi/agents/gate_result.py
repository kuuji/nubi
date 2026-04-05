"""Gate result models and persistence."""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import BaseModel, Field

GATES_FILE_PATH = ".nubi/gates.json"


class GateCategory(StrEnum):
    COMPLEXITY = "complexity"
    LINT = "lint"
    TEST = "test"
    SECRET_SCAN = "secret_scan"
    DIFF_SIZE = "diff_size"


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class GateResult(BaseModel):
    name: str
    category: GateCategory
    status: GateStatus
    output: str = ""
    command: str = ""
    duration_seconds: float = 0.0
    error: str = ""


class GateDiscovery(BaseModel):
    name: str
    category: GateCategory
    applies_to: list[str] = Field(default_factory=list)
    command: str = ""


class GatesResult(BaseModel):
    discovered: list[GateDiscovery]
    gates: list[GateResult]
    overall_passed: bool
    attempt: int = 1


class GateThreshold(BaseModel):
    max_cc: int = 10
    max_cognitive: int = 15
    diff_lines_max: int = 500


class GatePolicy(BaseModel):
    allow: list[GateCategory] = Field(default_factory=list)
    block: list[GateCategory] = Field(default_factory=list)
    thresholds: GateThreshold = Field(default_factory=GateThreshold)
    gate_timeout: int = 300


def write_gates_result(result: GatesResult, workspace: str) -> None:
    """Write gates result JSON to {workspace}/.nubi/gates.json."""
    result_path = os.path.join(workspace, GATES_FILE_PATH)
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    with open(result_path, "w") as f:
        f.write(result.model_dump_json(indent=2))
