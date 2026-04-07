"""Integration test helpers — K8s utilities for creating and watching TaskSpecs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kubernetes_asyncio.client import CustomObjectsApi
from kubernetes_asyncio.client.exceptions import ApiException

logger = logging.getLogger(__name__)

TASKSPEC_NAMESPACE = "nubi-system"
GROUP = "nubi.io"
VERSION = "v1"
PLURAL = "taskspecs"


async def create_taskspec(
    name: str,
    description: str = "Integration test task",
    repo: str = "test/repo",
    branch: str = "main",
    review_enabled: bool = True,
    review_focus: list[str] | None = None,
    max_retries: int = 2,
    timeout: str = "60s",
    **overrides: Any,
) -> str:
    """Create a TaskSpec CR and return its name.

    The TaskSpec uses minimal constraints suitable for integration tests
    with the fake agent image.
    """
    spec: dict[str, Any] = {
        "description": description,
        "type": "code-change",
        "inputs": {"repo": repo, "branch": branch},
        "constraints": {
            "timeout": timeout,
            "resources": {"cpu": "100m", "memory": "64Mi"},
            "tools": ["shell", "git", "file_read", "file_write"],
        },
        "review": {
            "enabled": review_enabled,
            "focus": review_focus or [],
        },
        "loop_policy": {
            "max_retries": max_retries,
            "reviewer_to_executor": True,
        },
    }
    spec.update(overrides)

    body = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "TaskSpec",
        "metadata": {
            "name": name,
            "namespace": TASKSPEC_NAMESPACE,
            "labels": {"app.kubernetes.io/created-by": "nubi-integration"},
        },
        "spec": spec,
    }

    api = CustomObjectsApi()
    await api.create_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace=TASKSPEC_NAMESPACE,
        plural=PLURAL,
        body=body,
    )
    logger.info("Created TaskSpec %s", name)
    return name


async def delete_taskspec(name: str) -> None:
    """Delete a TaskSpec CR, ignoring not-found errors."""
    api = CustomObjectsApi()
    try:
        await api.delete_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=TASKSPEC_NAMESPACE,
            plural=PLURAL,
            name=name,
        )
    except ApiException as exc:
        if exc.status != 404:
            raise


async def delete_namespace(name: str) -> None:
    """Delete a namespace, ignoring not-found errors."""
    from kubernetes_asyncio.client import CoreV1Api

    api = CoreV1Api()
    try:
        await api.delete_namespace(name=name)
    except ApiException as exc:
        if exc.status != 404:
            raise


async def get_taskspec_status(name: str) -> dict[str, Any]:
    """Read the current status of a TaskSpec."""
    api = CustomObjectsApi()
    obj = await api.get_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace=TASKSPEC_NAMESPACE,
        plural=PLURAL,
        name=name,
    )
    return obj.get("status", {})


async def get_taskspec_phase(name: str) -> str:
    """Read just the phase of a TaskSpec."""
    status = await get_taskspec_status(name)
    return status.get("phase", "")


async def await_phase(
    name: str,
    target: str | set[str],
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> str:
    """Poll until the TaskSpec reaches the target phase(s) or timeout.

    Args:
        name: TaskSpec name.
        target: A phase string or set of phase strings to wait for.
        timeout: Maximum seconds to wait.
        poll_interval: Seconds between polls.

    Returns:
        The phase that was reached.

    Raises:
        TimeoutError: If the target phase is not reached within timeout.
    """
    if isinstance(target, str):
        target = {target}

    deadline = asyncio.get_event_loop().time() + timeout
    last_phase = ""

    while asyncio.get_event_loop().time() < deadline:
        phase = await get_taskspec_phase(name)
        if phase != last_phase:
            logger.info("TaskSpec %s phase: %s", name, phase)
            last_phase = phase
        if phase in target:
            return phase
        await asyncio.sleep(poll_interval)

    raise TimeoutError(
        f"TaskSpec {name} did not reach {target} within {timeout}s (last phase: {last_phase})"
    )
