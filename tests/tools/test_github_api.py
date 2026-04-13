"""Tests for nubi.tools.github_api — GitHub API helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.tools.github_api import (
    _format_pipeline_summary,
    _gate_status_icon,
    _pr_number_from_url,
    configure,
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


class TestGateStatusIcon:
    def test_passed(self) -> None:
        assert _gate_status_icon("passed") == "✅"

    def test_failed(self) -> None:
        assert _gate_status_icon("failed") == "❌"

    def test_skipped(self) -> None:
        assert _gate_status_icon("skipped") == "⏭"

    def test_unknown(self) -> None:
        assert _gate_status_icon("unknown") == "❓"


class TestFormatPipelineSummary:
    """Tests for the markdown formatting of pipeline summary."""

    def test_complete_data(self) -> None:
        """Test formatting with complete data from all stages."""
        executor_data = {
            "status": "success",
            "commit_sha": "a1b2c3d4e5f6",
            "summary": "Added rate limiting middleware",
            "attempts": 1,
        }
        gates_data = {
            "gates": [
                {"name": "ruff", "status": "passed", "output": "0 errors"},
                {"name": "pytest", "status": "passed", "output": "12 passed, 0 failed"},
            ],
            "attempt": 1,
        }
        review_data = {
            "decision": "approve",
            "feedback": "Clean implementation.",
        }
        monitor_data = {
            "decision": "approve",
            "ci_status": "success",
        }

        summary = _format_pipeline_summary(
            task_id="add-rate-limiting",
            executor_data=executor_data,
            gates_data=gates_data,
            review_data=review_data,
            monitor_data=monitor_data,
            ci_status="success",
        )

        # Verify key sections are present
        assert "## Nubi Pipeline Summary" in summary
        assert "**Task:** `add-rate-limiting`" in summary
        assert "### Executor" in summary
        assert "✅ success" in summary
        assert "### Gates" in summary
        assert "ruff" in summary
        assert "pytest" in summary
        assert "✅ passed" in summary
        assert "### Reviewer" in summary
        assert "✅ approve" in summary
        assert "Clean implementation" in summary
        assert "### Monitor" in summary
        assert "<!-- nubi-pipeline-summary -->" in summary

    def test_missing_executor(self) -> None:
        """Test formatting when executor data is missing."""
        summary = _format_pipeline_summary(
            task_id="test-task",
            executor_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
            ci_status="",
        )

        assert "### Executor" in summary
        assert "⏭ Skipped" in summary

    def test_missing_review(self) -> None:
        """Test formatting when review was skipped."""
        executor_data = {"status": "success", "commit_sha": "abc123", "summary": "Done"}
        gates_data = {"gates": [], "attempt": 1}

        summary = _format_pipeline_summary(
            task_id="test-task",
            executor_data=executor_data,
            gates_data=gates_data,
            review_data=None,
            monitor_data=None,
            ci_status="success",
        )

        assert "### Reviewer" in summary
        assert "⏭ Skipped" in summary

    def test_missing_monitor(self) -> None:
        """Test formatting when monitor data is missing."""
        executor_data = {"status": "success", "commit_sha": "abc123", "summary": "Done"}

        summary = _format_pipeline_summary(
            task_id="test-task",
            executor_data=executor_data,
            gates_data=None,
            review_data=None,
            monitor_data=None,
            ci_status="success",
        )

        assert "### Monitor" in summary
        # Decision should show skipped but CI status should still show
        assert "CI Status" in summary

    def test_gates_with_failures(self) -> None:
        """Test formatting gates with a failed gate."""
        gates_data = {
            "gates": [
                {"name": "ruff", "status": "failed", "output": "3 errors (unused import, line...)"},
                {"name": "pytest", "status": "skipped", "output": "blocked by lint failure"},
            ],
            "attempt": 1,
        }

        summary = _format_pipeline_summary(
            task_id="test-task",
            executor_data={"status": "success"},
            gates_data=gates_data,
            review_data=None,
            monitor_data=None,
            ci_status="failure",
        )

        assert "❌ failed" in summary
        assert "⏭ skipped" in summary
        assert "3 errors" in summary

    def test_reviewer_request_changes(self) -> None:
        """Test formatting with request-changes decision."""
        review_data = {
            "decision": "request-changes",
            "feedback": "Please fix the type annotations.",
        }

        summary = _format_pipeline_summary(
            task_id="test-task",
            executor_data={"status": "success"},
            gates_data=None,
            review_data=review_data,
            monitor_data=None,
            ci_status="success",
        )

        assert "🔄 request-changes" in summary
        assert "Please fix the type annotations" in summary

    def test_ci_status_variations(self) -> None:
        """Test different CI status values are formatted correctly."""
        for ci_status, expected_icon in [
            ("success", "✅"),
            ("failure", "❌"),
            ("timed_out", "❌"),
            ("running", "⏳"),
        ]:
            summary = _format_pipeline_summary(
                task_id="test-task",
                executor_data=None,
                gates_data=None,
                review_data=None,
                monitor_data=None,
                ci_status=ci_status,
            )
            assert expected_icon in summary, f"Failed for ci_status={ci_status}"

    def test_long_output_truncated(self) -> None:
        """Test that long gate output is truncated."""
        gates_data = {
            "gates": [
                {
                    "name": "pytest",
                    "status": "failed",
                    "output": "x" * 500,  # Very long output
                },
            ],
            "attempt": 1,
        }

        summary = _format_pipeline_summary(
            task_id="test-task",
            executor_data={"status": "success"},
            gates_data=gates_data,
            review_data=None,
            monitor_data=None,
            ci_status="failure",
        )

        # Output should be truncated
        assert "..." in summary
        # Should not contain the full 500-character output
        assert ("x" * 500) not in summary


class TestPostPipelineSummary:
    """Tests for post_pipeline_summary function."""

    @patch("nubi.tools.github_api._post_comment")
    @patch("nubi.tools.github_api._read_artifact_file")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    def test_posts_new_comment(
        self,
        mock_find: MagicMock,
        mock_read: MagicMock,
        mock_post: MagicMock,
    ) -> None:
        """Test posting a new comment when no existing comment exists."""
        mock_find.return_value = None
        mock_read.return_value = {"status": "success"}
        mock_post.return_value = True

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/test-task",
            token="test-token",
        )

        assert result is True
        mock_post.assert_called_once()
        # Verify it was a POST (not PATCH) - existing_comment_id should be None
        call_args = mock_post.call_args
        assert call_args[0][0] == 42  # pr_number
        assert "<!-- nubi-pipeline-summary -->" in call_args[0][1]  # body

    @patch("nubi.tools.github_api._post_comment")
    @patch("nubi.tools.github_api._read_artifact_file")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    def test_updates_existing_comment(
        self,
        mock_find: MagicMock,
        mock_read: MagicMock,
        mock_post: MagicMock,
    ) -> None:
        """Test updating an existing comment when marker is found."""
        mock_find.return_value = {"id": 12345, "body": "old content"}
        mock_read.return_value = {"status": "success"}
        mock_post.return_value = True

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/test-task",
            token="test-token",
        )

        assert result is True
        mock_post.assert_called_once()
        # Verify PATCH was called with existing comment ID
        call_args = mock_post.call_args
        assert call_args[0][0] == 42  # pr_number
        assert call_args[0][2] == 12345  # existing_comment_id

    @patch("nubi.tools.github_api._post_comment")
    @patch("nubi.tools.github_api._read_artifact_file")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    def test_handles_missing_artifact_files(
        self,
        mock_find: MagicMock,
        mock_read: MagicMock,
        mock_post: MagicMock,
    ) -> None:
        """Test that missing artifact files are handled gracefully."""
        mock_find.return_value = None
        mock_read.side_effect = [None, None, None, None]  # All artifacts missing
        mock_post.return_value = True

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/test-task",
            token="test-token",
        )

        assert result is True
        # Should still post comment with "Skipped" for missing sections
        call_args = mock_post.call_args
        assert "⏭ Skipped" in call_args[0][1]

    def test_invalid_pr_url(self) -> None:
        """Test handling of invalid PR URL."""
        result = post_pipeline_summary(
            pr_url="not-a-valid-url",
            repo="kuuji/nubi",
            branch="nubi/test-task",
            token="test-token",
        )

        assert result is False

    @patch("nubi.tools.github_api._headers")
    @patch("nubi.tools.github_api._pr_number_from_url")
    def test_handles_api_error(
        self,
        mock_pr_num: MagicMock,
        mock_headers: MagicMock,
    ) -> None:
        """Test handling of API errors gracefully."""
        mock_pr_num.return_value = 42
        mock_headers.return_value = {"Authorization": "Bearer test"}

        # Mock httpx to simulate an error during artifact reading
        with patch("nubi.tools.github_api.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=500, text="Server error")

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="test-token",
            )

            # Should return False on API error
            assert result is False
