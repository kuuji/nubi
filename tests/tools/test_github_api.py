"""Tests for nubi.tools.github_api — GitHub API helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.tools.github_api import (
    PIPELINE_SUMMARY_MARKER,
    _pr_number_from_url,
    configure,
    format_pipeline_summary_markdown,
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


# ---------------------------------------------------------------------------
# Pipeline Summary Tests
# ---------------------------------------------------------------------------


class TestFormatPipelineSummaryMarkdown:
    """Tests for format_pipeline_summary_markdown."""

    def test_complete_data(self) -> None:
        """Test formatting with complete data from all stages."""
        result = {
            "decision": "approve",
            "summary": "Added rate limiting middleware with token bucket algorithm",
            "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
        }
        gates = {
            "gates": [
                {
                    "name": "ruff",
                    "status": "passed",
                    "output": "Success: no issues found",
                },
                {
                    "name": "pytest",
                    "status": "passed",
                    "output": "12 passed, 0 failed",
                },
            ],
            "overall_passed": True,
        }
        review = {
            "decision": "approve",
            "feedback": "Clean implementation. Token bucket is the right choice.",
        }
        monitor = {
            "decision": "approve",
            "ci_status": "success",
        }

        markdown = format_pipeline_summary_markdown(
            task_id="add-rate-limiting",
            result=result,
            gates=gates,
            review=review,
            monitor=monitor,
        )

        # Check marker
        assert PIPELINE_SUMMARY_MARKER in markdown

        # Check task info
        assert "add-rate-limiting" in markdown
        assert "nubi/add-rate-limiting" in markdown

        # Check executor section
        assert "### Executor" in markdown
        assert "✅ Complete" in markdown
        assert "a1b2c3d" in markdown

        # Check gates section
        assert "### Gates" in markdown
        assert "ruff" in markdown
        assert "✅ pass" in markdown
        assert "pytest" in markdown

        # Check reviewer section
        assert "### Reviewer" in markdown
        assert "✅ Approve" in markdown
        assert "Clean implementation" in markdown

        # Check monitor section
        assert "### Monitor" in markdown
        assert "✅ Approve" in markdown
        assert "✅ All checks passed" in markdown

        # Check footer
        assert ".nubi/add-rate-limiting/" in markdown
        assert "Generated by [Nubi]" in markdown

    def test_missing_executor_result(self) -> None:
        """Test formatting when executor result is missing."""
        markdown = format_pipeline_summary_markdown(
            task_id="test-task",
            result=None,
            gates=None,
            review=None,
            monitor=None,
        )

        assert "### Executor" in markdown
        assert "⚠️ Not available" in markdown

    def test_gates_failed(self) -> None:
        """Test formatting when gates have failures."""
        gates = {
            "gates": [
                {
                    "name": "ruff",
                    "status": "failed",
                    "output": "errors: unused import, line too long",
                },
                {
                    "name": "pytest",
                    "status": "skipped",
                    "output": "blocked by lint failure",
                },
            ],
        }

        markdown = format_pipeline_summary_markdown(
            task_id="test-task",
            result={"decision": "approve", "summary": "Test"},
            gates=gates,
        )

        assert "❌ fail" in markdown
        assert "⏭ skipped" in markdown
        assert "<details>" in markdown  # Collapsible section for failures

    def test_reviewer_skipped(self) -> None:
        """Test formatting when reviewer is skipped."""
        markdown = format_pipeline_summary_markdown(
            task_id="test-task",
            result={"decision": "approve", "summary": "Test"},
            review=None,  # Skipped
        )

        assert "### Reviewer" in markdown
        assert "⏭ Skipped" in markdown

    def test_monitor_with_ci_failure(self) -> None:
        """Test formatting when CI has failed."""
        monitor = {
            "decision": "ci-failed",
            "ci_status": "failure",
        }

        markdown = format_pipeline_summary_markdown(
            task_id="test-task",
            monitor=monitor,
        )

        assert "### Monitor" in markdown
        assert "❌ CI Failed" in markdown
        assert "❌ Checks failed" in markdown

    def test_monitor_escalated(self) -> None:
        """Test formatting when monitor has escalated."""
        monitor = {
            "decision": "escalate",
            "ci_status": "timed_out",
        }

        markdown = format_pipeline_summary_markdown(
            task_id="test-task",
            monitor=monitor,
        )

        assert "🔺 Escalated" in markdown
        assert "⏱️ Timed out" in markdown

    def test_reviewer_request_changes(self) -> None:
        """Test formatting when reviewer requests changes."""
        review = {
            "decision": "request_changes",
            "feedback": "Please fix the error handling in server.py",
        }

        markdown = format_pipeline_summary_markdown(
            task_id="test-task",
            review=review,
        )

        assert "⚠️ Request Changes" in markdown
        assert "Please fix the error handling" in markdown


class TestPostPipelineSummary:
    """Tests for post_pipeline_summary."""

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api._read_artifact")
    @patch("nubi.tools.github_api._find_existing_pipeline_comment")
    def test_posts_new_comment(
        self,
        mock_find: MagicMock,
        mock_read: MagicMock,
        mock_post: MagicMock,
    ) -> None:
        """Test posting a new comment when no existing comment found."""
        mock_find.return_value = None  # No existing comment
        mock_read.side_effect = [
            {"decision": "approve", "summary": "Test", "commit_sha": "abc123"},
            {"gates": []},
            {"decision": "approve", "feedback": "Looks good"},
            {"decision": "approve", "ci_status": "success"},
        ]
        mock_post.return_value = MagicMock(status_code=201)

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            base_branch="main",
            token="test-token",
            task_id="test-task",
        )

        assert result == "posted"
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/issues/42/comments" in call_args[0][0]
        body = call_args[1]["json"]["body"]
        assert "## Nubi Pipeline Summary" in body
        assert PIPELINE_SUMMARY_MARKER in body

    @patch("nubi.tools.github_api.httpx.patch")
    @patch("nubi.tools.github_api._read_artifact")
    @patch("nubi.tools.github_api._find_existing_pipeline_comment")
    def test_updates_existing_comment(
        self,
        mock_find: MagicMock,
        mock_read: MagicMock,
        mock_patch: MagicMock,
    ) -> None:
        """Test updating an existing comment when marker is found."""
        mock_find.return_value = {"id": 123, "body": "old comment"}
        mock_read.side_effect = [
            {"decision": "approve", "summary": "Test", "commit_sha": "abc123"},
            {"gates": []},
            {"decision": "approve", "feedback": "Looks good"},
            {"decision": "approve", "ci_status": "success"},
        ]
        mock_patch.return_value = MagicMock(status_code=200)

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            base_branch="main",
            token="test-token",
            task_id="test-task",
        )

        assert result == "updated"
        mock_patch.assert_called_once()
        call_args = mock_patch.call_args
        assert "/issues/comments/123" in call_args[0][0]

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api._read_artifact")
    @patch("nubi.tools.github_api._find_existing_pipeline_comment")
    def test_handles_missing_artifacts_gracefully(
        self,
        mock_find: MagicMock,
        mock_read: MagicMock,
        mock_post: MagicMock,
    ) -> None:
        """Test that missing artifact files are handled gracefully."""
        mock_find.return_value = None
        mock_read.return_value = None  # All artifacts missing
        mock_post.return_value = MagicMock(status_code=201)

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            base_branch="main",
            token="test-token",
            task_id="test-task",
        )

        assert result == "posted"
        body = mock_post.call_args[1]["json"]["body"]
        assert "⚠️ Not available" in body or "⏭ Skipped" in body

    def test_invalid_pr_url(self) -> None:
        """Test handling of invalid PR URL."""
        result = post_pipeline_summary(
            pr_url="not-a-url",
            repo="kuuji/nubi",
            base_branch="main",
            token="test-token",
            task_id="test-task",
        )

        assert "Error" in result
        assert "Could not parse" in result
