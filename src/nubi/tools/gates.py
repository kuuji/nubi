"""Gate tools for discovering and running deterministic gates."""

from __future__ import annotations

import json
import subprocess
import time
from shutil import which

from strands import tool

from nubi.agents.gate_result import (
    GateCategory,
    GateDiscovery,
    GatePolicy,
    GateResult,
    GatesResult,
    GateStatus,
)

MAX_OUTPUT_LENGTH = 5000

PYTHON_TOOLS: dict[str, list[str]] = {
    "lint": ["ruff", "ruff check"],
    "test": ["pytest"],
    "complexity": ["radon", "radon cc -j"],
}
NODE_TOOLS: dict[str, list[str]] = {
    "lint": ["eslint"],
    "test": ["jest"],
}
TERRAFORM_TOOLS: dict[str, list[str]] = {
    "lint": ["terraform validate"],
}


@tool
def discover_gates(
    workspace: str,
    gate_policy: GatePolicy,
    changed_files: list[str],
) -> list[GateDiscovery]:
    """Discover which gates apply based on changed files and gate policy.

    Args:
        workspace: Path to the workspace directory.
        gate_policy: Gate policy controlling which gates are allowed/blocked.
        changed_files: List of changed files to evaluate.

    Returns:
        List of GateDiscovery objects for applicable gates.
    """
    discoveries: list[GateDiscovery] = []

    allow_list = gate_policy.allow
    block_list = gate_policy.block

    def is_allowed(category: GateCategory) -> bool:
        if block_list and category in block_list:
            return False
        return not (allow_list and category not in allow_list)

    python_discoveries = _discover_python_gates(changed_files, workspace)
    for disc in python_discoveries:
        if is_allowed(disc.category):
            discoveries.append(disc)

    node_discoveries = _discover_node_gates(changed_files, workspace)
    for disc in node_discoveries:
        if is_allowed(disc.category):
            discoveries.append(disc)

    if changed_files and is_allowed(GateCategory.DIFF_SIZE):
        diff_size_disc = _discover_diff_size_gate(changed_files, workspace)
        discoveries.append(diff_size_disc)

    return discoveries


@tool
def run_gates(
    discovered: list[GateDiscovery],
    workspace: str,
    gate_policy: GatePolicy,
    attempt: int = 1,
) -> GatesResult:
    """Run discovered gates sequentially, stopping on first failure.

    Args:
        discovered: List of gate discoveries to run.
        workspace: Path to the workspace directory.
        gate_policy: Gate policy with thresholds and timeout settings.
        attempt: Current gate attempt number.

    Returns:
        GatesResult with per-gate results and overall pass/fail status.
    """
    results: list[GateResult] = []
    timeout = gate_policy.gate_timeout

    for disc in discovered:
        gate_result = _run_single_gate(disc, workspace, gate_policy, timeout)
        results.append(gate_result)
        if gate_result.status == GateStatus.FAILED:
            break

    all_passed = all(r.status in (GateStatus.PASSED, GateStatus.SKIPPED) for r in results)
    return GatesResult(
        discovered=discovered,
        gates=results,
        overall_passed=all_passed,
        attempt=attempt,
    )


def _discover_python_gates(changed: list[str], workspace: str) -> list[GateDiscovery]:
    """Discover applicable Python gates based on changed files and tools."""
    discoveries: list[GateDiscovery] = []

    py_patterns = [".py", "tests/", "src/"]
    if not any(any(p in f for p in py_patterns) for f in changed):
        return discoveries

    if which("ruff"):
        discoveries.append(
            GateDiscovery(
                name="ruff",
                category=GateCategory.LINT,
                applies_to=["*.py", "src/**/*.py", "tests/**/*.py"],
            )
        )

    if which("pytest"):
        discoveries.append(
            GateDiscovery(
                name="pytest",
                category=GateCategory.TEST,
                applies_to=["tests/**/*.py", "**/test_*.py"],
            )
        )

    if which("radon"):
        discoveries.append(
            GateDiscovery(
                name="radon",
                category=GateCategory.COMPLEXITY,
                applies_to=["*.py", "src/**/*.py"],
            )
        )

    return discoveries


def _discover_node_gates(changed: list[str], workspace: str) -> list[GateDiscovery]:
    """Discover applicable Node gates based on changed files and tools."""
    discoveries: list[GateDiscovery] = []

    js_patterns = [".js", ".ts", "tests/", "src/"]
    if not any(any(p in f for p in js_patterns) for f in changed):
        return discoveries

    if which("eslint"):
        discoveries.append(
            GateDiscovery(
                name="eslint",
                category=GateCategory.LINT,
                applies_to=["*.js", "*.ts", "src/**/*.js", "src/**/*.ts"],
            )
        )

    if which("jest"):
        discoveries.append(
            GateDiscovery(
                name="jest",
                category=GateCategory.TEST,
                applies_to=["**/*.test.js", "**/*.test.ts", "tests/**/*.js"],
            )
        )

    return discoveries


