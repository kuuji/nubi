"""Tests for nubi.tools.github_api — GitHub API helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.tools.github_api import (
    _pr_number_from_url,
    configure,
    format_pipeline_summary,
    mark_pr_ready,
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

        mock_get.assert_called_once()
        mock_post.assert_not_called()

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api.httpx.get")
    def test_skips_on_invalid_url(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        mark_pr_ready("not-a-url")

        mock_get.assert_not_called()
        mock_post.assert_not_called()


class TestFormatPipelineSummary:
    """Tests for the markdown formatting logic."""

    def test_complete_data(self) -> None:
        """Full pipeline data renders all four sections with icons."""
        result_data = {
            "status": "success",
            "commit_sha": "a1b2c3d4e5f6",
            "summary": "Added rate limiting middleware",
            "attempt": 2,
            "files_changed": ["src/main.py", "tests/test_main.py"],
        }
        gates_data = {
            "discovered": [
                {"name": "ruff"},
                {"name": "radon"},
                {"name": "pytest"},
                {"name": "diff_size"},
            ],
            "gates": [
                {
                    "name": "ruff",
                    "category": "lint",
                    "status": "passed",
                    "output": "[]",
                    "error": "",
                },
                {
                    "name": "radon",
                    "category": "complexity",
                    "status": "passed",
                    "output": '[{"complexity": 6}]',
                    "error": "",
                },
                {
                    "name": "pytest",
                    "category": "test",
                    "status": "passed",
                    "output": '{"passed": 12, "failed": 0}',
                    "error": "",
                },
                {
                    "name": "diff_size",
                    "category": "diff_size",
                    "status": "passed",
                    "output": "+87 -4 lines",
                    "error": "",
                },
            ],
        }
        review_data = {
            "decision": "approve",
            "feedback": "Clean implementation. Token bucket is the right choice.",
            "summary": "All good.",
            "issues": [],
        }
        monitor_data = {
            "decision": "approve",
            "ci_status": "success",
        }

        md = format_pipeline_summary(
            task_id="add-rate-limiting-a1b2c3",
            task_branch="nubi/add-rate-limiting-a1b2c3",
            result_data=result_data,
            gates_data=gates_data,
            review_data=review_data,
            monitor_data=monitor_data,
        )

        assert "## Nubi Pipeline Summary" in md
        assert "**Task:** `add-rate-limiting-a1b2c3`" in md
        assert "**Branch:** `nubi/add-rate-limiting-a1b2c3`" in md
        # Executor
        assert "### Executor" in md
        assert "✅ Complete" in md
        assert "Attempts | 2" in md
        assert "`a1b2c3d4`" in md
        # Gates
        assert "### Gates" in md
        assert "✅ passed | ruff" in md or "ruff" in md
        assert "radon" in md
        assert "pytest" in md
        assert "diff_size" in md
        # Reviewer
        assert "### Reviewer" in md
        assert "✅ Approve" in md
        # Monitor
        assert "### Monitor" in md
        assert "✅ Approve" in md
        assert "✅ Success" in md
        # Footer
        assert "Generated by [Nubi]" in md
        assert "pipeline artifacts" in md

    def test_missing_review_skipped(self) -> None:
        """Missing review artifact shows 'skipped' in the reviewer section."""
        md = format_pipeline_summary(
            task_id="task-abc",
            task_branch="nubi/task-abc",
            result_data={"status": "success"},
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )

        assert "### Executor" in md
        assert "### Gates" in md
        assert "### Reviewer" in md
        assert "⏭ Skipped" in md
        assert "### Monitor" in md
        assert "⏭ Skipped" in md

    def test_missing_result_no_artifact(self) -> None:
        """Missing result artifact shows 'No artifact' in the executor section."""
        md = format_pipeline_summary(
            task_id="task-abc",
            task_branch="nubi/task-abc",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )

        assert "### Executor" in md
        assert "⚠️ No artifact" in md

    def test_failed_gate_includes_error(self) -> None:
        """Failed gate shows the error message in the details column."""
        gates_data = {
            "discovered": [{"name": "ruff"}],
            "gates": [
                {
                    "name": "ruff",
                    "status": "failed",
                    "error": "F401 unused import",
                    "output": "",
                },
            ],
        }
        md = format_pipeline_summary(
            task_id="task-abc",
            task_branch="nubi/task-abc",
            result_data=None,
            gates_data=gates_data,
            review_data=None,
            monitor_data=None,
        )

        assert "❌ fail" in md or "❌ failed" in md
        assert "F401" in md

    def test_ci_failed_decision(self) -> None:
        """CI failure decision shows the right icon."""
        monitor_data = {
            "decision": "ci-failed",
            "ci_status": "failure",
            "ci_feedback": "Test suite timed out",
        }
        md = format_pipeline_summary(
            task_id="task-abc",
            task_branch="nubi/task-abc",
            result_data={"status": "success"},
            gates_data=None,
            review_data={"decision": "approve", "feedback": ""},
            monitor_data=monitor_data,
        )

        assert "❌ Ci Failed" in md
        assert "❌ Failure" in md

    def test_monitor_ci_feedback_displayed(self) -> None:
        """CI feedback from monitor data is rendered in the monitor section."""
        monitor_data = {
            "decision": "ci-failed",
            "ci_status": "failure",
            "ci_feedback": "### ruff\n3 errors\n### pytest\n2 failed",
        }
        md = format_pipeline_summary(
            task_id="task-abc",
            task_branch="nubi/task-abc",
            result_data={"status": "success"},
            gates_data=None,
            review_data=None,
            monitor_data=monitor_data,
        )

        assert "### Monitor" in md
        assert "CI Feedback" in md
        assert "### ruff" in md
        assert "3 errors" in md

    def test_executor_success_has_emoji(self) -> None:
        """Successful executor status shows '✅ Complete', not plain 'success'."""
        md = format_pipeline_summary(
            task_id="task-abc",
            task_branch="nubi/task-abc",
            result_data={"status": "success"},
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )

        assert "✅ Complete" in md

    def test_executor_failed_has_emoji(self) -> None:
        """Failed executor status shows '❌ Failed'."""
        md = format_pipeline_summary(
            task_id="task-abc",
            task_branch="nubi/task-abc",
            result_data={"status": "failed"},
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )

        assert "❌ Failed" in md


class TestPostPipelineSummary:
    """Tests for post_pipeline_summary — mocks GitHub API calls."""

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api._read_artifact_json")
    def test_posts_new_comment(
        self,
        mock_read: MagicMock,
        mock_post: MagicMock,
    ) -> None:
        """post_pipeline_summary calls POST /issues/{pr}/comments with correct body."""
        mock_read.side_effect = [
            {"status": "success", "commit_sha": "abc123", "attempt": 1, "files_changed": []},
            None,
            None,
            None,
        ]
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {})

        from nubi.tools.github_api import post_pipeline_summary

        rv = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/my-task",
            token="tok123",
        )

        assert rv == "Pipeline summary posted"
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/issues/42/comments" in call_args[0][0]
        body = call_args[1]["json"]["body"]
        assert "<!-- nubi-pipeline-summary -->" in body
        assert "## Nubi Pipeline Summary" in body
        # Verifies the executor status rendered with emoji (not plain "success")
        assert "✅ Complete" in body

    @patch("nubi.tools.github_api.httpx.patch")
    @patch("nubi.tools.github_api.httpx.get")
    @patch("nubi.tools.github_api._read_artifact_json")
    def test_updates_existing_comment(
        self,
        mock_read: MagicMock,
        mock_get: MagicMock,
        mock_patch: MagicMock,
    ) -> None:
        """When an existing comment with the marker is found, it is updated via PATCH."""
        mock_read.side_effect = [
            {"status": "success"},
            None,
            None,
            None,
        ]
        # Existing comment with marker
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"id": 999, "body": "stale comment without marker"},
                {"id": 123456, "body": "<!-- nubi-pipeline-summary -->\n\nold body"},
                {"id": 998, "body": "another comment"},
            ],
        )
        mock_patch.return_value = MagicMock(status_code=200)

        from nubi.tools.github_api import post_pipeline_summary

        rv = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/my-task",
            token="tok123",
        )

        assert rv == "Pipeline summary updated"
        mock_patch.assert_called_once()
        call_args = mock_patch.call_args
        assert "/issues/comments/123456" in call_args[0][0]
        assert "<!-- nubi-pipeline-summary -->" in call_args[1]["json"]["body"]

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api.httpx.get")
    @patch("nubi.tools.github_api._read_artifact_json")
    def test_posts_new_when_no_existing(
        self,
        mock_read: MagicMock,
        mock_get: MagicMock,
        mock_post: MagicMock,
    ) -> None:
        """When no existing comment is found, a new one is posted."""
        mock_read.side_effect = [
            {"status": "success"},
            None,
            None,
            None,
        ]
        # No comments on the PR
        mock_get.return_value = MagicMock(status_code=200, json=lambda: [])
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {})

        from nubi.tools.github_api import post_pipeline_summary

        rv = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/my-task",
            token="tok123",
        )

        assert rv == "Pipeline summary posted"
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/issues/42/comments" in call_args[0][0]
        body = call_args[1]["json"]["body"]
        assert "<!-- nubi-pipeline-summary -->" in body
        assert "## Nubi Pipeline Summary" in body

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api._read_artifact_json")
    def test_handles_invalid_pr_url(
        self,
        mock_read: MagicMock,
        mock_post: MagicMock,
    ) -> None:
        """Invalid PR URL returns an error instead of crashing."""
        from nubi.tools.github_api import post_pipeline_summary

        rv = post_pipeline_summary(
            pr_url="not-a-url",
            repo="kuuji/nubi",
            branch="nubi/my-task",
            token="tok123",
        )

        assert "Error" in rv
        mock_post.assert_not_called()


class TestFindExistingPipelineComment:
    """Direct tests for _find_existing_pipeline_comment."""

    @patch("nubi.tools.github_api.httpx.get")
    def test_returns_comment_id_when_marker_found(self, mock_get: MagicMock) -> None:
        """Returns comment ID when a comment contains the marker."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"id": 100, "body": "some text"},
                {"id": 200, "body": "<!-- nubi-pipeline-summary -->\n\nold body"},
            ],
        )

        from nubi.tools.github_api import _find_existing_pipeline_comment

        rv = _find_existing_pipeline_comment(42, "<!-- nubi-pipeline-summary -->", "kuuji/nubi")
        assert rv == 200

    @patch("nubi.tools.github_api.httpx.get")
    def test_returns_none_when_no_matching_comment(self, mock_get: MagicMock) -> None:
        """Returns None when no comment contains the marker."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"id": 100, "body": "some text"},
                {"id": 200, "body": "another comment"},
            ],
        )

        from nubi.tools.github_api import _find_existing_pipeline_comment

        rv = _find_existing_pipeline_comment(42, "<!-- nubi-pipeline-summary -->", "kuuji/nubi")
        assert rv is None

    @patch("nubi.tools.github_api.httpx.get")
    def test_returns_none_on_empty_comment_list(self, mock_get: MagicMock) -> None:
        """Returns None when the PR has no comments."""
        mock_get.return_value = MagicMock(status_code=200, json=lambda: [])

        from nubi.tools.github_api import _find_existing_pipeline_comment

        rv = _find_existing_pipeline_comment(42, "<!-- nubi-pipeline-summary -->", "kuuji/nubi")
        assert rv is None

    @patch("nubi.tools.github_api.httpx.get")
    def test_returns_none_on_api_error(self, mock_get: MagicMock) -> None:
        """Returns None when GitHub API returns non-200 status."""
        mock_get.return_value = MagicMock(status_code=500, text="Internal Server Error")

        from nubi.tools.github_api import _find_existing_pipeline_comment

        rv = _find_existing_pipeline_comment(42, "<!-- nubi-pipeline-summary -->", "kuuji/nubi")
        assert rv is None
