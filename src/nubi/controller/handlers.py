"""Kopf handler stubs for the Nubi TaskSpec controller."""

from __future__ import annotations

import logging
from typing import Any

from nubi.crd.schema import Phase, TaskSpecSpec

logger = logging.getLogger(__name__)


async def on_taskspec_created(
    spec: dict[str, Any],
    name: str,
    namespace: str,
    patch: Any,
    **kwargs: Any,
) -> dict[str, str]:
    """Handle TaskSpec creation: validate and set initial phase."""
    task_spec = TaskSpecSpec.model_validate(spec)
    logger.info(
        "TaskSpec created: %s/%s type=%s repo=%s",
        namespace,
        name,
        task_spec.type,
        task_spec.inputs.repo,
    )

    patch.status["phase"] = Phase.PENDING

    # TODO: Create task namespace
    # TODO: Create git branch
    # TODO: Spawn executor job

    return {"message": f"TaskSpec {name} accepted"}


async def on_job_status_change(
    name: str,
    namespace: str,
    status: dict[str, Any],
    labels: dict[str, str],
    **kwargs: Any,
) -> None:
    """Handle Job status changes for nubi-labeled Jobs."""
    task_id = labels.get("nubi.io/task-id", "unknown")
    stage = labels.get("nubi.io/stage", "unknown")

    logger.info(
        "Job %s/%s changed (task=%s, stage=%s)",
        namespace,
        name,
        task_id,
        stage,
    )

    # TODO: Check if job succeeded or failed
    # TODO: Advance pipeline phase or handle failure
    # TODO: Spawn next stage job
