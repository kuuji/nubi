"""Kopf handlers for the Nubi TaskSpec controller."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import kopf

from nubi.controller.credentials import ensure_stage_secret
from nubi.controller.namespace import ensure_task_namespace
from nubi.controller.results import read_executor_result, read_gates_result
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


async def _patch_taskspec_status(
    task_id: str,
    taskspec_namespace: str,
    status_update: dict[str, Any],
) -> None:
    """Patch the TaskSpec status using the Kubernetes API with JSON Merge Patch."""
    import ssl

    import aiohttp

    logger.info("Patching TaskSpec %s/%s status: %s", taskspec_namespace, task_id, status_update)

    host = "https://kubernetes.default.svc"
    url = f"{host}/apis/nubi.io/v1/namespaces/{taskspec_namespace}/taskspecs/{task_id}/status"

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    connector = aiohttp.TCPConnector(ssl=ssl_context)

    token_file = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    with open(token_file) as f:
        token = f.read().strip()

    async with aiohttp.ClientSession(connector=connector) as session:
        headers = {
            "Content-Type": "application/merge-patch+json",
            "Authorization": f"Bearer {token}",
        }

        async with session.patch(url, json=status_update, headers=headers) as resp:
            body = await resp.text()
            logger.info(
                "Patch response for TaskSpec %s/%s: %d %s",
                taskspec_namespace,
                task_id,
                resp.status,
                body[:200],
            )
            if resp.status not in (200, 201):
                raise kopf.PermanentError(f"Failed to patch TaskSpec status: {resp.status} {body}")


@kopf.on.event("jobs", group="batch", version="v1", labels={"app.kubernetes.io/managed-by": "nubi"})  # type: ignore[arg-type]
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

    conditions = status.get("conditions", [])
    succeeded = any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions)
    failed = any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)

    if not succeeded and not failed:
        return

    if stage != "executor":
        return

    from kubernetes_asyncio.client import CustomObjectsApi

    custom_api = CustomObjectsApi()
    taskspec_namespace = namespace
    try:
        taskspec = await custom_api.get_namespaced_custom_object(
            group="nubi.io",
            version="v1",
            plural="taskspecs",
            name=task_id,
            namespace=taskspec_namespace,
        )
        logger.info("Found TaskSpec %s in namespace %s", task_id, taskspec_namespace)
    except Exception:
        taskspec_namespace = "default"
        try:
            taskspec = await custom_api.get_namespaced_custom_object(
                group="nubi.io",
                version="v1",
                plural="taskspecs",
                name=task_id,
                namespace=taskspec_namespace,
            )
            logger.info("Found TaskSpec %s in default namespace", task_id)
        except Exception as exc:
            logger.error("Failed to look up TaskSpec %s: %s", task_id, exc)
            return

    task_spec_dict = taskspec.get("spec", {})
    repo = task_spec_dict.get("inputs", {}).get("repo", "")
    task_branch = f"nubi/{task_id}"
    logger.info("TaskSpec repo=%s task_branch=%s", repo, task_branch)

    if failed:
        await _patch_taskspec_status(
            task_id,
            taskspec_namespace,
            {
                "phase": Phase.FAILED.value,
                "phaseChangedAt": datetime.now(tz=UTC).isoformat(),
                "stages": {"executor": {"status": "failed", "summary": "Job failed"}},
            },
        )
        logger.warning("Executor job %s/%s failed", namespace, name)
        return

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
        await _patch_taskspec_status(
            task_id,
            taskspec_namespace,
            {
                "phase": Phase.FAILED.value,
                "phaseChangedAt": datetime.now(tz=UTC).isoformat(),
                "stages": {
                    "executor": {
                        "status": "failed",
                        "summary": f"Failed to read credentials: {exc}",
                    }
                },
            },
        )
        return

    try:
        result = await read_executor_result(repo, task_branch, token)
    except ResultError as exc:
        logger.error("Failed to read executor result: %s", exc)
        await _patch_taskspec_status(
            task_id,
            taskspec_namespace,
            {
                "phase": Phase.FAILED.value,
                "phaseChangedAt": datetime.now(tz=UTC).isoformat(),
                "stages": {
                    "executor": {"status": "failed", "summary": f"Failed to read result: {exc}"}
                },
            },
        )
        return

    try:
        gates_result = await read_gates_result(repo, task_branch, token)
    except ResultError:
        logger.warning("No gates result found for task %s, skipping gate check", task_id)
        gates_result = None

    task_spec = TaskSpecSpec.model_validate(task_spec_dict) if task_spec_dict else None
    max_retries = task_spec.loop_policy.max_retries if task_spec else 3

    if gates_result and not gates_result.overall_passed:
        current_attempt = gates_result.attempt
        if current_attempt >= max_retries:
            await _patch_taskspec_status(
                task_id,
                taskspec_namespace,
                {
                    "phase": Phase.ESCALATED.value,
                    "phaseChangedAt": datetime.now(tz=UTC).isoformat(),
                    "stages": {
                        "gating": {"status": "failed", "passed": False, "attempt": current_attempt},
                        "executor": {
                            "status": "failed",
                            "summary": f"Gate failed after {current_attempt} attempts",
                        },
                    },
                },
            )
            logger.warning(
                "Task %s gate failed after %d attempts, escalating",
                task_id,
                current_attempt,
            )
        else:
            await _patch_taskspec_status(
                task_id,
                taskspec_namespace,
                {
                    "phase": Phase.EXECUTING.value,
                    "phaseChangedAt": datetime.now(tz=UTC).isoformat(),
                    "stages": {
                        "gating": {"status": "failed", "passed": False, "attempt": current_attempt},
                        "executor": {"status": "running", "attempts": current_attempt + 1},
                    },
                },
            )
            logger.info(
                "Task %s gate failed on attempt %d, will retry (max %d)",
                task_id,
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

    await _patch_taskspec_status(
        task_id,
        taskspec_namespace,
        {
            "phase": Phase.DONE.value,
            "phaseChangedAt": datetime.now(tz=UTC).isoformat(),
            "stages": stages_update,
            "workspace": {"headSHA": result.commit_sha, "branch": task_branch},
        },
    )

    logger.info(
        "Executor completed for task %s: sha=%s",
        task_id,
        result.commit_sha,
    )
