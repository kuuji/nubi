"""Tests for nubi.tools.github_api — GitHub API helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.tools.github_api import (
    _pr_number_from_url,
    configure,
    format_pipeline_summary,
    mark_pr_ready,
    post_pipeline_summary,
    update_pr_from_url,
)


class TestPrNumberFromUrl:
    def test_standard_url(self) -> None:
        assert _pr_number_from_url("https://github.com/kuuji/nubi/pull/42") == 42

    def test_trailing_slash(self) -> None:
        assert _pr_number_from_url("https://github.com/kuuji/nubi/pull/42/") == 42

    def test_invalid_url(self) -> None:
        assert _pr_number_from_url("https://github.com/kuuji/nubi") is None

    def test_non_numeric(self) -> None:
        assert _pr_number_from_url("https://github.com/kuuji/nubi/pull/abc") is None

    def test_empty(self) -> None:
        assert _pr_number_from_url("") is None


class TestUpdatePrFromUrl:
    @patch("nubi.tools.github_api.httpx.patch")
    def test_updates_pr(self, mock_patch: MagicMock) -> None:
        configure(repo="kuuji/nubi", base_branch="main", task_branch="nubi/t1", token="tok")
        mock_patch.return_value = MagicMock(status_code=200)

        update_pr_from_url("https://github.com/kuuji/nubi/pull/42", "title", "body")

        mock_patch.assert_called_once()
        call_args = mock_patch.call_args
        assert "/pulls/42" in call_args[0][0]
        assert call_args[1]["json"] == {"title": "title", "body": "body"}

    @patch("nubi.tools.github_api.httpx.patch")
    def test_invalid_url_skips(self, mock_patch: MagicMock) -> None:
        update_pr_from_url("not-a-url", "title", "body")
        mock_patch.assert_not_called()


class TestMarkPrReady:
    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api.httpx.get")
    def test_marks_ready_via_graphql(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        configure(repo="kuuji/nubi", base_branch="main", task_branch="nubi/t1", token="tok")
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"node_id": "PR_abc123"},
        )
        mock_post.return_value = MagicMock(status_code=200)

        mark_pr_ready("https://github.com/kuuji/nubi/pull/42")

        mock_get.assert_called_once()
        assert "/pulls/42" in mock_get.call_args[0][0]
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "graphql" in call_args[0][0]
        assert call_args[1]["json"]["variables"]["id"] == "PR_abc123"

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api.httpx.get")
    def test_skips_on_pr_fetch_failure(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        configure(repo="kuuji/nubi", base_branch="main", task_branch="nubi/t1", token="tok")
        mock_get.return_value = MagicMock(status_code=404)

        mark_pr_ready("https://github.com/kuuji/nubi/pull/42")

        mock_post.assert_not_called()

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api.httpx.get")
    def test_skips_on_invalid_url(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        mark_pr_ready("not-a-url")

        mock_get.assert_not_called()
        mock_post.assert_not_called()


class TestFormatPipelineSummary:
    """Tests for the markdown formatting of pipeline summary."""

    def test_complete_data(self) -> None:
        """Test formatting with complete data from all stages."""
        executor_data = {
            "status": "success",
            "commit_sha": "a1b2c3d4e5f6",
            "summary": "Added rate limiting middleware",
            "error": "",
        }
        gates_data = {
            "gates": [
                {
                    "name": "ruff",
                    "category": "lint",
                    "status": "passed",
                    "output": "0 errors",
                    "error": "",
                },
                {
                    "name": "pytest",
                    "category": "test",
                    "status": "passed",
                    "output": "12 passed, 0 failed",
                    "error": "",
                },
            ],
            "overall_passed": True,
        }
        review_data = {
            "decision": "approve",
            "feedback": "Clean implementation.",
        }
        monitor_data = {
            "decision": "approve",
            "ci_status": "success",
        }

        result = format_pipeline_summary(
            task_id="add-rate-limiting",
            branch="nubi/add-rate-limiting-a1b2c3",
            executor_data=executor_data,
            gates_data=gates_data,
            review_data=review_data,
            monitor_data=monitor_data,
            ci_status="success",
        )

        assert "## Nubi Pipeline Summary" in result
        assert "**Task:** `add-rate-limiting`" in result
        assert "**Branch:** `nubi/add-rate-limiting-a1b2c3`" in result
        assert "### Executor" in result
        assert "✅ Complete" in result
        # Commit SHA is truncated to 8 characters
        assert "`a1b2c3d4`" in result
        assert "### Gates" in result
        assert "| ruff | ✅ pass |" in result
        assert "| pytest | ✅ pass |" in result
        assert "### Reviewer" in result
        assert "✅ Approve" in result
        assert "Clean implementation." in result
        assert "### Monitor" in result
        assert "<!-- nubi-pipeline-summary -->" in result
        assert ".nubi/add-rate-limiting/" in result

    def test_missing_executor(self) -> None:
        """Test formatting when executor data is missing."""
        result = format_pipeline_summary(
            task_id="test-task",
            branch="nubi/test-task-123",
            executor_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )

        assert "## Nubi Pipeline Summary" in result
        assert "### Executor" in result
        assert "⏭ Skipped" in result
        assert "<!-- nubi-pipeline-summary -->" in result

    def test_skipped_review(self) -> None:
        """Test formatting when review was skipped."""
        result = format_pipeline_summary(
            task_id="test-task",
            branch="nubi/test-task-123",
            executor_data={"status": "success", "summary": "Done"},
            gates_data={"gates": []},
            review_data=None,  # Skipped
            monitor_data={"decision": "approve"},
        )

        assert "### Reviewer" in result
        assert "⏭ Skipped" in result

    def test_failed_gates(self) -> None:
        """Test formatting with failed gates."""
        gates_data = {
            "gates": [
                {
                    "name": "ruff",
                    "category": "lint",
                    "status": "failed",
                    "output": "unused import",
                    "error": "3 errors",
                },
            ],
        }
        result = format_pipeline_summary(
            task_id="test-task",
            branch="nubi/test-task-123",
            executor_data={"status": "success"},
            gates_data=gates_data,
            review_data={"decision": "approve"},
            monitor_data={"decision": "approve"},
        )

        assert "| ruff | ❌ fail |" in result
        assert "3 errors" in result

    def test_skipped_gates(self) -> None:
        """Test formatting with skipped gates."""
        gates_data = {
            "gates": [
                {
                    "name": "pytest",
                    "category": "test",
                    "status": "skipped",
                    "output": "",
                    "error": "",
                    "skipped_reason": "blocked by lint failure",
                },
            ],
        }
        result = format_pipeline_summary(
            task_id="test-task",
            branch="nubi/test-task-123",
            executor_data={"status": "success"},
            gates_data=gates_data,
            review_data={"decision": "approve"},
            monitor_data={"decision": "approve"},
        )

        assert "| pytest | ⏭ skipped |" in result
        assert "blocked by lint failure" in result

    def test_ci_status_in_monitor(self) -> None:
        """Test that CI status is displayed in monitor section."""
        monitor_data = {
            "decision": "approve",
            "ci_status": "success",
        }
        result = format_pipeline_summary(
            task_id="test-task",
            branch="nubi/test-task-123",
            executor_data={"status": "success"},
            gates_data={"gates": []},
            review_data={"decision": "approve"},
            monitor_data=monitor_data,
            ci_status="success",
        )

        assert "### Monitor" in result
        assert "✅ All checks passed" in result

    def test_ci_failed_status(self) -> None:
        """Test formatting with CI failure."""
        monitor_data = {
            "decision": "flag",
            "ci_status": "failure",
        }
        result = format_pipeline_summary(
            task_id="test-task",
            branch="nubi/test-task-123",
            executor_data={"status": "success"},
            gates_data={"gates": []},
            review_data={"decision": "approve"},
            monitor_data=monitor_data,
            ci_status="failure",
        )

        assert "❌ Failed" in result


class TestPostPipelineSummary:
    """Tests for the post_pipeline_summary function."""

    @patch("nubi.tools.github_api._read_artifact")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    @patch("nubi.tools.github_api._post_comment")
    def test_posts_new_comment(
        self,
        mock_post: MagicMock,
        mock_find: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        """Test posting a new comment when no existing comment found."""
        mock_find.return_value = None  # No existing comment
        mock_post.return_value = True
        mock_read.side_effect = [
            {"status": "success", "commit_sha": "abc123", "summary": "Done"},
            {"gates": []},
            {"decision": "approve", "feedback": "LGTM"},
            {"decision": "approve"},
        ]

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/test-task-abc123",
            token="test-token",
        )

        assert result == "Pipeline summary posted"
        mock_post.assert_called_once()
        # Verify the comment body contains expected content
        call_args = mock_post.call_args
        body = call_args[0][1]
        assert "## Nubi Pipeline Summary" in body
        assert "<!-- nubi-pipeline-summary -->" in body

    @patch("nubi.tools.github_api._read_artifact")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    @patch("nubi.tools.github_api._update_comment")
    def test_updates_existing_comment(
        self,
        mock_update: MagicMock,
        mock_find: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        """Test updating an existing comment when found by marker."""
        mock_find.return_value = 12345  # Existing comment ID
        mock_update.return_value = True
        mock_read.side_effect = [
            {"status": "success", "commit_sha": "abc123", "summary": "Done"},
            {"gates": []},
            {"decision": "approve", "feedback": "LGTM"},
            {"decision": "approve"},
        ]

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/test-task-abc123",
            token="test-token",
        )

        assert result == "Pipeline summary updated: comment #12345"
        mock_update.assert_called_once()
        # Verify update was called with the correct comment ID
        call_args = mock_update.call_args
        assert call_args[0][0] == 12345

    @patch("nubi.tools.github_api._read_artifact")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    @patch("nubi.tools.github_api._post_comment")
    def test_handles_missing_artifacts(
        self,
        mock_post: MagicMock,
        mock_find: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        """Test graceful handling when artifact files are missing."""
        mock_find.return_value = None
        mock_post.return_value = True
        mock_read.return_value = None  # All artifacts missing

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/test-task-abc123",
            token="test-token",
        )

        assert result == "Pipeline summary posted"
        call_args = mock_post.call_args
        body = call_args[0][1]
        # All sections should show skipped
        assert "⏭ Skipped" in body

    @patch("nubi.tools.github_api._read_artifact")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    @patch("nubi.tools.github_api._post_comment")
    def test_handles_invalid_pr_url(
        self,
        mock_post: MagicMock,
        mock_find: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        """Test handling of invalid PR URL."""
        result = post_pipeline_summary(
            pr_url="not-a-valid-url",
            repo="kuuji/nubi",
            branch="nubi/test-task-abc123",
            token="test-token",
        )

        assert "Could not extract PR number" in result
        mock_post.assert_not_called()
        mock_find.assert_not_called()
        mock_read.assert_not_called()

    @patch("nubi.tools.github_api._read_artifact")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    @patch("nubi.tools.github_api._update_comment")
    def test_handles_update_failure(
        self,
        mock_update: MagicMock,
        mock_find: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        """Test handling when update of existing comment fails."""
        mock_find.return_value = 12345
        mock_update.return_value = False  # Update fails
        mock_read.side_effect = [
            {"status": "success"},
            {"gates": []},
            {"decision": "approve"},
            {"decision": "approve"},
        ]

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/test-task-abc123",
            token="test-token",
        )

        assert "Failed to update" in result
