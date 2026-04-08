"""Integration test fixtures — real K8s, in-process kopf, mocked results."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from kubernetes_asyncio import config

# All integration tests share a single event loop so the session-scoped
# kopf operator background task persists across tests.
from tests.integration.helpers import (
    delete_namespace,
    delete_taskspec,
)
from tests.integration.scenario_store import ScenarioResultStore

logger = logging.getLogger(__name__)

CLUSTER_NAME = os.environ.get("NUBI_INTEGRATION_CLUSTER", "nubi-integration")
CONTEXT = f"k3d-{CLUSTER_NAME}"
FAKE_AGENT_IMAGE = "nubi-fake-agent:test"


def _cluster_exists() -> bool:
    """Check if the k3d integration cluster exists."""
    result = subprocess.run(
        ["k3d", "cluster", "list", "-o", "json"],
        capture_output=True,
        text=True,
    )
    return f'"name":"{CLUSTER_NAME}"' in result.stdout


@pytest.fixture(scope="session")
def k3d_cluster() -> None:
    """Ensure the k3d integration cluster is running.

    Run scripts/integration-setup.sh first if the cluster doesn't exist.
    """
    if not _cluster_exists():
        pytest.skip(f"k3d cluster '{CLUSTER_NAME}' not found. Run: ./scripts/integration-setup.sh")


@pytest_asyncio.fixture(scope="session")
async def k8s_config(k3d_cluster: None) -> None:
    """Load kubeconfig for the integration cluster."""
    await config.load_kube_config(context=CONTEXT)


@pytest.fixture(scope="session")
def scenario_store() -> ScenarioResultStore:
    """Shared scenario result store for all integration tests."""
    return ScenarioResultStore()


@pytest_asyncio.fixture(scope="session")
async def controller(
    k8s_config: None,
    scenario_store: ScenarioResultStore,
) -> AsyncGenerator[None, None]:
    """Run the kopf controller in-process with mocked results.

    Patches the handler module's result-reading functions to use the
    scenario store, then starts kopf.operator() as a background task.
    """
    import kopf

    import nubi.controller.handlers as handlers_mod
    import nubi.controller.sandbox as sandbox_mod

    # Patch result readers to use scenario store
    handlers_mod.read_executor_result = scenario_store.read_executor_result  # type: ignore[assignment]
    handlers_mod.read_gates_result = scenario_store.read_gates_result  # type: ignore[assignment]
    handlers_mod.read_review_result = scenario_store.read_review_result  # type: ignore[assignment]
    handlers_mod.read_monitor_result = scenario_store.read_monitor_result  # type: ignore[assignment]

    # Patch GitHub token reader to return a fake token
    handlers_mod._read_github_token = AsyncMock(return_value="fake-token")  # type: ignore[assignment]

    # Patch pod log collector to return empty (no real pods to read from)
    handlers_mod._collect_pod_logs = AsyncMock(return_value="")  # type: ignore[assignment]

    # Patch reviewer job builder to remove command override
    # (fake agent image is alpine, has no Python)
    original_build_reviewer = sandbox_mod.build_reviewer_job

    def patched_build_reviewer(*args: Any, **kwargs: Any) -> Any:
        job = original_build_reviewer(*args, **kwargs)
        job.spec.template.spec.containers[0].command = None
        return job

    sandbox_mod.build_reviewer_job = patched_build_reviewer  # type: ignore[assignment]

    # Patch monitor job builder similarly
    original_build_monitor = sandbox_mod.build_monitor_job

    def patched_build_monitor(*args: Any, **kwargs: Any) -> Any:
        job = original_build_monitor(*args, **kwargs)
        job.spec.template.spec.containers[0].command = None
        return job

    sandbox_mod.build_monitor_job = patched_build_monitor  # type: ignore[assignment]

    # Set env vars for the agent image
    os.environ["NUBI_AGENT_IMAGE"] = FAKE_AGENT_IMAGE
    os.environ["NUBI_AGENT_IMAGE_PULL_POLICY"] = "IfNotPresent"
    os.environ["NUBI_RUNTIME_CLASS"] = ""

    # Start kopf operator in background
    ready = asyncio.Event()
    stop = asyncio.Event()

    async def run_operator() -> None:
        await kopf.operator(
            clusterwide=True,
            ready_flag=ready,
            stop_flag=stop,
        )

    task = asyncio.create_task(run_operator())
    try:
        await asyncio.wait_for(ready.wait(), timeout=30)
        logger.info("kopf operator ready")
        yield
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()


@pytest_asyncio.fixture
async def task_name(
    controller: None,
    scenario_store: ScenarioResultStore,
) -> AsyncGenerator[str, None]:
    """Generate a unique task name and clean up after the test."""
    name = f"integ-{uuid.uuid4().hex[:8]}"
    scenario_store.reset()
    yield name
    # Cleanup — best effort, ignore errors from already-deleted resources
    with contextlib.suppress(Exception):
        await delete_taskspec(name)
    with contextlib.suppress(Exception):
        await delete_namespace(f"nubi-{name}")