def _discover_diff_size_gate(changed: list[str], workspace: str) -> GateDiscovery:
    """Discover diff_size gate (always applicable when files change)."""
    return GateDiscovery(
        name="diff_size",
        category=GateCategory.DIFF_SIZE,
        applies_to=["*"],
    )


def _run_single_gate(
    discovery: GateDiscovery,
    workspace: str,
    gate_policy: GatePolicy,
    timeout: int,
) -> GateResult:
    """Run a single gate and return the result."""
    name = discovery.name
    category = discovery.category
    start_time = time.time()

    if category == GateCategory.LINT:
        return _run_lint_gate(name, workspace, timeout, start_time)
    elif category == GateCategory.TEST:
        return _run_test_gate(name, workspace, timeout, start_time)
    elif category == GateCategory.COMPLEXITY:
        return _run_complexity_gate(name, workspace, gate_policy, timeout, start_time)
    elif category == GateCategory.DIFF_SIZE:
        return _run_diff_size_gate(name, workspace, gate_policy, timeout, start_time)
    else:
        return GateResult(
            name=name,
            category=category,
            status=GateStatus.SKIPPED,
            output="Unknown gate category",
            duration_seconds=time.time() - start_time,
        )


def _run_lint_gate(name: str, workspace: str, timeout: int, start_time: float) -> GateResult:
    """Run a lint gate (ruff, eslint)."""
    if not which(name):
        return GateResult(
            name=name,
            category=GateCategory.LINT,
            status=GateStatus.SKIPPED,
            output=f"{name} not found in PATH",
            duration_seconds=time.time() - start_time,
        )

    if name == "ruff":
        cmd = f"ruff check {workspace} --output-format=concise"
    else:
        cmd = f"{name} {workspace}"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start_time
        output = _truncate_output(result.stdout + result.stderr)

        if result.returncode == 0:
            return GateResult(
                name=name,
                category=GateCategory.LINT,
                status=GateStatus.PASSED,
                output=output,
                command=cmd,
                duration_seconds=duration,
            )
        else:
            return GateResult(
                name=name,
                category=GateCategory.LINT,
                status=GateStatus.FAILED,
                output=output,
                command=cmd,
                duration_seconds=duration,
            )
    except subprocess.TimeoutExpired:
        return GateResult(
            name=name,
            category=GateCategory.LINT,
            status=GateStatus.FAILED,
            output=f"{name} timed out after {timeout}s",
            command=cmd,
            duration_seconds=time.time() - start_time,
            error="timeout",
        )
    except Exception as e:
        return GateResult(
            name=name,
            category=GateCategory.LINT,
            status=GateStatus.FAILED,
            output=str(e),
            command=cmd,
            duration_seconds=time.time() - start_time,
            error=str(e),
        )


def _run_test_gate(name: str, workspace: str, timeout: int, start_time: float) -> GateResult:
    """Run a test gate (pytest, jest)."""
    if not which(name):
        return GateResult(
            name=name,
            category=GateCategory.TEST,
            status=GateStatus.SKIPPED,
            output=f"{name} not found in PATH",
            duration_seconds=time.time() - start_time,
        )

    cmd = f"pytest {workspace} -v --tb=short" if name == "pytest" else f"{name} {workspace}"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start_time
        output = _truncate_output(result.stdout + result.stderr)

        if result.returncode == 0:
            return GateResult(
                name=name,
                category=GateCategory.TEST,
                status=GateStatus.PASSED,
                output=output,
                command=cmd,
                duration_seconds=duration,
            )
        else:
            return GateResult(
                name=name,
                category=GateCategory.TEST,
                status=GateStatus.FAILED,
                output=output,
                command=cmd,
                duration_seconds=duration,
            )
    except subprocess.TimeoutExpired:
        return GateResult(
            name=name,
            category=GateCategory.TEST,
            status=GateStatus.FAILED,
            output=f"{name} timed out after {timeout}s",
            command=cmd,
            duration_seconds=time.time() - start_time,
            error="timeout",
        )
    except Exception as e:
        return GateResult(
            name=name,
            category=GateCategory.TEST,
            status=GateStatus.FAILED,
            output=str(e),
            command=cmd,
            duration_seconds=time.time() - start_time,
            error=str(e),
        )


