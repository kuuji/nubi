"""Kopf handlers for the Nubi TaskSpec controller."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

import kopf

from nubi.controller.credentials import ensure_stage_secret
from nubi.controller.namespace import ensure_task_namespace
from nubi.controller.results import read_executor_result, read_gates_result
from nubi.controller.sandbox import create_executor_job
from nubi.crd.defaults import (
    CREDENTIAL_GITHUB_TOKEN,
    LABEL_TASKSPEC_NAMESPACE,
    MASTER_SECRET_NAME,
    MASTER_SECRET_NAMESPACE,
)
from nubi.crd.schema import Phase, TaskSpecSpec
from nubi.exceptions import CredentialError, NamespaceError, ResultError, SandboxError

logger = logging.getLogger(__name__)

JOB_STATUS_ANNOTATION = "nubi.io/job-completed"
JOB_NAME_ANNOTATION = "nubi.io/job-name"
JOB_NAMESPACE_ANNOTATION = "nubi.io/job-namespace"


@kopf.on.create("taskspecs", group="nubi.io", version="v1")  # type: ignore[arg-type]
async def on_taskspec_created(
    spec: dict[str, Any],
    name: str,
    namespace: str,
    patch: Any,
    **kwargs: Any,
) -> dict[str, str]:
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
        job_name = await create_executor_job(name, ns_name, task_spec, secret_name, namespace)
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
    labels: dict[str, Any],
    **kwargs: Any,
) -> None:
    task_id = labels.get("nubi.io/task-id", "unknown")
    taskspec_namespace = labels.get(LABEL_TASKSPEC_NAMESPACE)
    stage = labels.get("nubi.io/stage", "unknown")

    logger.info(
        "Job %s/%s changed (task=%s, stage=%s)",
        namespace,
        name,
        task_id,
        stage,
    )

    conditions = status.get("conditions", [])
    succeeded = any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions)
    failed = any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)

    if not succeeded and not failed:
        return

    if stage != "executor":
        return

    if not taskspec_namespace:
        logger.error(
            "Job %s/%s missing %s label; cannot route completion for task %s",
            namespace,
            name,
            LABEL_TASKSPEC_NAMESPACE,
            task_id,
        )
        return

    from kubernetes_asyncio.client import CustomObjectsApi

    custom_api = CustomObjectsApi()
    try:
        taskspec = await custom_api.get_namespaced_custom_object(
            group="nubi.io",
            version="v1",
            plural="taskspecs",
            name=task_id,
            namespace=taskspec_namespace,
        )
        logger.info("Found TaskSpec %s in namespace %s", task_id, taskspec_namespace)
    except Exception as exc:
        logger.error("Failed to look up TaskSpec %s: %s", task_id, exc)
        return

    annotations = taskspec.get("metadata", {}).get("annotations", {})
    if annotations.get(JOB_STATUS_ANNOTATION) == "processed":
        logger.info(
            "TaskSpec %s/%s already processed job completion; skipping duplicate event",
            taskspec_namespace,
            task_id,
        )
        return

    job_status = "succeeded" if succeeded else "failed"
    await _annotate_task_completion(task_id, taskspec_namespace, name, namespace, job_status)
    logger.info(
        "TaskSpec %s/%s annotated with job completion: %s", taskspec_namespace, task_id, job_status
    )


async def _annotate_task_completion(
    task_id: str,
    taskspec_namespace: str,
    job_name: str,
    job_namespace: str,
    job_status: str,
) -> None:
    from kubernetes_asyncio.client import CustomObjectsApi

    custom_api = CustomObjectsApi()
    logger.info(
        "Annotating TaskSpec %s/%s with job completion: %s",
        taskspec_namespace,
        task_id,
        job_status,
    )

    patch = {
        "metadata": {
            "annotations": {
                JOB_STATUS_ANNOTATION: job_status,
                JOB_NAME_ANNOTATION: job_name,
                JOB_NAMESPACE_ANNOTATION: job_namespace,
            }
        }
    }

    patch_custom_object = cast(Any, custom_api.patch_namespaced_custom_object)
    await patch_custom_object(
        group="nubi.io",
        version="v1",
        plural="taskspecs",
        name=task_id,
        namespace=taskspec_namespace,
        body=patch,
        _content_type="application/merge-patch+json",
    )
    logger.info("TaskSpec %s/%s annotated successfully", taskspec_namespace, task_id)


@kopf.on.field(  # type: ignore[arg-type]
    "taskspecs",
    group="nubi.io",
    version="v1",
    field=("metadata", "annotations", JOB_STATUS_ANNOTATION),
)
async def on_job_completion_annotation(
    spec: dict[str, Any],
    name: str,
    namespace: str,
    status: dict[str, Any],
    patch: Any,
    old: Any,
    new: Any,
    **kwargs: Any,
) -> None:
    if not new or new == "processed":
        return

    logger.info("Processing job completion annotation for TaskSpec %s/%s: %s", namespace, name, new)

    normalized_spec = dict(spec)
    if "loop_policy" not in normalized_spec and "loopPolicy" in normalized_spec:
        normalized_spec["loop_policy"] = normalized_spec["loopPolicy"]

    task_spec = TaskSpecSpec.model_validate(normalized_spec)
    repo = task_spec.inputs.repo
    task_branch = f"nubi/{name}"

    if new == "failed":
        patch.status["phase"] = Phase.FAILED.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {"executor": {"status": "failed", "summary": "Job failed"}}
        patch.meta.annotations[JOB_STATUS_ANNOTATION] = "processed"
        logger.warning("TaskSpec %s/%s marked as failed", namespace, name)
        return

    try:
        import base64

        from kubernetes_asyncio.client import CoreV1Api

        core_api = CoreV1Api()
        secret = await core_api.read_namespaced_secret(
            name=MASTER_SECRET_NAME, namespace=MASTER_SECRET_NAMESPACE
        )
        token = base64.b64decode(secret.data[CREDENTIAL_GITHUB_TOKEN]).decode()
    except Exception as exc:
        logger.error("Failed to read GitHub token: %s", exc)
        patch.status["phase"] = Phase.FAILED.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            "executor": {"status": "failed", "summary": f"Failed to read credentials: {exc}"}
        }
        patch.meta.annotations[JOB_STATUS_ANNOTATION] = "processed"
        return

    try:
        result = await read_executor_result(repo, task_branch, token)
    except ResultError as exc:
        logger.error("Failed to read executor result: %s", exc)
        patch.status["phase"] = Phase.FAILED.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            "executor": {"status": "failed", "summary": f"Failed to read result: {exc}"}
        }
        patch.meta.annotations[JOB_STATUS_ANNOTATION] = "processed"
        return

    gates_result = None
    try:
        gates_result = await read_gates_result(repo, task_branch, token)
    except ResultError:
        logger.warning("No gates result found for task %s, skipping gate check", name)

    max_retries = task_spec.loop_policy.max_retries

    if gates_result and not gates_result.overall_passed:
        current_attempt = gates_result.attempt
        if current_attempt >= max_retries:
            patch.status["phase"] = Phase.ESCALATED.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = {
                "gating": {"status": "failed", "passed": False, "attempt": current_attempt},
                "executor": {
                    "status": "failed",
                    "summary": f"Gate failed after {current_attempt} attempts",
                },
            }
            patch.meta.annotations[JOB_STATUS_ANNOTATION] = "processed"
            logger.warning(
                "Task %s gate failed after %d attempts, escalating",
                name,
                current_attempt,
            )
        else:
            patch.status["phase"] = Phase.EXECUTING.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = {
                "gating": {"status": "failed", "passed": False, "attempt": current_attempt},
                "executor": {"status": "running", "attempts": current_attempt + 1},
            }
            patch.meta.annotations[JOB_STATUS_ANNOTATION] = "processed"
            logger.info(
                "Task %s gate failed on attempt %d, will retry (max %d)",
                name,
                current_attempt,
                max_retries,
            )
        return

    stages_update: dict[str, Any] = {
        "executor": {
            "status": "complete",
            "commitSHA": result.commit_sha,
            "summary": result.summary,
        },
    }
    if gates_result:
        stages_update["gating"] = {
            "status": "passed",
            "passed": True,
            "attempt": gates_result.attempt,
        }

    patch.status["phase"] = Phase.DONE.value
    patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
    patch.status["stages"] = stages_update
    patch.status["workspace"] = {"headSHA": result.commit_sha, "branch": task_branch}
    patch.meta.annotations[JOB_STATUS_ANNOTATION] = "processed"

    logger.info(
        "Executor completed for task %s/%s: sha=%s",
        namespace,
        name,
        result.commit_sha,
    )
