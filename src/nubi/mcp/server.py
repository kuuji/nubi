"""Nubi MCP Server — FastMCP server exposing TaskSpec operations.

This server provides MCP tools for managing Nubi TaskSpec resources:
- create_taskspec: Create a new task
- list_tasks: List tasks with optional phase filter
- get_task_status: Get detailed task status
- get_task_logs: Read pod logs for a task stage
- delete_taskspec: Delete a task
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from nubi.crd.schema import TaskSpecSpec
from nubi.mcp import k8s

# Server configuration
SERVER_NAME = "nubi"
DEFAULT_PORT = 8080


def _get_port() -> int:
    """Get the port from environment variable or use default."""
    port_str = os.environ.get("NUBI_MCP_PORT", str(DEFAULT_PORT))
    try:
        return int(port_str)
    except ValueError:
        return DEFAULT_PORT


mcp = FastMCP(SERVER_NAME, port=_get_port())


@mcp.tool()
def create_taskspec(
    name: Annotated[str, "TaskSpec name (must be DNS-compatible)"],
    spec: Annotated[dict[str, Any], "Full TaskSpec spec as JSON object"],
    namespace: Annotated[str, "Kubernetes namespace"] = "nubi-system",
) -> str:
    """Create a new TaskSpec custom resource.

    The spec must include at minimum:
    - description (str): Task description
    - type (str): One of code-change, research, refactor, docs
    - inputs (dict): Must include 'repo' and optionally 'branch'

    Optional fields: constraints, review, loop_policy, output, monitoring
    """
    # Validate the spec using Pydantic
    try:
        TaskSpecSpec.model_validate(spec)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = ".".join(str(part) for part in err["loc"])
            msg = err["msg"]
            errors.append(f"  - {loc}: {msg}")
        return "Validation error:\n" + "\n".join(errors)

    try:
        k8s.create_taskspec(name=name, namespace=namespace, spec=spec)
        return f"TaskSpec '{name}' created successfully in namespace '{namespace}'."
    except Exception as e:
        return f"Error creating TaskSpec: {e}"


@mcp.tool()
def list_tasks(
    namespace: Annotated[str, "Namespace to list from"] = "nubi-system",
    phase: Annotated[str, "Optional phase filter (Pending, Executing, etc.)"] = "",
) -> str:
    """List TaskSpec resources with optional phase filtering.

    Returns a formatted table with task name, type, phase, and age.
    """
    try:
        tasks = k8s.list_taskspecs(namespace=namespace, phase=phase)
    except Exception as e:
        return f"Error listing tasks: {e}"

    if not tasks:
        return "No tasks found."

    # Build table header
    header = f"{'NAME':<30} {'TYPE':<15} {'PHASE':<15} {'AGE':<20}"
    separator = "-" * 80
    lines = [header, separator]

    for task in tasks:
        metadata = task.get("metadata", {})
        spec = task.get("spec", {})
        status = task.get("status", {})

        task_name = metadata.get("name", "unknown")
        task_type = spec.get("type", "unknown")
        task_phase = status.get("phase", "Unknown")
        age = metadata.get("creationTimestamp", "unknown")

        lines.append(f"{task_name:<30} {task_type:<15} {task_phase:<15} {age:<20}")

    return "\n".join(lines)


@mcp.tool()
def get_task_status(
    name: Annotated[str, "TaskSpec name"],
    namespace: Annotated[str, "Namespace"] = "nubi-system",
) -> str:
    """Get detailed status of a specific TaskSpec.

    Returns formatted status including phase, workspace info (branch, headSHA),
    and all stage statuses (executor, validator, reviewer, gating).
    """
    try:
        task = k8s.get_taskspec(name=name, namespace=namespace)
    except Exception as e:
        return f"Error getting task status: {e}"

    spec = task.get("spec", {})
    status = task.get("status", {})
    stages = status.get("stages", {})
    workspace = status.get("workspace", {})

    lines = [
        f"TaskSpec: {name}",
        f"Namespace: {namespace}",
        f"Phase: {status.get('phase', 'Unknown')}",
        f"Phase Changed At: {status.get('phaseChangedAt', 'N/A')}",
        "",
        "Workspace:",
        f"  Namespace: {workspace.get('namespace', 'N/A')}",
        f"  Repo: {workspace.get('repo', 'N/A')}",
        f"  Branch: {workspace.get('branch', 'N/A')}",
        f"  Head SHA: {workspace.get('headSHA', 'N/A')}",
        "",
        "Task Info:",
        f"  Description: {spec.get('description', 'N/A')}",
        f"  Type: {spec.get('type', 'N/A')}",
        "",
        "Stages:",
    ]

    # Executor stage
    executor = stages.get("executor", {})
    lines.append("  Executor:")
    lines.append(f"    Status: {executor.get('status', 'pending')}")
    lines.append(f"    Attempts: {executor.get('attempts', 0)}")
    lines.append(f"    Commit SHA: {executor.get('commitSHA', 'N/A')}")
    lines.append(f"    Summary: {executor.get('summary', 'N/A')}")

    # Validator stage
    validator = stages.get("validator", {})
    lines.append("  Validator:")
    lines.append(f"    Status: {validator.get('status', 'pending')}")
    det = validator.get("deterministic", {})
    lines.append(f"    Lint: {det.get('lint', 'N/A')}")
    lines.append(f"    Tests: {det.get('tests', 'N/A')}")
    lines.append(f"    Secret Scan: {det.get('secret_scan', 'N/A')}")
    lines.append(f"    Test Commit SHA: {validator.get('testCommitSHA', 'N/A')}")

    # Reviewer stage
    reviewer = stages.get("reviewer", {})
    lines.append("  Reviewer:")
    lines.append(f"    Status: {reviewer.get('status', 'pending')}")
    lines.append(f"    Decision: {reviewer.get('decision', 'N/A')}")
    lines.append(f"    Feedback: {reviewer.get('feedback', 'N/A')}")

    # Monitor stage
    monitor = stages.get("monitor", {})
    lines.append("  Monitor:")
    lines.append(f"    Status: {monitor.get('status', 'pending')}")
    lines.append(f"    Decision: {monitor.get('decision', 'N/A')}")
    lines.append(f"    Summary: {monitor.get('summary', 'N/A')}")
    lines.append(f"    PR URL: {monitor.get('prURL', 'N/A')}")

    # Gating stage
    gating = stages.get("gating", {})
    lines.append("  Gating:")
    lines.append(f"    Status: {gating.get('status', 'pending')}")
    lines.append(f"    Passed: {gating.get('passed', False)}")
    lines.append(f"    Attempt: {gating.get('attempt', 0)}")

    return "\n".join(lines)


@mcp.tool()
def get_task_logs(
    name: Annotated[str, "TaskSpec name"],
    stage: Annotated[str, "Stage name: executor, reviewer, or monitor"],
    namespace: Annotated[str, "Namespace"] = "nubi-system",
) -> str:
    """Read pod logs for a specific stage of a task.

    The task namespace is derived from the task name (nubi-{name}).
    Finds pods with label nubi.io/stage={stage}.
    Returns the last 200 lines of logs.
    """
    valid_stages = {"executor", "reviewer", "monitor"}
    if stage not in valid_stages:
        return f"Invalid stage '{stage}'. Must be one of: {', '.join(valid_stages)}"

    try:
        logs = k8s.get_pod_logs(name=name, namespace=namespace, stage=stage)
        return f"Logs for task '{name}' stage '{stage}':\n\n{logs}"
    except Exception as e:
        return f"Error getting logs: {e}"


@mcp.tool()
def delete_taskspec(
    name: Annotated[str, "TaskSpec name to delete"],
    namespace: Annotated[str, "Namespace"] = "nubi-system",
) -> str:
    """Delete a TaskSpec resource (cancel a running task)."""
    try:
        k8s.delete_taskspec(name=name, namespace=namespace)
        return f"TaskSpec '{name}' deleted successfully from namespace '{namespace}'."
    except Exception as e:
        return f"Error deleting TaskSpec: {e}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
