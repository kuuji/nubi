"""Tests for nubi.agents.gate_result — GateResult, GateDiscovery, GatesResult models."""

from __future__ import annotations

import json

from nubi.agents.gate_result import (
    GateCategory,
    GateDiscovery,
    GateResult,
    GatesResult,
    GateStatus,
    gates_file_path,
    write_gates_result,
)

TASK_ID = "test-task-1"


class TestGateCategory:
    EXPECTED_VALUES = ["complexity", "lint", "test", "secret_scan", "diff_size"]

    def test_all_categories_exist(self) -> None:
        for value in self.EXPECTED_VALUES:
            assert GateCategory(value) == value

    def test_category_count(self) -> None:
        assert len(GateCategory) == 6

    def test_category_is_string_enum(self) -> None:
        assert isinstance(GateCategory.COMPLEXITY, str)
        assert GateCategory.COMPLEXITY == "complexity"


class TestGateStatus:
    EXPECTED_VALUES = ["passed", "failed", "skipped"]

    def test_all_statuses_exist(self) -> None:
        for value in self.EXPECTED_VALUES:
            assert GateStatus(value) == value

    def test_status_count(self) -> None:
        assert len(GateStatus) == 3

    def test_status_is_string_enum(self) -> None:
        assert isinstance(GateStatus.PASSED, str)
        assert GateStatus.PASSED == "passed"


class TestGateResult:
    def test_create_with_required_fields(self) -> None:
        result = GateResult(name="ruff", category=GateCategory.LINT, status=GateStatus.PASSED)
        assert result.name == "ruff"
        assert result.category == GateCategory.LINT
        assert result.status == GateStatus.PASSED

    def test_defaults(self) -> None:
        result = GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.FAILED)
        assert result.output == ""
        assert result.command == ""
        assert result.duration_seconds == 0.0
        assert result.error == ""

    def test_full_fields(self) -> None:
        result = GateResult(
            name="radon",
            category=GateCategory.COMPLEXITY,
            status=GateStatus.FAILED,
            output="Function too complex: cc=15",
            command="radon --max-cc 10 -j /workspace",
            duration_seconds=1.5,
            error="",
        )
        assert result.output == "Function too complex: cc=15"
        assert result.command == "radon --max-cc 10 -j /workspace"
        assert result.duration_seconds == 1.5

    def test_round_trip_json(self) -> None:
        result = GateResult(
            name="ruff",
            category=GateCategory.LINT,
            status=GateStatus.PASSED,
            output="No issues found",
            command="ruff check /workspace",
            duration_seconds=0.8,
        )
        data = json.loads(result.model_dump_json())
        result2 = GateResult.model_validate(data)
        assert result == result2

    def test_skipped_status(self) -> None:
        result = GateResult(
            name="eslint",
            category=GateCategory.LINT,
            status=GateStatus.SKIPPED,
            output="eslint not found in PATH",
        )
        assert result.status == GateStatus.SKIPPED


class TestGateDiscovery:
    def test_create_with_required_fields(self) -> None:
        discovery = GateDiscovery(name="ruff", category=GateCategory.LINT)
        assert discovery.name == "ruff"
        assert discovery.category == GateCategory.LINT

    def test_applies_to_defaults_empty(self) -> None:
        discovery = GateDiscovery(name="ruff", category=GateCategory.LINT)
        assert discovery.applies_to == []

    def test_applies_to_with_globs(self) -> None:
        discovery = GateDiscovery(
            name="ruff",
            category=GateCategory.LINT,
            applies_to=["*.py", "src/**/*.py"],
        )
        assert discovery.applies_to == ["*.py", "src/**/*.py"]

    def test_command_field(self) -> None:
        discovery = GateDiscovery(
            name="radon",
            category=GateCategory.COMPLEXITY,
            command="radon --max-cc 10 -j /workspace",
        )
        assert discovery.command == "radon --max-cc 10 -j /workspace"

    def test_round_trip_json(self) -> None:
        discovery = GateDiscovery(
            name="pytest",
            category=GateCategory.TEST,
            applies_to=["tests/**/*.py", "**/test_*.py"],
            command="pytest /workspace -v",
        )
        data = json.loads(discovery.model_dump_json())
        discovery2 = GateDiscovery.model_validate(data)
        assert discovery == discovery2


