"""Kopf handlers for the Nubi TaskSpec controller."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import kopf

from nubi.controller.credentials import ensure_stage_secret
from nubi.controller.namespace import ensure_task_namespace
from nubi.controller.results import read_executor_result
from nubi.controller.sandbox import create_executor_job
from nubi.crd.defaults import CREDENTIAL_GITHUB_TOKEN, MASTER_SECRET_NAME, MASTER_SECRET_NAMESPACE
from nubi.crd.schema import Phase, TaskSpecSpec
from nubi.exceptions import CredentialError, NamespaceError, ResultError, SandboxError

logger = logging.getLogger(__name__)


@kopf.on.create("taskspecs", group="nubi.io", version="v1")  # type: ignore[arg-type]
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
        job_name = await create_executor_job(name, ns_name, task_spec, secret_name)
    except SandboxError:
        patch.status["phase"] = Phase.FAILED
        raise

    patch.status["phase"] = Phase.EXECUTING
    patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
    patch.status["stages"] = {"executor": {"status": "running", "attempts": 1}}

    return {"message": f"TaskSpec {name} accepted, executor job {job_name} created"}


@kopf.on.event("jobs", group="batch", version="v1", labels={"app.kubernetes.io/managed-by": "nubi"})  # type: ignore[arg-type]
async def on_job_status_change(
    name: str,
    namespace: str,
    status: dict[str, Any],
    labels: dict[str, str],
    patch: Any,
    spec: dict[str, Any] | None = None,
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

    # Determine if job succeeded or failed from status conditions
    conditions = status.get("conditions", [])
    succeeded = any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions)
    failed = any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)

    if not succeeded and not failed:
        return  # Job still running

    if stage != "executor":
        return  # Only handle executor stage for now (v0.1)

    if failed:
        patch.status["phase"] = Phase.FAILED
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status.setdefault("stages", {})["executor"] = {
            "status": "failed",
            "summary": "Job failed",
        }
        logger.warning("Executor job %s/%s failed", namespace, name)
        return

    # Job succeeded — read result from GitHub
    # Get repo/branch from the TaskSpec (passed via spec or stored in status)
    task_spec_dict = spec or {}
    repo = task_spec_dict.get("inputs", {}).get("repo", "")
    task_branch = f"nubi/{task_id}"

    try:
        from kubernetes_asyncio.client import CoreV1Api

        core_api = CoreV1Api()
        secret = await core_api.read_namespaced_secret(
            name=MASTER_SECRET_NAME, namespace=MASTER_SECRET_NAMESPACE
        )
        import base64

        token = base64.b64decode(secret.data[CREDENTIAL_GITHUB_TOKEN]).decode()
    except Exception as exc:
        logger.error("Failed to read GitHub token: %s", exc)
        patch.status["phase"] = Phase.FAILED
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status.setdefault("stages", {})["executor"] = {
            "status": "failed",
            "summary": f"Failed to read credentials: {exc}",
        }
        return

    try:
        result = await read_executor_result(repo, task_branch, token)
    except ResultError as exc:
        logger.error("Failed to read executor result: %s", exc)
        patch.status["phase"] = Phase.FAILED
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status.setdefault("stages", {})["executor"] = {
            "status": "failed",
            "summary": f"Failed to read result: {exc}",
        }
        return

    # Update CRD status with result
    patch.status["phase"] = Phase.DONE
    patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
    patch.status.setdefault("stages", {})["executor"] = {
        "status": "complete",
        "commitSHA": result.commit_sha,
        "summary": result.summary,
    }
    patch.status.setdefault("workspace", {})["headSHA"] = result.commit_sha
    patch.status["workspace"]["branch"] = task_branch

    logger.info(
        "Executor completed for task %s: sha=%s",
        task_id,
        result.commit_sha,
    )
