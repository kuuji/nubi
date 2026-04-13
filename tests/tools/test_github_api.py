"""Tests for nubi.tools.github_api — GitHub API helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

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


# ---------------------------------------------------------------------------
# Tests for post_pipeline_summary and format_pipeline_summary
# ---------------------------------------------------------------------------


COMPLETE_RESULT_DATA = {
    "status": "success",
    "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
    "summary": "Added rate limiting middleware with token bucket algorithm",
    "files_changed": ["src/middleware/rate_limit.py", "tests/test_rate_limit.py"],
    "error": "",
}

COMPLETE_GATES_DATA = {
    "discovered": [
        {"name": "ruff", "category": "lint", "applies_to": [], "command": "ruff check src/"},
        {"name": "pytest", "category": "test", "applies_to": [], "command": "pytest tests/"},
        {"name": "diff_size", "category": "diff_size", "applies_to": ["*"], "command": ""},
    ],
    "gates": [
        {
            "name": "ruff",
            "category": "lint",
            "status": "passed",
            "output": "0 errors",
            "command": "ruff check src/",
            "duration_seconds": 1.2,
            "error": "",
        },
        {
            "name": "pytest",
            "category": "test",
            "status": "passed",
            "output": "12 passed, 0 failed",
            "command": "pytest tests/ -v",
            "duration_seconds": 45.0,
            "error": "",
        },
        {
            "name": "diff_size",
            "category": "diff_size",
            "status": "passed",
            "output": "+87 -4 lines",
            "command": "",
            "duration_seconds": 0.1,
            "error": "",
        },
    ],
    "overall_passed": True,
    "attempt": 1,
}

COMPLETE_REVIEW_DATA = {
    "decision": "approve",
    "feedback": "Clean implementation. Token bucket is the right choice for this use case.",
    "summary": "Looks good",
    "issues": [],
}

COMPLETE_MONITOR_DATA = {
    "decision": "approve",
    "summary": "Approved — all gates passed",
    "pr_summary": "",
    "concerns": [],
    "pr_url": "https://github.com/kuuji/nubi/pull/42",
    "ci_status": "success",
    "ci_feedback": "",
}


class TestFormatPipelineSummaryComplete:
    """Test format_pipeline_summary with complete data."""

    @patch("nubi.tools.github_api._read_json_file")
    def test_includes_task_and_branch_info(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            COMPLETE_GATES_DATA,
            COMPLETE_REVIEW_DATA,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "**Task:** `add-rate-limiting`" in result
        assert "**Branch:** `nubi/add-rate-limiting`" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_executor_section(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            COMPLETE_GATES_DATA,
            COMPLETE_REVIEW_DATA,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "### Executor" in result
        assert "✅ success" in result
        # Commit is first 8 chars of the SHA
        assert "`a1b2c3d4`" in result
        assert "Added rate limiting middleware" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_gates_section(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            COMPLETE_GATES_DATA,
            COMPLETE_REVIEW_DATA,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "### Gates" in result
        assert "ruff" in result
        assert "✅ pass" in result
        assert "pytest" in result
        assert "diff_size" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_reviewer_section(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            COMPLETE_GATES_DATA,
            COMPLETE_REVIEW_DATA,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "### Reviewer" in result
        assert "✅ Approve" in result
        assert "Clean implementation" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_monitor_section(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            COMPLETE_GATES_DATA,
            COMPLETE_REVIEW_DATA,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "### Monitor" in result
        assert "✅ Approve" in result
        assert "✅ success" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_includes_summary_marker(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            COMPLETE_GATES_DATA,
            COMPLETE_REVIEW_DATA,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "<!-- nubi-pipeline-summary -->" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_includes_artifact_link(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            COMPLETE_GATES_DATA,
            COMPLETE_REVIEW_DATA,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "[pipeline artifacts](.nubi/add-rate-limiting/)" in result


class TestFormatPipelineSummaryMissingData:
    """Test format_pipeline_summary handles missing data gracefully."""

    @patch("nubi.tools.github_api._read_json_file")
    def test_missing_result_shows_not_found(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [None, None, None, None]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/some-task",
        )

        assert "### Executor" in result
        assert "⚠ Not found" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_missing_gates_shows_not_found(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [COMPLETE_RESULT_DATA, None, None, None]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "### Gates" in result
        assert "⚠ Not found" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_missing_review_shows_skipped(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [COMPLETE_RESULT_DATA, COMPLETE_GATES_DATA, None, None]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "### Reviewer" in result
        assert "⚠ Skipped" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_missing_monitor_shows_not_found(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            COMPLETE_GATES_DATA,
            COMPLETE_REVIEW_DATA,
            None,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "### Monitor" in result
        assert "⚠ Not found" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_empty_gates_list(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            {"discovered": [], "gates": [], "overall_passed": True, "attempt": 1},
            COMPLETE_REVIEW_DATA,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "⚠ No gates run" in result


class TestFormatPipelineSummarySkippedStages:
    """Test format_pipeline_summary with skipped stages."""

    @patch("nubi.tools.github_api._read_json_file")
    def test_skipped_gates(self, mock_read: MagicMock) -> None:
        gates_with_skipped = {
            "discovered": [],
            "gates": [
                {
                    "name": "ruff",
                    "category": "lint",
                    "status": "skipped",
                    "output": "ruff not found",
                    "command": "",
                    "duration_seconds": 0.0,
                    "error": "",
                },
            ],
            "overall_passed": True,
            "attempt": 1,
        }
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            gates_with_skipped,
            COMPLETE_REVIEW_DATA,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "⏭ skipped" in result

    @patch("nubi.tools.github_api._read_json_file")
    def test_rejected_review(self, mock_read: MagicMock) -> None:
        rejected_review = {
            "decision": "reject",
            "feedback": "Needs refactoring before approval.",
            "summary": "Needs work",
            "issues": [],
        }
        mock_read.side_effect = [
            COMPLETE_RESULT_DATA,
            COMPLETE_GATES_DATA,
            rejected_review,
            COMPLETE_MONITOR_DATA,
        ]

        result = format_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
        )

        assert "❌ Reject" in result


class TestPostPipelineSummary:
    """Test post_pipeline_summary GitHub API integration."""

    @patch("nubi.tools.github_api._post_or_update_comment")
    @patch("nubi.tools.github_api.format_pipeline_summary")
    def test_posts_new_comment(self, mock_format: MagicMock, mock_post: MagicMock) -> None:
        mock_format.return_value = "# Pipeline Summary\n\n<!-- nubi-pipeline-summary -->"

        result = post_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
            "ghp_testtoken",
        )

        assert result == "Pipeline summary posted to PR."
        mock_format.assert_called_once()
        expected_body = "# Pipeline Summary\n\n<!-- nubi-pipeline-summary -->"
        mock_post.assert_called_once_with(42, expected_body)

    @patch("nubi.tools.github_api._post_or_update_comment")
    @patch("nubi.tools.github_api.format_pipeline_summary")
    def test_invalid_pr_url(self, mock_format: MagicMock, mock_post: MagicMock) -> None:
        result = post_pipeline_summary(
            "https://github.com/kuuji/nubi",  # No PR number
            "kuuji/nubi",
            "nubi/add-rate-limiting",
            "ghp_testtoken",
        )

        assert "Error: Could not extract PR number" in result
        mock_format.assert_not_called()
        mock_post.assert_not_called()

    @patch("nubi.tools.github_api.format_pipeline_summary")
    def test_http_error_returns_error(self, mock_format: MagicMock) -> None:
        mock_format.side_effect = httpx.HTTPError("Connection refused")

        result = post_pipeline_summary(
            "https://github.com/kuuji/nubi/pull/42",
            "kuuji/nubi",
            "nubi/add-rate-limiting",
            "ghp_testtoken",
        )

        assert "Error posting pipeline summary" in result


class TestPostOrUpdateComment:
    """Test _post_or_update_comment for update-existing-comment path."""

    @patch("nubi.tools.github_api.httpx.patch")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    def test_updates_existing_comment(self, mock_find: MagicMock, mock_patch: MagicMock) -> None:
        from nubi.tools.github_api import _post_or_update_comment

        mock_find.return_value = 123456789  # Existing comment ID
        mock_patch.return_value = MagicMock(status_code=200)

        _post_or_update_comment(42, "Updated body")

        mock_patch.assert_called_once()
        assert "/issues/comments/123456789" in mock_patch.call_args[0][0]

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    def test_posts_new_comment_when_none_found(
        self, mock_find: MagicMock, mock_post: MagicMock
    ) -> None:
        from nubi.tools.github_api import _post_or_update_comment

        mock_find.return_value = None
        mock_post.return_value = MagicMock(status_code=201)

        _post_or_update_comment(42, "New body")

        mock_post.assert_called_once()
        assert "/issues/42/comments" in mock_post.call_args[0][0]


class TestFindExistingSummaryComment:
    """Test _find_existing_summary_comment finds by marker."""

    @patch("nubi.tools.github_api.httpx.get")
    def test_finds_comment_with_marker(self, mock_get: MagicMock) -> None:
        from nubi.tools.github_api import _find_existing_summary_comment

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"id": 111, "body": "Some other comment"},
                {"id": 222, "body": "## Nubi Pipeline Summary\n\n<!-- nubi-pipeline-summary -->"},
                {"id": 333, "body": "Unrelated"},
            ],
        )

        comment_id = _find_existing_summary_comment(42)

        assert comment_id == 222

    @patch("nubi.tools.github_api.httpx.get")
    def test_returns_none_when_no_matching_comment(self, mock_get: MagicMock) -> None:
        from nubi.tools.github_api import _find_existing_summary_comment

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"id": 111, "body": "Just a regular comment"},
                {"id": 222, "body": "Another comment"},
            ],
        )

        comment_id = _find_existing_summary_comment(42)

        assert comment_id is None

    @patch("nubi.tools.github_api.httpx.get")
    def test_returns_none_on_api_error(self, mock_get: MagicMock) -> None:
        from nubi.tools.github_api import _find_existing_summary_comment

        mock_get.return_value = MagicMock(status_code=403)

        comment_id = _find_existing_summary_comment(42)

        assert comment_id is None
