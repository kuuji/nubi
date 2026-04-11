"""Kopf handlers for the Nubi TaskSpec controller."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

import kopf

from nubi.controller.credentials import ensure_stage_secret
from nubi.controller.namespace import delete_task_namespace, ensure_task_namespace
from nubi.controller.results import (
    read_executor_result,
    read_gates_result,
    read_monitor_result,
    read_review_result,
)
from nubi.controller.sandbox import create_executor_job, create_monitor_job, create_reviewer_job
from nubi.crd.defaults import (
    CREDENTIAL_GITHUB_TOKEN,
    LABEL_TASKSPEC_NAMESPACE,
    MASTER_SECRET_NAME,
    MASTER_SECRET_NAMESPACE,
)
from nubi.crd.schema import Phase, TaskSpecSpec
from nubi.exceptions import CredentialError, NamespaceError, ResultError, SandboxError

logger = logging.getLogger(__name__)

EXECUTOR_JOB_STATUS_ANNOTATION = "nubi.io/executor-job-completed"
REVIEWER_JOB_STATUS_ANNOTATION = "nubi.io/reviewer-job-completed"
MONITOR_JOB_STATUS_ANNOTATION = "nubi.io/monitor-job-completed"
JOB_NAME_ANNOTATION = "nubi.io/job-name"
JOB_NAMESPACE_ANNOTATION = "nubi.io/job-namespace"
RETRY_ANNOTATION = "nubi.io/retry"

# Keep for backward compat in tests that reference it
JOB_STATUS_ANNOTATION = EXECUTOR_JOB_STATUS_ANNOTATION

_STAGE_ANNOTATIONS = {
    "executor": EXECUTOR_JOB_STATUS_ANNOTATION,
    "reviewer": REVIEWER_JOB_STATUS_ANNOTATION,
    "monitor": MONITOR_JOB_STATUS_ANNOTATION,
}


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


@kopf.on.delete("taskspecs", group="nubi.io", version="v1")  # type: ignore[arg-type]
async def on_taskspec_deleted(
    name: str,
    namespace: str,
    status: dict[str, Any],
    **kwargs: Any,
) -> None:
    """Clean up the task namespace (cascades to jobs and pods) when a TaskSpec is deleted."""
    ns_name = status.get("workspace", {}).get("namespace")
    if not ns_name:
        logger.info(
            "TaskSpec %s/%s has no workspace namespace, nothing to clean up", namespace, name
        )
        return

    logger.info("TaskSpec %s/%s deleted, cleaning up namespace %s", namespace, name, ns_name)
    await delete_task_namespace(ns_name)
    logger.info("Namespace %s deleted for TaskSpec %s/%s", ns_name, namespace, name)


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

    annotation_key = _STAGE_ANNOTATIONS.get(stage)
    if not annotation_key:
        logger.warning("Unknown stage %r on Job %s/%s for task %s", stage, namespace, name, task_id)
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
    if annotations.get(annotation_key) == "processed":
        logger.info(
            "TaskSpec %s/%s already processed %s completion; skipping duplicate event",
            taskspec_namespace,
            task_id,
            stage,
        )
        return

    job_status = "succeeded" if succeeded else "failed"
    await _annotate_task_completion(
        task_id, taskspec_namespace, name, namespace, job_status, annotation_key
    )
    logger.info(
        "TaskSpec %s/%s annotated with %s completion: %s",
        taskspec_namespace,
        task_id,
        stage,
        job_status,
    )


async def _annotate_task_completion(
    task_id: str,
    taskspec_namespace: str,
    job_name: str,
    job_namespace: str,
    job_status: str,
    annotation_key: str,
) -> None:
    from kubernetes_asyncio.client import CustomObjectsApi

    custom_api = CustomObjectsApi()

    patch = {
        "metadata": {
            "annotations": {
                annotation_key: job_status,
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
    logger.info("TaskSpec %s/%s annotated with %s", taskspec_namespace, task_id, annotation_key)


async def _read_github_token() -> str:
    """Read the GitHub token from the master secret."""
    import base64

    from kubernetes_asyncio.client import CoreV1Api

    core_api = CoreV1Api()
    secret = await core_api.read_namespaced_secret(
        name=MASTER_SECRET_NAME, namespace=MASTER_SECRET_NAMESPACE
    )
    return base64.b64decode(secret.data[CREDENTIAL_GITHUB_TOKEN]).decode()


@kopf.on.field(  # type: ignore[arg-type]
    "taskspecs",
    group="nubi.io",
    version="v1",
    field=("metadata", "annotations", EXECUTOR_JOB_STATUS_ANNOTATION),
)
async def on_executor_completion(
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

    logger.info("Processing executor completion for TaskSpec %s/%s: %s", namespace, name, new)

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
        patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = "processed"
        logger.warning("TaskSpec %s/%s marked as failed", namespace, name)
        return

    try:
        token = await _read_github_token()
    except Exception as exc:
        logger.error("Failed to read GitHub token: %s", exc)
        patch.status["phase"] = Phase.FAILED.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            "executor": {"status": "failed", "summary": f"Failed to read credentials: {exc}"}
        }
        patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = "processed"
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
        patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = "processed"
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
            patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = "processed"
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
            patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = "processed"
            logger.info(
                "Task %s gate failed on attempt %d, will retry (max %d)",
                name,
                current_attempt,
                max_retries,
            )
        return

    # Gates passed — decide whether to review or finish
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

    patch.status["workspace"] = {"headSHA": result.commit_sha, "branch": task_branch}

    if task_spec.review.enabled:
        # Spawn the reviewer
        ns_name = status.get("workspace", {}).get("namespace", f"nubi-{name}")

        try:
            secret_name = await ensure_stage_secret(ns_name, name, "reviewer")
        except CredentialError as exc:
            logger.error("Failed to create reviewer credentials: %s", exc)
            patch.status["phase"] = Phase.FAILED.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = stages_update
            patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = "processed"
            return

        # Determine attempt from executor status
        reviewer_attempt = status.get("stages", {}).get("executor", {}).get("attempts", 1)

        try:
            reviewer_job = await create_reviewer_job(
                name, ns_name, task_spec, secret_name, namespace, attempt=reviewer_attempt
            )
        except SandboxError as exc:
            logger.error("Failed to create reviewer job: %s", exc)
            patch.status["phase"] = Phase.FAILED.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = stages_update
            patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = "processed"
            return

        stages_update["reviewer"] = {"status": "running"}
        patch.status["phase"] = Phase.REVIEWING.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = stages_update
        patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = "processed"
        # Reset reviewer annotation so the new reviewer completion can be detected
        patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = ""

        logger.info(
            "Executor completed for task %s/%s, spawned reviewer job %s",
            namespace,
            name,
            reviewer_job,
        )
    else:
        patch.status["phase"] = Phase.DONE.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = stages_update
        patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = "processed"

        logger.info(
            "Executor completed for task %s/%s: sha=%s (review disabled)",
            namespace,
            name,
            result.commit_sha,
        )


@kopf.on.field(  # type: ignore[arg-type]
    "taskspecs",
    group="nubi.io",
    version="v1",
    field=("metadata", "annotations", REVIEWER_JOB_STATUS_ANNOTATION),
)
async def on_reviewer_completion(
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

    logger.info("Processing reviewer completion for TaskSpec %s/%s: %s", namespace, name, new)

    normalized_spec = dict(spec)
    if "loop_policy" not in normalized_spec and "loopPolicy" in normalized_spec:
        normalized_spec["loop_policy"] = normalized_spec["loopPolicy"]

    task_spec = TaskSpecSpec.model_validate(normalized_spec)
    repo = task_spec.inputs.repo
    task_branch = f"nubi/{name}"

    if new == "failed":
        patch.status["phase"] = Phase.FAILED.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            **status.get("stages", {}),
            "reviewer": {"status": "failed", "feedback": "Reviewer job failed"},
        }
        patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
        logger.warning("TaskSpec %s/%s reviewer failed", namespace, name)
        return

    try:
        token = await _read_github_token()
    except Exception as exc:
        logger.error("Failed to read GitHub token for reviewer result: %s", exc)
        patch.status["phase"] = Phase.FAILED.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
        return

    try:
        review = await read_review_result(repo, task_branch, token)
    except ResultError as exc:
        logger.error("Failed to read reviewer result: %s", exc)
        patch.status["phase"] = Phase.FAILED.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            **status.get("stages", {}),
            "reviewer": {"status": "failed", "feedback": f"Failed to read result: {exc}"},
        }
        patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
        return

    reviewer_stage = {
        "status": review.decision.value,
        "feedback": review.feedback,
        "decision": review.decision.value,
    }

    if review.decision == "approve":
        if task_spec.monitoring.summary:
            # Spawn monitor agent for final audit
            ns_name = status.get("workspace", {}).get("namespace", f"nubi-{name}")

            pod_logs_b64 = await _collect_pod_logs(ns_name, name)

            try:
                secret_name = await ensure_stage_secret(ns_name, name, "monitor")
            except CredentialError as exc:
                logger.error("Failed to create monitor credentials: %s", exc)
                # Graceful degradation — go to Done without monitor
                patch.status["phase"] = Phase.DONE.value
                patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
                patch.status["stages"] = {**status.get("stages", {}), "reviewer": reviewer_stage}
                patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
                logger.info(
                    "Task %s/%s approved (monitor skipped: credential error)",
                    namespace,
                    name,
                )
                return

            try:
                monitor_job = await create_monitor_job(
                    name,
                    ns_name,
                    task_spec,
                    secret_name,
                    namespace,
                    pod_logs_b64=pod_logs_b64,
                )
            except SandboxError as exc:
                logger.error("Failed to create monitor job: %s", exc)
                # Graceful degradation — go to Done without monitor
                patch.status["phase"] = Phase.DONE.value
                patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
                patch.status["stages"] = {**status.get("stages", {}), "reviewer": reviewer_stage}
                patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
                logger.info("Task %s/%s approved (monitor skipped: job error)", namespace, name)
                return

            patch.status["phase"] = Phase.MONITORING.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = {
                **status.get("stages", {}),
                "reviewer": reviewer_stage,
                "monitor": {"status": "running"},
            }
            patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
            patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = ""
            logger.info(
                "Task %s/%s approved by reviewer, spawned monitor job %s",
                namespace,
                name,
                monitor_job,
            )
        else:
            patch.status["phase"] = Phase.DONE.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = {**status.get("stages", {}), "reviewer": reviewer_stage}
            patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
            logger.info("Task %s/%s approved by reviewer (monitor disabled)", namespace, name)

    elif review.decision == "request-changes" and task_spec.loop_policy.reviewer_to_executor:
        # Re-spawn executor with reviewer feedback
        ns_name = status.get("workspace", {}).get("namespace", f"nubi-{name}")

        try:
            secret_name = await ensure_stage_secret(ns_name, name, "executor")
        except CredentialError as exc:
            logger.error("Failed to create executor credentials for retry: %s", exc)
            patch.status["phase"] = Phase.ESCALATED.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = {**status.get("stages", {}), "reviewer": reviewer_stage}
            patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
            return

        # Determine attempt number from previous executor status
        prev_attempts = status.get("stages", {}).get("executor", {}).get("attempts", 1)
        attempt = prev_attempts + 1

        try:
            executor_job = await create_executor_job(
                name,
                ns_name,
                task_spec,
                secret_name,
                namespace,
                attempt=attempt,
                reviewer_feedback=review.feedback,
            )
        except SandboxError as exc:
            logger.error("Failed to re-create executor job: %s", exc)
            patch.status["phase"] = Phase.ESCALATED.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = {**status.get("stages", {}), "reviewer": reviewer_stage}
            patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
            return

        patch.status["phase"] = Phase.EXECUTING.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            **status.get("stages", {}),
            "reviewer": reviewer_stage,
            "executor": {"status": "running", "attempts": attempt},
        }
        # Reset both annotations so the new executor/reviewer cycle can be detected
        patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = ""
        patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
        logger.info(
            "Task %s/%s reviewer requested changes, re-spawning executor %s",
            namespace,
            name,
            executor_job,
        )

    else:
        # reject, or request-changes without reviewer_to_executor
        patch.status["phase"] = Phase.ESCALATED.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {**status.get("stages", {}), "reviewer": reviewer_stage}
        patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = "processed"
        logger.warning(
            "Task %s/%s escalated: reviewer decision=%s",
            namespace,
            name,
            review.decision.value,
        )


async def _collect_pod_logs(ns_name: str, task_name: str) -> str:
    """Collect pod logs from executor and reviewer pods in the task namespace.

    Returns base64-encoded concatenated logs, truncated to ~32KB.
    Returns empty string on any error (best-effort).
    """
    import base64

    from kubernetes_asyncio.client import CoreV1Api

    core_api = CoreV1Api()
    logs_parts: list[str] = []
    max_bytes = 32 * 1024

    for stage in ("executor", "reviewer"):
        try:
            pods = await core_api.list_namespaced_pod(
                namespace=ns_name,
                label_selector=f"nubi.io/task-id={task_name},nubi.io/stage={stage}",
            )
            for pod in pods.items:
                try:
                    log = await core_api.read_namespaced_pod_log(
                        name=pod.metadata.name,
                        namespace=ns_name,
                        container=stage,
                        tail_lines=500,
                    )
                    logs_parts.append(f"=== {stage} pod: {pod.metadata.name} ===\n{log}\n")
                except Exception as exc:
                    logger.debug("Failed to read logs for pod %s: %s", pod.metadata.name, exc)
        except Exception as exc:
            logger.debug("Failed to list %s pods in %s: %s", stage, ns_name, exc)

    if not logs_parts:
        return ""

    combined = "\n".join(logs_parts)
    if len(combined) > max_bytes:
        combined = combined[:max_bytes] + "\n... (truncated)"

    return base64.b64encode(combined.encode()).decode()


@kopf.on.field(  # type: ignore[arg-type]
    "taskspecs",
    group="nubi.io",
    version="v1",
    field=("metadata", "annotations", MONITOR_JOB_STATUS_ANNOTATION),
)
async def on_monitor_completion(
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

    logger.info("Processing monitor completion for TaskSpec %s/%s: %s", namespace, name, new)

    normalized_spec = dict(spec)
    if "loop_policy" not in normalized_spec and "loopPolicy" in normalized_spec:
        normalized_spec["loop_policy"] = normalized_spec["loopPolicy"]

    task_spec = TaskSpecSpec.model_validate(normalized_spec)
    repo = task_spec.inputs.repo
    task_branch = f"nubi/{name}"

    # Monitor failure is always graceful — task goes to Done
    if new == "failed":
        logger.warning(
            "TaskSpec %s/%s monitor job failed — graceful degradation, marking Done",
            namespace,
            name,
        )
        patch.status["phase"] = Phase.DONE.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            **status.get("stages", {}),
            "monitor": {"status": "failed", "summary": "Monitor job failed (graceful)"},
        }
        patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = "processed"
        return

    try:
        token = await _read_github_token()
    except Exception as exc:
        logger.error("Failed to read GitHub token for monitor result: %s", exc)
        # Graceful degradation
        patch.status["phase"] = Phase.DONE.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            **status.get("stages", {}),
            "monitor": {"status": "failed", "summary": f"Failed to read credentials: {exc}"},
        }
        patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = "processed"
        return

    try:
        monitor = await read_monitor_result(repo, task_branch, token)
    except ResultError as exc:
        logger.warning("Failed to read monitor result: %s — graceful degradation", exc)
        patch.status["phase"] = Phase.DONE.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            **status.get("stages", {}),
            "monitor": {"status": "failed", "summary": f"Failed to read result: {exc}"},
        }
        patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = "processed"
        return

    monitor_stage: dict[str, Any] = {
        "status": monitor.decision.value,
        "decision": monitor.decision.value,
        "summary": monitor.summary,
    }

    if monitor.concerns:
        monitor_stage["concerns"] = [
            {"severity": c.severity, "area": c.area, "description": c.description}
            for c in monitor.concerns
        ]

    if monitor.pr_url:
        monitor_stage["prURL"] = monitor.pr_url
    if monitor.ci_status:
        monitor_stage["ciStatus"] = monitor.ci_status
    if monitor.ci_feedback:
        monitor_stage["ciFeedback"] = monitor.ci_feedback[:500]

    # CI failure: kick back to executor if retries remain
    if monitor.decision == "ci-failed":
        ci_retries = status.get("stages", {}).get("monitor", {}).get("ciRetries", 0)
        max_ci_retries = task_spec.loop_policy.max_ci_retries

        if ci_retries >= max_ci_retries:
            patch.status["phase"] = Phase.ESCALATED.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            monitor_stage["ciRetries"] = ci_retries
            patch.status["stages"] = {**status.get("stages", {}), "monitor": monitor_stage}
            patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = "processed"
            logger.warning(
                "Task %s/%s CI failed after %d retries, escalating",
                namespace,
                name,
                ci_retries,
            )
            return

        # Re-spawn executor with CI failure feedback
        ns_name = status.get("workspace", {}).get("namespace", f"nubi-{name}")
        ci_feedback = monitor.ci_feedback or monitor.summary

        try:
            secret_name = await ensure_stage_secret(ns_name, name, "executor")
        except CredentialError as exc:
            logger.error("Failed to create executor credentials for CI retry: %s", exc)
            patch.status["phase"] = Phase.ESCALATED.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = {**status.get("stages", {}), "monitor": monitor_stage}
            patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = "processed"
            return

        prev_attempts = status.get("stages", {}).get("executor", {}).get("attempts", 1)
        attempt = prev_attempts + 1

        try:
            executor_job = await create_executor_job(
                name,
                ns_name,
                task_spec,
                secret_name,
                namespace,
                attempt=attempt,
                reviewer_feedback=f"CI checks failed. Fix the issues:\n\n{ci_feedback}",
            )
        except SandboxError as exc:
            logger.error("Failed to re-create executor job for CI retry: %s", exc)
            patch.status["phase"] = Phase.ESCALATED.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            patch.status["stages"] = {**status.get("stages", {}), "monitor": monitor_stage}
            patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = "processed"
            return

        monitor_stage["ciRetries"] = ci_retries + 1
        patch.status["phase"] = Phase.EXECUTING.value
        patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
        patch.status["stages"] = {
            **status.get("stages", {}),
            "monitor": monitor_stage,
            "executor": {"status": "running", "attempts": attempt},
        }
        patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = ""
        patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = "processed"
        logger.info(
            "Task %s/%s CI failed, re-spawning executor %s (ci_retry %d/%d)",
            namespace,
            name,
            executor_job,
            ci_retries + 1,
            max_ci_retries,
        )
        return

    # Approve and flag go to Done (graceful)
    patch.status["phase"] = Phase.DONE.value
    patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
    patch.status["stages"] = {**status.get("stages", {}), "monitor": monitor_stage}
    patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = "processed"

    if monitor.decision == "approve":
        logger.info(
            "Task %s/%s monitor approved, PR: %s",
            namespace,
            name,
            monitor.pr_url or "(none)",
        )
    else:
        logger.info(
            "Task %s/%s monitor flagged concerns: %s",
            namespace,
            name,
            monitor.summary[:200],
        )


@kopf.on.field(  # type: ignore[arg-type]
    "taskspecs",
    group="nubi.io",
    version="v1",
    field=("metadata", "annotations", RETRY_ANNOTATION),
)
async def on_retry_requested(
    spec: dict[str, Any],
    name: str,
    namespace: str,
    status: dict[str, Any],
    patch: Any,
    old: Any,
    new: Any,
    **kwargs: Any,
) -> None:
    """Re-run the pipeline when a retry annotation is set on a Failed/Escalated TaskSpec.

    Usage: kubectl annotate taskspec <name> nubi.io/retry=$(date +%s) --overwrite
    """
    if not new:
        return

    current_phase = status.get("phase")
    if current_phase not in (Phase.FAILED.value, Phase.ESCALATED.value):
        logger.info(
            "TaskSpec %s/%s retry requested but phase is %s (not Failed/Escalated), ignoring",
            namespace,
            name,
            current_phase,
        )
        return

    logger.info(
        "TaskSpec %s/%s retry requested (phase=%s), re-running pipeline",
        namespace,
        name,
        current_phase,
    )

    task_spec = TaskSpecSpec.model_validate(spec)

    # Re-use existing namespace or create a new one
    ns_name = status.get("workspace", {}).get("namespace")
    if not ns_name:
        try:
            ns_name = await ensure_task_namespace(name, task_spec.type.value, task_spec.constraints)
        except NamespaceError:
            patch.status["phase"] = Phase.FAILED.value
            patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
            raise

    patch.status["phase"] = Phase.PENDING.value
    patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
    patch.status["workspace"] = {"namespace": ns_name}

    try:
        secret_name = await ensure_stage_secret(ns_name, name, "executor")
    except CredentialError:
        patch.status["phase"] = Phase.FAILED.value
        raise

    try:
        job_name = await create_executor_job(name, ns_name, task_spec, secret_name, namespace)
    except SandboxError:
        patch.status["phase"] = Phase.FAILED.value
        raise

    patch.status["phase"] = Phase.EXECUTING.value
    patch.status["phaseChangedAt"] = datetime.now(tz=UTC).isoformat()
    patch.status["stages"] = {"executor": {"status": "running", "attempts": 1}}

    # Clear completion annotations so the new pipeline cycle works
    patch.meta.annotations[EXECUTOR_JOB_STATUS_ANNOTATION] = ""
    patch.meta.annotations[REVIEWER_JOB_STATUS_ANNOTATION] = ""
    patch.meta.annotations[MONITOR_JOB_STATUS_ANNOTATION] = ""

    logger.info(
        "TaskSpec %s/%s retried, executor job %s created in namespace %s",
        namespace,
        name,
        job_name,
        ns_name,
    )
