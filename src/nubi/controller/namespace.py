"""Task namespace lifecycle — create and delete isolated namespaces."""

from __future__ import annotations

import logging

from kubernetes_asyncio.client import (
    CoreV1Api,
    NetworkingV1Api,
    V1LabelSelector,
    V1Namespace,
    V1NetworkPolicy,
    V1NetworkPolicyEgressRule,
    V1NetworkPolicyPeer,
    V1NetworkPolicyPort,
    V1NetworkPolicySpec,
    V1ObjectMeta,
    V1ResourceQuota,
    V1ResourceQuotaSpec,
)
from kubernetes_asyncio.client.exceptions import ApiException

from nubi.crd.defaults import (
    DEFAULT_NAMESPACE_PREFIX,
    LABEL_MANAGED_BY,
    LABEL_TASK_ID,
    LABEL_TASK_TYPE,
)
from nubi.crd.schema import TaskConstraints
from nubi.exceptions import NamespaceError

logger = logging.getLogger(__name__)


def task_namespace_name(task_name: str) -> str:
    """Derive namespace name from task name, truncated to 63 chars."""
    return f"{DEFAULT_NAMESPACE_PREFIX}{task_name}"[:63]


async def ensure_task_namespace(
    task_name: str,
    task_type: str,
    constraints: TaskConstraints,
) -> str:
    """Create namespace, ResourceQuota, and NetworkPolicy for a task.

    Returns the namespace name. All sub-calls are idempotent (409 = no-op).
    """
    ns_name = task_namespace_name(task_name)
    core_api = CoreV1Api()
    networking_api = NetworkingV1Api()

    await _create_namespace(core_api, ns_name, task_name, task_type)
    await _create_resource_quota(core_api, ns_name, constraints)
    await _create_network_policy(networking_api, ns_name, constraints.network_access)

    return ns_name


async def _create_namespace(
    core_api: CoreV1Api,
    ns_name: str,
    task_name: str,
    task_type: str,
) -> None:
    """Create Namespace with PSS and nubi labels."""
    body = V1Namespace(
        metadata=V1ObjectMeta(
            name=ns_name,
            labels={
                LABEL_TASK_ID: task_name,
                LABEL_TASK_TYPE: task_type,
                LABEL_MANAGED_BY: "nubi",
                "pod-security.kubernetes.io/enforce": "restricted",
            },
        ),
    )
    try:
        await core_api.create_namespace(body=body)
    except ApiException as exc:
        if exc.status == 409:
            logger.info("Namespace %s already exists, continuing", ns_name)
            return
        raise NamespaceError(f"Failed to create namespace {ns_name}: {exc}") from exc


async def _create_resource_quota(
    core_api: CoreV1Api,
    ns_name: str,
    constraints: TaskConstraints,
) -> None:
    """Create ResourceQuota with hard limits from spec constraints."""
    body = V1ResourceQuota(
        metadata=V1ObjectMeta(name="nubi-quota"),
        spec=V1ResourceQuotaSpec(
            hard={
                "requests.cpu": constraints.resources.cpu,
                "requests.memory": constraints.resources.memory,
                "limits.cpu": constraints.resources.cpu,
                "limits.memory": constraints.resources.memory,
                "pods": "4",
            },
        ),
    )
    try:
        await core_api.create_namespaced_resource_quota(namespace=ns_name, body=body)
    except ApiException as exc:
        if exc.status == 409:
            logger.info("ResourceQuota in %s already exists, continuing", ns_name)
            return
        raise NamespaceError(f"Failed to create ResourceQuota in {ns_name}: {exc}") from exc


async def _create_network_policy(
    networking_api: NetworkingV1Api,
    ns_name: str,
    network_access: list[str],
) -> None:
    """Create NetworkPolicy: deny-all + DNS egress + optional web egress."""
    dns_egress = V1NetworkPolicyEgressRule(
        to=[
            V1NetworkPolicyPeer(
                namespace_selector=V1LabelSelector(
                    match_labels={"kubernetes.io/metadata.name": "kube-system"},
                ),
            ),
        ],
        ports=[
            V1NetworkPolicyPort(port=53, protocol="UDP"),
            V1NetworkPolicyPort(port=53, protocol="TCP"),
        ],
    )

    egress_rules: list[V1NetworkPolicyEgressRule] = [dns_egress]

    if network_access:
        # v0.1: allow ports 80/443 to any destination.
        # Host list is advisory — NetworkPolicy can't match hostnames.
        egress_rules.append(
            V1NetworkPolicyEgressRule(
                ports=[
                    V1NetworkPolicyPort(port=80, protocol="TCP"),
                    V1NetworkPolicyPort(port=443, protocol="TCP"),
                ],
            ),
        )

    body = V1NetworkPolicy(
        metadata=V1ObjectMeta(name="nubi-default"),
        spec=V1NetworkPolicySpec(
            pod_selector=V1LabelSelector(),
            policy_types=["Ingress", "Egress"],
            ingress=[],
            egress=egress_rules,
        ),
    )
    try:
        await networking_api.create_namespaced_network_policy(namespace=ns_name, body=body)
    except ApiException as exc:
        if exc.status == 409:
            logger.info("NetworkPolicy in %s already exists, continuing", ns_name)
            return
        raise NamespaceError(f"Failed to create NetworkPolicy in {ns_name}: {exc}") from exc


async def delete_task_namespace(ns_name: str) -> None:
    """Delete a task namespace. Handles 404 gracefully."""
    core_api = CoreV1Api()
    try:
        await core_api.delete_namespace(name=ns_name)
    except ApiException as exc:
        if exc.status == 404:
            logger.info("Namespace %s already deleted", ns_name)
            return
        raise NamespaceError(f"Failed to delete namespace {ns_name}: {exc}") from exc
