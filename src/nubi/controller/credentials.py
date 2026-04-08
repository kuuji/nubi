"""Credential scoping — per-stage Secret creation with least-privilege."""

from __future__ import annotations

import logging

from kubernetes_asyncio.client import CoreV1Api, V1ObjectMeta, V1Secret
from kubernetes_asyncio.client.exceptions import ApiException

from nubi.crd.defaults import (
    CREDENTIAL_GITHUB_TOKEN,
    CREDENTIAL_LLM_API_KEY,
    LABEL_MANAGED_BY,
    LABEL_STAGE,
    LABEL_TASK_ID,
    MASTER_SECRET_NAME,
    MASTER_SECRET_NAMESPACE,
)
from nubi.exceptions import CredentialError

logger = logging.getLogger(__name__)

STAGE_CREDENTIALS: dict[str, list[str]] = {
    "executor": [CREDENTIAL_GITHUB_TOKEN, CREDENTIAL_LLM_API_KEY],
    "validator": [CREDENTIAL_GITHUB_TOKEN, CREDENTIAL_LLM_API_KEY],
    "reviewer": [CREDENTIAL_GITHUB_TOKEN, CREDENTIAL_LLM_API_KEY],
    "monitor": [CREDENTIAL_GITHUB_TOKEN, CREDENTIAL_LLM_API_KEY],
    "gate": [],
}


async def ensure_stage_secret(ns_name: str, task_name: str, stage: str) -> str:
    """Create a scoped Secret in the task namespace for the given stage.

    Reads the master Secret from nubi-system, filters to only the keys
    the stage needs, and creates a Secret in the task namespace.

    Returns the Secret name. Idempotent (409 = no-op).
    """
    if stage not in STAGE_CREDENTIALS:
        raise CredentialError(f"Unknown stage: {stage}")

    keys = STAGE_CREDENTIALS[stage]
    if not keys:
        raise CredentialError(f"Stage '{stage}' requires no credentials")

    core_api = CoreV1Api()
    secret_name = f"nubi-{stage}-credentials"

    try:
        master = await core_api.read_namespaced_secret(MASTER_SECRET_NAME, MASTER_SECRET_NAMESPACE)
    except ApiException as exc:
        raise CredentialError(f"Failed to read master secret {MASTER_SECRET_NAME}: {exc}") from exc

    scoped_data = {k: master.data[k] for k in keys if k in master.data}

    body = V1Secret(
        metadata=V1ObjectMeta(
            name=secret_name,
            namespace=ns_name,
            labels={
                LABEL_TASK_ID: task_name,
                LABEL_STAGE: stage,
                LABEL_MANAGED_BY: "nubi",
            },
        ),
        data=scoped_data,
    )

    try:
        await core_api.create_namespaced_secret(namespace=ns_name, body=body)
    except ApiException as exc:
        if exc.status == 409:
            logger.info("Secret %s in %s already exists, continuing", secret_name, ns_name)
            return secret_name
        raise CredentialError(f"Failed to create secret {secret_name} in {ns_name}: {exc}") from exc

    return secret_name