class TestGatesResult:
    def test_create_empty(self) -> None:
        result = GatesResult(discovered=[], gates=[], overall_passed=True)
        assert result.discovered == []
        assert result.gates == []
        assert result.overall_passed is True

    def test_attempt_defaults_to_one(self) -> None:
        result = GatesResult(discovered=[], gates=[], overall_passed=True)
        assert result.attempt == 1

    def test_discovered_and_gates(self) -> None:
        discovery = GateDiscovery(name="ruff", category=GateCategory.LINT)
        gate_result = GateResult(
            name="ruff",
            category=GateCategory.LINT,
            status=GateStatus.PASSED,
        )
        result = GatesResult(
            discovered=[discovery],
            gates=[gate_result],
            overall_passed=True,
        )
        assert len(result.discovered) == 1
        assert len(result.gates) == 1
        assert result.overall_passed is True

    def test_overall_passed_with_mixed_status(self) -> None:
        gate1 = GateResult(name="ruff", category=GateCategory.LINT, status=GateStatus.PASSED)
        gate2 = GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.FAILED)
        result = GatesResult(
            discovered=[],
            gates=[gate1, gate2],
            overall_passed=False,
        )
        assert result.overall_passed is False

    def test_overall_passed_with_all_skipped(self) -> None:
        gate1 = GateResult(name="eslint", category=GateCategory.LINT, status=GateStatus.SKIPPED)
        gate2 = GateResult(name="jest", category=GateCategory.TEST, status=GateStatus.SKIPPED)
        result = GatesResult(
            discovered=[],
            gates=[gate1, gate2],
            overall_passed=True,
        )
        assert result.overall_passed is True

    def test_round_trip_json(self) -> None:
        discovery = GateDiscovery(name="ruff", category=GateCategory.LINT, applies_to=["*.py"])
        gate_result = GateResult(
            name="ruff",
            category=GateCategory.LINT,
            status=GateStatus.PASSED,
            duration_seconds=0.5,
        )
        result = GatesResult(
            discovered=[discovery],
            gates=[gate_result],
            overall_passed=True,
            attempt=2,
        )
        data = json.loads(result.model_dump_json())
        result2 = GatesResult.model_validate(data)
        assert result2.attempt == 2
        assert len(result2.discovered) == 1
        assert len(result2.gates) == 1


class TestGatesFilePath:
    def test_gates_file_path_function(self) -> None:
        assert gates_file_path("my-task") == ".nubi/my-task/gates.json"


class TestWriteGatesResult:
    def test_writes_json_file(self, tmp_path: str) -> None:
        result = GatesResult(discovered=[], gates=[], overall_passed=True)
        write_gates_result(result, tmp_path, TASK_ID)
        gates_path = f"{tmp_path}/.nubi/{TASK_ID}/gates.json"
        with open(gates_path) as f:
            data = json.load(f)
        assert data["overall_passed"] is True
        assert data["discovered"] == []
        assert data["gates"] == []

    def test_creates_nubi_dir(self, tmp_path: str) -> None:
        result = GatesResult(discovered=[], gates=[], overall_passed=True)
        write_gates_result(result, tmp_path, TASK_ID)
        import os

        assert os.path.isdir(f"{tmp_path}/.nubi/{TASK_ID}")

    def test_overwrites_existing(self, tmp_path: str) -> None:
        discovery1 = GateDiscovery(name="ruff", category=GateCategory.LINT)
        gate1 = GateResult(name="ruff", category=GateCategory.LINT, status=GateStatus.FAILED)
        result1 = GatesResult(
            discovered=[discovery1], gates=[gate1], overall_passed=False, attempt=1
        )
        write_gates_result(result1, tmp_path, TASK_ID)

        discovery2 = GateDiscovery(name="pytest", category=GateCategory.TEST)
        gate2 = GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.PASSED)
        result2 = GatesResult(
            discovered=[discovery2], gates=[gate2], overall_passed=True, attempt=2
        )
        write_gates_result(result2, tmp_path, TASK_ID)

        gates_path = f"{tmp_path}/.nubi/{TASK_ID}/gates.json"
        with open(gates_path) as f:
            data = json.load(f)
        assert data["overall_passed"] is True
        assert data["attempt"] == 2

    def test_writes_complex_result(self, tmp_path: str) -> None:
        discovery = GateDiscovery(
            name="ruff",
            category=GateCategory.LINT,
            applies_to=["*.py"],
            command="ruff check /workspace",
        )
        gate_result = GateResult(
            name="ruff",
            category=GateCategory.LINT,
            status=GateStatus.PASSED,
            output="No issues found",
            command="ruff check /workspace",
            duration_seconds=0.75,
        )
        result = GatesResult(
            discovered=[discovery],
            gates=[gate_result],
            overall_passed=True,
            attempt=1,
        )
        write_gates_result(result, tmp_path, TASK_ID)
        gates_path = f"{tmp_path}/.nubi/{TASK_ID}/gates.json"
        with open(gates_path) as f:
            data = json.load(f)
        assert data["gates"][0]["name"] == "ruff"
        assert data["gates"][0]["status"] == "passed"
        assert data["discovered"][0]["category"] == "lint"