def _run_complexity_gate(
    name: str, workspace: str, gate_policy: GatePolicy, timeout: int, start_time: float
) -> GateResult:
    """Run a complexity gate (radon)."""
    if not which(name):
        return GateResult(
            name=name,
            category=GateCategory.COMPLEXITY,
            status=GateStatus.SKIPPED,
            output=f"{name} not found in PATH",
            duration_seconds=time.time() - start_time,
        )

    max_cc = gate_policy.thresholds.max_cc
    cmd = f"radon cc -j {workspace}"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start_time
        output = _truncate_output(result.stdout + result.stderr)

        if result.returncode == 0:
            try:
                # radon cc -j outputs {filename: [{name, complexity, ...}, ...]}
                file_results = json.loads(result.stdout)
                if isinstance(file_results, dict):
                    for _file, functions in file_results.items():
                        if isinstance(functions, list):
                            for func in functions:
                                cc = func.get("complexity", 0)
                                if isinstance(cc, (int, float)) and cc > max_cc:
                                    return GateResult(
                                        name=name,
                                        category=GateCategory.COMPLEXITY,
                                        status=GateStatus.FAILED,
                                        output=output,
                                        command=cmd,
                                        duration_seconds=duration,
                                    )
                return GateResult(
                    name=name,
                    category=GateCategory.COMPLEXITY,
                    status=GateStatus.PASSED,
                    output=output,
                    command=cmd,
                    duration_seconds=duration,
                )
            except json.JSONDecodeError:
                return GateResult(
                    name=name,
                    category=GateCategory.COMPLEXITY,
                    status=GateStatus.FAILED,
                    output=f"Failed to parse radon output: {output}",
                    command=cmd,
                    duration_seconds=duration,
                )
        else:
            return GateResult(
                name=name,
                category=GateCategory.COMPLEXITY,
                status=GateStatus.FAILED,
                output=output,
                command=cmd,
                duration_seconds=duration,
            )
    except subprocess.TimeoutExpired:
        return GateResult(
            name=name,
            category=GateCategory.COMPLEXITY,
            status=GateStatus.FAILED,
            output=f"{name} timed out after {timeout}s",
            command=cmd,
            duration_seconds=time.time() - start_time,
            error="timeout",
        )
    except Exception as e:
        return GateResult(
            name=name,
            category=GateCategory.COMPLEXITY,
            status=GateStatus.FAILED,
            output=str(e),
            command=cmd,
            duration_seconds=time.time() - start_time,
            error=str(e),
        )


def _run_diff_size_gate(
    name: str, workspace: str, gate_policy: GatePolicy, timeout: int, start_time: float
) -> GateResult:
    """Run a diff_size gate, checking total changed lines against threshold."""
    base_ref = f"origin/{gate_policy.base_branch}"
    diff_cmd = f"git diff --stat {base_ref}..HEAD"
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", f"{base_ref}..HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start_time
        output = _truncate_output(result.stdout)

        diff_lines_max = gate_policy.thresholds.diff_lines_max
        try:
            last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            if last_line:
                parts = last_line.split()
                for i, part in enumerate(parts):
                    if part.isdigit() and i > 0 and parts[i - 1] in ("+", "-"):
                        total_changes = int(part)
                        if total_changes > diff_lines_max:
                            exceeded_msg = (
                                f"Exceeded diff_lines_max: {total_changes} > {diff_lines_max}"
                            )
                            msg = f"{output}\n{exceeded_msg}"
                            return GateResult(
                                name=name,
                                category=GateCategory.DIFF_SIZE,
                                status=GateStatus.FAILED,
                                output=msg,
                                command=diff_cmd,
                                duration_seconds=duration,
                            )
                        break
        except (IndexError, ValueError):
            pass

        return GateResult(
            name=name,
            category=GateCategory.DIFF_SIZE,
            status=GateStatus.PASSED,
            output=output,
            command=diff_cmd,
            duration_seconds=duration,
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            name=name,
            category=GateCategory.DIFF_SIZE,
            status=GateStatus.FAILED,
            output=f"git diff timed out after {timeout}s",
            command=diff_cmd,
            duration_seconds=time.time() - start_time,
            error="timeout",
        )
    except Exception as e:
        return GateResult(
            name=name,
            category=GateCategory.DIFF_SIZE,
            status=GateStatus.FAILED,
            output=str(e),
            command=diff_cmd,
            duration_seconds=time.time() - start_time,
            error=str(e),
        )


def _truncate_output(output: str) -> str:
    """Truncate output to MAX_OUTPUT_LENGTH characters."""
    if len(output) <= MAX_OUTPUT_LENGTH:
        return output
    return (
        output[:MAX_OUTPUT_LENGTH]
        + f"\n[truncated - {len(output) - MAX_OUTPUT_LENGTH} characters omitted]"
    )
