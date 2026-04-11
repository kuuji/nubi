"""Kubernetes client helpers for the Nubi MCP server.

Wraps the official kubernetes Python client to provide a clean interface
for TaskSpec CRUD operations. Uses incluster config when running in K8s,
falls back to kubeconfig for local development.
"""

from __future__ import annotations

from typing import Any

from kubernetes import client, config
from kubernetes.client import ApiException

# Module-level config initialization flag
_config_loaded = False


def _ensure_config() -> None:
    """Load Kubernetes config. Uses incluster config first, falls back to kubeconfig.

    This function is idempotent - it only loads config once per module load.
    """
    global _config_loaded
    if not _config_loaded:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        _config_loaded = True


def create_taskspec(name: str, namespace: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Create a TaskSpec custom resource in the cluster.

    Args:
        name: TaskSpec name (must be DNS-compatible).
        namespace: Kubernetes namespace.
        spec: The TaskSpec spec as a dict.

    Returns:
        The created TaskSpec resource as a dict.

    Raises:
        ApiException: If the API call fails.
    """
    _ensure_config()
    api = client.CustomObjectsApi()
    body: dict[str, Any] = {
        "apiVersion": "nubi.io/v1",
        "kind": "TaskSpec",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }
    result: dict[str, Any] = api.create_namespaced_custom_object(
        group="nubi.io",
        version="v1",
        namespace=namespace,
        plural="taskspecs",
        body=body,
    )
    return result


def list_taskspecs(namespace: str, phase: str = "") -> list[dict[str, Any]]:
    """List TaskSpec resources in a namespace.

    Args:
        namespace: Kubernetes namespace.
        phase: Optional phase filter.

    Returns:
        List of TaskSpec resources as dicts.

    Raises:
        ApiException: If the API call fails.
    """
    _ensure_config()
    api = client.CustomObjectsApi()
    result = api.list_namespaced_custom_object(
        group="nubi.io",
        version="v1",
        namespace=namespace,
        plural="taskspecs",
    )
    items: list[dict[str, Any]] = result.get("items", [])
    if phase:
        items = [item for item in items if item.get("status", {}).get("phase") == phase]
    return items


def get_taskspec(name: str, namespace: str) -> dict[str, Any]:
    """Get a single TaskSpec resource.

    Args:
        name: TaskSpec name.
        namespace: Kubernetes namespace.

    Returns:
        The TaskSpec resource as a dict.

    Raises:
        ApiException: If the TaskSpec is not found (404).
    """
    _ensure_config()
    api = client.CustomObjectsApi()
    result: dict[str, Any] = api.get_namespaced_custom_object(
        group="nubi.io",
        version="v1",
        namespace=namespace,
        plural="taskspecs",
        name=name,
    )
    return result


def delete_taskspec(name: str, namespace: str) -> dict[str, Any]:
    """Delete a TaskSpec resource.

    Args:
        name: TaskSpec name.
        namespace: Kubernetes namespace.

    Returns:
        The delete response as a dict.

    Raises:
        ApiException: If the TaskSpec is not found (404).
    """
    _ensure_config()
    api = client.CustomObjectsApi()
    result: dict[str, Any] = api.delete_namespaced_custom_object(
        group="nubi.io",
        version="v1",
        namespace=namespace,
        plural="taskspecs",
        name=name,
    )
    return result


def patch_taskspec_annotation(
    name: str,
    namespace: str,
    annotation: str,
    value: str,
) -> dict[str, Any]:
    """Patch a TaskSpec with an annotation using JSON merge patch.

    Args:
        name: TaskSpec name.
        namespace: Kubernetes namespace.
        annotation: The annotation key (e.g., "nubi.io/retry").
        value: The annotation value.

    Returns:
        The patched TaskSpec resource as a dict.

    Raises:
        ApiException: If the API call fails.
    """
    _ensure_config()
    api = client.CustomObjectsApi()
    body: dict[str, Any] = {
        "metadata": {
            "annotations": {
                annotation: value,
            }
        }
    }
    result: dict[str, Any] = api.patch_namespaced_custom_object(
        group="nubi.io",
        version="v1",
        namespace=namespace,
        plural="taskspecs",
        name=name,
        body=body,
        _content_type="application/merge-patch+json",
    )
    return result


def get_pod_logs(name: str, namespace: str, stage: str) -> str:
    """Get logs from a pod for a specific task stage.

    Args:
        name: TaskSpec name (used to find the task namespace).
        namespace: TaskSpec namespace (typically nubi-system).
        stage: Stage name (executor, reviewer, monitor).

    Returns:
        Pod logs as a string (last 200 lines).

    Raises:
        ApiException: If no pod is found or logs cannot be retrieved.
    """
    _ensure_config()
    core_api = client.CoreV1Api()
    task_namespace = f"nubi-{name}"

    label_selector = f"nubi.io/stage={stage},nubi.io/task-id={name}"
    pods = core_api.list_namespaced_pod(
        namespace=task_namespace,
        label_selector=label_selector,
    )

    if not pods.items:
        raise ApiException(
            status=404,
            reason=f"No pod found for stage '{stage}' in namespace '{task_namespace}'",
        )

    pod = pods.items[0]
    logs: str = core_api.read_namespaced_pod_log(
        name=pod.metadata.name,
        namespace=task_namespace,
        tail_lines=200,
    )
    return logs
