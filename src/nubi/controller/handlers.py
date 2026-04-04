"""Kopf handlers for the Nubi TaskSpec controller."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from nubi.controller.credentials import ensure_stage_secret
from nubi.controller.namespace import ensure_task_namespace
from nubi.controller.sandbox import create_executor_job
from nubi.crd.schema import Phase, TaskSpecSpec
from nubi.exceptions import CredentialError, NamespaceError, SandboxError

logger = logging.getLogger(__name__)


async def on_taskspec_created(
    spec: dict[str, Any],
    name: str,
    namespace: str,
    patch: Any,
    **kwargs: Any,
) -> dict[str, str]:
    """Handle TaskSpec creation: validate, create namespace, scope creds, spawn executor."""
    task_spec = TaskSpecSpec.model_validate(spec)
    logger.info(
        "TaskSpec created: %s/%s type=%s repo=%s",
        namespace,
        name,
        task_spec.type,
        task_spec.inputs.repo,
    )

    patch.status["phase"] = Phase.PENDING
    patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()

    try:
        ns_name = await ensure_task_namespace(name, task_spec.type.value, task_spec.constraints)
    except NamespaceError:
        patch.status["phase"] = Phase.FAILED
        raise

    patch.status["workspace"] = {"namespace": ns_name}

    try:
        secret_name = await ensure_stage_secret(ns_name, name, "executor")
    except CredentialError:
        patch.status["phase"] = Phase.FAILED
        raise

    try:
        owner_uid = kwargs.get("uid", "")
        job_name = await create_executor_job(name, ns_name, task_spec, secret_name, owner_uid)
    except SandboxError:
        patch.status["phase"] = Phase.FAILED
        raise

    patch.status["phase"] = Phase.EXECUTING
    patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
    patch.status["stages"] = {"executor": {"status": "running", "attempts": 1}}

    return {"message": f"TaskSpec {name} accepted, executor job {job_name} created"}


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
