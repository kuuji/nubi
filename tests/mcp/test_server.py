"""Tests for the Nubi MCP server tools.

These tests mock the Kubernetes client calls to ensure the MCP tools
work correctly without requiring a K8s cluster.

To run these tests:
    python -m pytest tests/mcp/ -v

Note: These tests require the mcp and kubernetes modules to be mocked.
The test_run.py script in the repo root provides standalone test execution.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# Set up mocks before importing server
from tests.mcp.test_fixtures import setup_mocks

setup_mocks()

# Import fixtures after mocks are set up
from tests.mcp.test_fixtures import (  # noqa: E402
    TASK_CR,
    VALID_SPEC,
    MockApiException,
)

# =============================================================================
# create_taskspec tests
# =============================================================================


class TestCreateTaskSpec:
    """Tests for the create_taskspec tool."""

    @patch("nubi.mcp.k8s.create_taskspec")
    def test_create_taskspec_valid(self, mock_create: MagicMock) -> None:
        """Valid spec creates successfully."""
        from nubi.mcp.server import create_taskspec

        mock_create.return_value = {"metadata": {"name": "my-task"}}

        result = create_taskspec(name="my-task", spec=VALID_SPEC)

        assert "created successfully" in result
        mock_create.assert_called_once_with(
            name="my-task",
            namespace="nubi-system",
            spec=VALID_SPEC,
        )

    @patch("nubi.mcp.k8s.create_taskspec")
    def test_create_taskspec_custom_namespace(self, mock_create: MagicMock) -> None:
        """Spec creates in custom namespace."""
        from nubi.mcp.server import create_taskspec

        mock_create.return_value = {"metadata": {"name": "my-task"}}

        result = create_taskspec(
            name="my-task",
            spec=VALID_SPEC,
            namespace="custom-ns",
        )

        assert "created successfully" in result
        mock_create.assert_called_once_with(
            name="my-task",
            namespace="custom-ns",
            spec=VALID_SPEC,
        )

    def test_create_taskspec_invalid_spec(self) -> None:
        """Missing required fields returns validation error."""
        from nubi.mcp.server import create_taskspec

        # Missing required fields: description, type, inputs
        invalid_spec: dict = {}

        result = create_taskspec(name="my-task", spec=invalid_spec)

        assert "Validation error" in result
        assert "description" in result.lower() or "Field required" in result

    def test_create_taskspec_invalid_type(self) -> None:
        """Bad task type returns validation error."""
        from nubi.mcp.server import create_taskspec

        invalid_spec: dict = {
            "description": "Test",
            "type": "invalid-type",
            "inputs": {"repo": "kuuji/test"},
        }

        result = create_taskspec(name="my-task", spec=invalid_spec)

        assert "Validation error" in result
        assert "type" in result.lower()

    def test_create_taskspec_missing_inputs_repo(self) -> None:
        """Missing inputs.repo returns validation error."""
        from nubi.mcp.server import create_taskspec

        invalid_spec: dict = {
            "description": "Test",
            "type": "code-change",
            "inputs": {},  # Missing repo
        }

        result = create_taskspec(name="my-task", spec=invalid_spec)

        assert "Validation error" in result

    @patch("nubi.mcp.k8s.create_taskspec")
    def test_create_taskspec_api_error(self, mock_create: MagicMock) -> None:
        """API error returns clear error message."""
        from nubi.mcp.server import create_taskspec

        mock_create.side_effect = Exception("Connection refused")

        result = create_taskspec(name="my-task", spec=VALID_SPEC)

        assert "Error creating TaskSpec" in result
        assert "Connection refused" in result


# =============================================================================
# list_tasks tests
# =============================================================================


class TestListTasks:
    """Tests for the list_tasks tool."""

    @patch("nubi.mcp.k8s.list_taskspecs")
    def test_list_tasks_empty(self, mock_list: MagicMock) -> None:
        """No tasks returns empty list message."""
        from nubi.mcp.server import list_tasks

        mock_list.return_value = []

        result = list_tasks()

        assert "No tasks found" in result

    @patch("nubi.mcp.k8s.list_taskspecs")
    def test_list_tasks_with_results(self, mock_list: MagicMock) -> None:
        """Returns formatted task list."""
        from nubi.mcp.server import list_tasks

        mock_list.return_value = [TASK_CR]

        result = list_tasks()

        assert "NAME" in result
        assert "TYPE" in result
        assert "PHASE" in result
        assert "test-task" in result
        assert "code-change" in result
        assert "Pending" in result

    @patch("nubi.mcp.k8s.list_taskspecs")
    def test_list_tasks_phase_filter(self, mock_list: MagicMock) -> None:
        """Filters by phase when specified."""
        from nubi.mcp.server import list_tasks

        mock_list.return_value = []

        list_tasks(phase="Executing")

        mock_list.assert_called_once_with(namespace="nubi-system", phase="Executing")

    @patch("nubi.mcp.k8s.list_taskspecs")
    def test_list_tasks_custom_namespace(self, mock_list: MagicMock) -> None:
        """Lists from custom namespace."""
        from nubi.mcp.server import list_tasks

        mock_list.return_value = []

        list_tasks(namespace="custom-ns")

        mock_list.assert_called_once_with(namespace="custom-ns", phase="")

    @patch("nubi.mcp.k8s.list_taskspecs")
    def test_list_tasks_api_error(self, mock_list: MagicMock) -> None:
        """API error returns clear error message."""
        from nubi.mcp.server import list_tasks

        mock_list.side_effect = Exception("Connection timeout")

        result = list_tasks()

        assert "Error listing tasks" in result
        assert "Connection timeout" in result


# =============================================================================
# get_task_status tests
# =============================================================================


class TestGetTaskStatus:
    """Tests for the get_task_status tool."""

    @patch("nubi.mcp.k8s.get_taskspec")
    def test_get_task_status(self, mock_get: MagicMock) -> None:
        """Returns formatted status."""
        from nubi.mcp.server import get_task_status

        mock_get.return_value = TASK_CR

        result = get_task_status(name="test-task")

        assert "TaskSpec: test-task" in result
        assert "Phase: Pending" in result
        assert "Workspace:" in result
        assert "Stages:" in result
        assert "Executor:" in result
        assert "Validator:" in result
        assert "Reviewer:" in result
        assert "Gating:" in result

    @patch("nubi.mcp.k8s.get_taskspec")
    def test_get_task_status_workspace_info(self, mock_get: MagicMock) -> None:
        """Returns workspace info including branch and headSHA."""
        from nubi.mcp.server import get_task_status

        mock_get.return_value = TASK_CR

        result = get_task_status(name="test-task")

        assert "Branch:" in result
        assert "Head SHA:" in result

    @patch("nubi.mcp.k8s.get_taskspec")
    def test_get_task_status_not_found(self, mock_get: MagicMock) -> None:
        """404 returns clear error."""
        from nubi.mcp.server import get_task_status

        mock_get.side_effect = MockApiException(status=404, reason="Not Found")

        result = get_task_status(name="nonexistent")

        assert "Error getting task status" in result

    @patch("nubi.mcp.k8s.get_taskspec")
    def test_get_task_status_custom_namespace(self, mock_get: MagicMock) -> None:
        """Uses custom namespace."""
        from nubi.mcp.server import get_task_status

        mock_get.return_value = TASK_CR

        get_task_status(name="test-task", namespace="custom-ns")

        mock_get.assert_called_once_with(name="test-task", namespace="custom-ns")


# =============================================================================
# get_task_logs tests
# =============================================================================


class TestGetTaskLogs:
    """Tests for the get_task_logs tool."""

    @patch("nubi.mcp.k8s.get_pod_logs")
    def test_get_task_logs(self, mock_logs: MagicMock) -> None:
        """Returns pod logs."""
        from nubi.mcp.server import get_task_logs

        mock_logs.return_value = "Log line 1\nLog line 2\nLog line 3"

        result = get_task_logs(name="test-task", stage="executor")

        assert "Logs for task" in result
        assert "Log line 1" in result
        mock_logs.assert_called_once_with(
            name="test-task",
            namespace="nubi-system",
            stage="executor",
        )

    @patch("nubi.mcp.k8s.get_pod_logs")
    def test_get_task_logs_review_stage(self, mock_logs: MagicMock) -> None:
        """Returns logs for reviewer stage."""
        from nubi.mcp.server import get_task_logs

        mock_logs.return_value = "Reviewer logs"

        result = get_task_logs(name="test-task", stage="reviewer")

        assert "Reviewer logs" in result
        mock_logs.assert_called_once_with(
            name="test-task",
            namespace="nubi-system",
            stage="reviewer",
        )

    def test_get_task_logs_invalid_stage(self) -> None:
        """Invalid stage returns clear error."""
        from nubi.mcp.server import get_task_logs

        result = get_task_logs(name="test-task", stage="invalid-stage")

        assert "Invalid stage" in result
        assert "invalid-stage" in result

    @patch("nubi.mcp.k8s.get_pod_logs")
    def test_get_task_logs_no_pods(self, mock_logs: MagicMock) -> None:
        """No pods returns clear error."""
        from nubi.mcp.server import get_task_logs

        mock_logs.side_effect = MockApiException(
            status=404,
            reason="No pod found",
        )

        result = get_task_logs(name="test-task", stage="executor")

        assert "Error getting logs" in result

    @patch("nubi.mcp.k8s.get_pod_logs")
    def test_get_task_logs_api_error(self, mock_logs: MagicMock) -> None:
        """API error returns clear error message."""
        from nubi.mcp.server import get_task_logs

        mock_logs.side_effect = Exception("Connection reset")

        result = get_task_logs(name="test-task", stage="executor")

        assert "Error getting logs" in result


# =============================================================================
# delete_taskspec tests
# =============================================================================


class TestDeleteTaskSpec:
    """Tests for the delete_taskspec tool."""

    @patch("nubi.mcp.k8s.delete_taskspec")
    def test_delete_taskspec(self, mock_delete: MagicMock) -> None:
        """Deletes successfully."""
        from nubi.mcp.server import delete_taskspec

        mock_delete.return_value = {"status": "Success"}

        result = delete_taskspec(name="test-task")

        assert "deleted successfully" in result
        mock_delete.assert_called_once_with(name="test-task", namespace="nubi-system")

    @patch("nubi.mcp.k8s.delete_taskspec")
    def test_delete_taskspec_custom_namespace(self, mock_delete: MagicMock) -> None:
        """Deletes from custom namespace."""
        from nubi.mcp.server import delete_taskspec

        mock_delete.return_value = {"status": "Success"}

        result = delete_taskspec(name="test-task", namespace="custom-ns")

        assert "deleted successfully" in result
        mock_delete.assert_called_once_with(name="test-task", namespace="custom-ns")

    @patch("nubi.mcp.k8s.delete_taskspec")
    def test_delete_taskspec_not_found(self, mock_delete: MagicMock) -> None:
        """404 returns clear error."""
        from nubi.mcp.server import delete_taskspec

        mock_delete.side_effect = MockApiException(status=404, reason="Not Found")

        result = delete_taskspec(name="nonexistent")

        assert "Error deleting TaskSpec" in result

    @patch("nubi.mcp.k8s.delete_taskspec")
    def test_delete_taskspec_api_error(self, mock_delete: MagicMock) -> None:
        """API error returns clear error message."""
        from nubi.mcp.server import delete_taskspec

        mock_delete.side_effect = Exception("Connection refused")

        result = delete_taskspec(name="test-task")

        assert "Error deleting TaskSpec" in result
        assert "Connection refused" in result
