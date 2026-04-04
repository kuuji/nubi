"""Tests for nubi.controller.namespace — task namespace lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes_asyncio.client import ApiException

from nubi.controller.namespace import (
    delete_task_namespace,
    ensure_task_namespace,
    task_namespace_name,
)
from nubi.crd.schema import TaskConstraints
from nubi.exceptions import NamespaceError

# -- Helpers -----------------------------------------------------------------


def _constraints(**overrides: object) -> TaskConstraints:
    """Build TaskConstraints, optionally overriding nested resource fields."""
    resources = {
        "cpu": overrides.pop("cpu", "1"),
        "memory": overrides.pop("memory", "512Mi"),
    }
    return TaskConstraints(
        network_access=overrides.get("network_access", []),  # type: ignore[arg-type]
        resources=resources,  # type: ignore[arg-type]
    )


def _api_exc(status: int, reason: str = "Error") -> ApiException:
    resp = MagicMock()
    resp.status = status
    resp.reason = reason
    resp.data = ""
    return ApiException(status=status, reason=reason, http_resp=resp)


# -- task_namespace_name -----------------------------------------------------


class TestTaskNamespaceName:
    def test_prefix(self) -> None:
        assert task_namespace_name("my-task") == "nubi-my-task"

    def test_truncates_to_63_chars(self) -> None:
        result = task_namespace_name("a" * 200)
        assert len(result) <= 63

    def test_truncated_keeps_prefix(self) -> None:
        result = task_namespace_name("b" * 200)
        assert result.startswith("nubi-")


# -- ensure_task_namespace ---------------------------------------------------


class TestEnsureTaskNamespace:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        core_p = patch("nubi.controller.namespace.CoreV1Api")
        net_p = patch("nubi.controller.namespace.NetworkingV1Api")

        self.mock_core_cls = core_p.start()
        self.mock_net_cls = net_p.start()

        self.mock_core = MagicMock()
        self.mock_net = MagicMock()
        self.mock_core_cls.return_value = self.mock_core
        self.mock_net_cls.return_value = self.mock_net

        self.mock_core.create_namespace = AsyncMock()
        self.mock_core.create_namespaced_resource_quota = AsyncMock()
        self.mock_net.create_namespaced_network_policy = AsyncMock()

        yield

        core_p.stop()
        net_p.stop()

    async def test_returns_namespace_name(self) -> None:
        result = await ensure_task_namespace("task-1", "code-change", _constraints())
        assert result == "nubi-task-1"

    async def test_calls_create_namespace(self) -> None:
        await ensure_task_namespace("task-1", "code-change", _constraints())
        self.mock_core.create_namespace.assert_awaited_once()

    async def test_calls_create_resource_quota(self) -> None:
        await ensure_task_namespace("task-1", "code-change", _constraints())
        self.mock_core.create_namespaced_resource_quota.assert_awaited_once()

    async def test_calls_create_network_policy(self) -> None:
        await ensure_task_namespace("task-1", "code-change", _constraints())
        self.mock_net.create_namespaced_network_policy.assert_awaited_once()


# -- Namespace labels --------------------------------------------------------


class TestCreateNamespaceLabels:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        core_p = patch("nubi.controller.namespace.CoreV1Api")
        net_p = patch("nubi.controller.namespace.NetworkingV1Api")

        self.mock_core_cls = core_p.start()
        self.mock_net_cls = net_p.start()

        self.mock_core = MagicMock()
        self.mock_net = MagicMock()
        self.mock_core_cls.return_value = self.mock_core
        self.mock_net_cls.return_value = self.mock_net

        self.mock_core.create_namespace = AsyncMock()
        self.mock_core.create_namespaced_resource_quota = AsyncMock()
        self.mock_net.create_namespaced_network_policy = AsyncMock()

        yield

        core_p.stop()
        net_p.stop()

    def _ns_labels(self) -> dict[str, str]:
        call_args = self.mock_core.create_namespace.call_args
        body = call_args.kwargs.get("body") or call_args[0][0]
        return body.metadata.labels

    async def test_pss_label(self) -> None:
        await ensure_task_namespace("task-1", "code-change", _constraints())
        assert self._ns_labels()["pod-security.kubernetes.io/enforce"] == "restricted"

    async def test_task_id_label(self) -> None:
        await ensure_task_namespace("task-1", "code-change", _constraints())
        assert self._ns_labels()["nubi.io/task-id"] == "task-1"

    async def test_task_type_label(self) -> None:
        await ensure_task_namespace("task-1", "code-change", _constraints())
        assert self._ns_labels()["nubi.io/task-type"] == "code-change"

    async def test_managed_by_label(self) -> None:
        await ensure_task_namespace("task-1", "code-change", _constraints())
        assert self._ns_labels()["app.kubernetes.io/managed-by"] == "nubi"


# -- Namespace 409 / error handling ------------------------------------------


class TestCreateNamespaceIdempotency:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        core_p = patch("nubi.controller.namespace.CoreV1Api")
        net_p = patch("nubi.controller.namespace.NetworkingV1Api")

        self.mock_core_cls = core_p.start()
        self.mock_net_cls = net_p.start()

        self.mock_core = MagicMock()
        self.mock_net = MagicMock()
        self.mock_core_cls.return_value = self.mock_core
        self.mock_net_cls.return_value = self.mock_net

        self.mock_core.create_namespace = AsyncMock()
        self.mock_core.create_namespaced_resource_quota = AsyncMock()
        self.mock_net.create_namespaced_network_policy = AsyncMock()

        yield

        core_p.stop()
        net_p.stop()

    async def test_409_on_namespace_does_not_raise(self) -> None:
        self.mock_core.create_namespace.side_effect = _api_exc(409, "Conflict")
        await ensure_task_namespace("task-1", "code-change", _constraints())

    async def test_non_409_on_namespace_raises(self) -> None:
        self.mock_core.create_namespace.side_effect = _api_exc(500, "Internal")
        with pytest.raises(NamespaceError):
            await ensure_task_namespace("task-1", "code-change", _constraints())


# -- ResourceQuota -----------------------------------------------------------


class TestCreateResourceQuota:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        core_p = patch("nubi.controller.namespace.CoreV1Api")
        net_p = patch("nubi.controller.namespace.NetworkingV1Api")

        self.mock_core_cls = core_p.start()
        self.mock_net_cls = net_p.start()

        self.mock_core = MagicMock()
        self.mock_net = MagicMock()
        self.mock_core_cls.return_value = self.mock_core
        self.mock_net_cls.return_value = self.mock_net

        self.mock_core.create_namespace = AsyncMock()
        self.mock_core.create_namespaced_resource_quota = AsyncMock()
        self.mock_net.create_namespaced_network_policy = AsyncMock()

        yield

        core_p.stop()
        net_p.stop()

    def _quota_hard(self) -> dict[str, str]:
        call_args = self.mock_core.create_namespaced_resource_quota.call_args
        body = call_args.kwargs.get("body") or call_args[0][0]
        return body.spec.hard

    async def test_cpu_matches_constraints(self) -> None:
        await ensure_task_namespace("t", "code-change", _constraints(cpu="2"))
        assert self._quota_hard()["requests.cpu"] == "2"
        assert self._quota_hard()["limits.cpu"] == "2"

    async def test_memory_matches_constraints(self) -> None:
        await ensure_task_namespace("t", "code-change", _constraints(memory="1Gi"))
        assert self._quota_hard()["requests.memory"] == "1Gi"
        assert self._quota_hard()["limits.memory"] == "1Gi"

    async def test_pods_limit_is_4(self) -> None:
        await ensure_task_namespace("t", "code-change", _constraints())
        assert self._quota_hard()["pods"] == "4"

    async def test_409_does_not_raise(self) -> None:
        self.mock_core.create_namespaced_resource_quota.side_effect = _api_exc(409)
        await ensure_task_namespace("t", "code-change", _constraints())

    async def test_non_409_raises(self) -> None:
        self.mock_core.create_namespaced_resource_quota.side_effect = _api_exc(500)
        with pytest.raises(NamespaceError):
            await ensure_task_namespace("t", "code-change", _constraints())


# -- NetworkPolicy -----------------------------------------------------------


class TestCreateNetworkPolicy:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        core_p = patch("nubi.controller.namespace.CoreV1Api")
        net_p = patch("nubi.controller.namespace.NetworkingV1Api")

        self.mock_core_cls = core_p.start()
        self.mock_net_cls = net_p.start()

        self.mock_core = MagicMock()
        self.mock_net = MagicMock()
        self.mock_core_cls.return_value = self.mock_core
        self.mock_net_cls.return_value = self.mock_net

        self.mock_core.create_namespace = AsyncMock()
        self.mock_core.create_namespaced_resource_quota = AsyncMock()
        self.mock_net.create_namespaced_network_policy = AsyncMock()

        yield

        core_p.stop()
        net_p.stop()

    def _netpol_body(self) -> object:
        call_args = self.mock_net.create_namespaced_network_policy.call_args
        return call_args.kwargs.get("body") or call_args[0][0]

    async def test_dns_egress_always_present(self) -> None:
        await ensure_task_namespace("t", "code-change", _constraints(network_access=[]))
        netpol = self._netpol_body()
        dns_found = any(
            port.port == 53 for rule in netpol.spec.egress for port in (rule.ports or [])
        )
        assert dns_found, "DNS egress (port 53) must always be present"

    async def test_empty_network_access_only_dns(self) -> None:
        await ensure_task_namespace("t", "code-change", _constraints(network_access=[]))
        netpol = self._netpol_body()
        assert len(netpol.spec.egress) == 1

    async def test_nonempty_network_access_adds_http(self) -> None:
        c = _constraints(network_access=["github.com"])
        await ensure_task_namespace("t", "code-change", c)
        netpol = self._netpol_body()
        assert len(netpol.spec.egress) > 1
        http_ports = {
            port.port
            for rule in netpol.spec.egress
            for port in (rule.ports or [])
            if port.port in (80, 443)
        }
        assert 80 in http_ports
        assert 443 in http_ports

    async def test_denies_all_ingress(self) -> None:
        await ensure_task_namespace("t", "code-change", _constraints())
        netpol = self._netpol_body()
        assert "Ingress" in netpol.spec.policy_types
        assert netpol.spec.ingress is None or netpol.spec.ingress == []

    async def test_409_does_not_raise(self) -> None:
        self.mock_net.create_namespaced_network_policy.side_effect = _api_exc(409)
        await ensure_task_namespace("t", "code-change", _constraints())

    async def test_non_409_raises(self) -> None:
        self.mock_net.create_namespaced_network_policy.side_effect = _api_exc(500)
        with pytest.raises(NamespaceError):
            await ensure_task_namespace("t", "code-change", _constraints())


# -- delete_task_namespace ---------------------------------------------------


class TestDeleteTaskNamespace:
    @pytest.fixture(autouse=True)
    def _mock_k8s(self) -> None:
        core_p = patch("nubi.controller.namespace.CoreV1Api")
        self.mock_core_cls = core_p.start()
        self.mock_core = MagicMock()
        self.mock_core_cls.return_value = self.mock_core
        self.mock_core.delete_namespace = AsyncMock()

        yield

        core_p.stop()

    async def test_calls_delete(self) -> None:
        await delete_task_namespace("nubi-task-1")
        self.mock_core.delete_namespace.assert_awaited_once_with(name="nubi-task-1")

    async def test_404_does_not_raise(self) -> None:
        self.mock_core.delete_namespace.side_effect = _api_exc(404, "Not Found")
        await delete_task_namespace("nubi-task-1")

    async def test_non_404_raises(self) -> None:
        self.mock_core.delete_namespace.side_effect = _api_exc(500, "Internal")
        with pytest.raises(NamespaceError):
            await delete_task_namespace("nubi-task-1")
