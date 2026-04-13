"""Tests for nubi.tools.github_api — GitHub API helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from nubi.tools.github_api import (
    _build_pipeline_summary_markdown,
    _gate_emoji,
    _human_readable_status,
    _pr_number_from_url,
    _status_emoji,
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


# ----------------------------------------------------------------------
# Pipeline Summary Tests
# ----------------------------------------------------------------------


class TestStatusEmoji:
    def test_pass_status(self) -> None:
        assert _status_emoji("passed") == "✅"
        assert _status_emoji("success") == "✅"

    def test_fail_status(self) -> None:
        assert _status_emoji("failed") == "❌"
        assert _status_emoji("reject") == "❌"
        assert _status_emoji("rejected") == "❌"

    def test_skipped_status(self) -> None:
        assert _status_emoji("skipped") == "⏭"

    def test_approve_status(self) -> None:
        assert _status_emoji("approve") == "✅"

    def test_timed_out_status(self) -> None:
        assert _status_emoji("timed_out") == "⏭"

    def test_unknown_status(self) -> None:
        assert _status_emoji("unknown") == "❓"


class TestGateEmoji:
    def test_pass(self) -> None:
        assert _gate_emoji("passed") == "✅ pass"

    def test_fail(self) -> None:
        assert _gate_emoji("failed") == "❌ fail"

    def test_skipped(self) -> None:
        assert _gate_emoji("skipped") == "⏭ skipped"


class TestHumanReadableStatus:
    def test_normal(self) -> None:
        assert _human_readable_status("passed") == "Passed"

    def test_underscore_replaced(self) -> None:
        assert _human_readable_status("timed_out") == "Timed Out"

    def test_dash_replaced(self) -> None:
        assert _human_readable_status("ci-failed") == "Ci-Failed"


class TestBuildPipelineSummaryMarkdown:
    """Tests for markdown formatting of the pipeline summary."""

    def test_complete_data(self) -> None:
        """All four artifact files present — full table rendering."""
        result_data = {
            "status": "success",
            "commit_sha": "a1b2c3d4e5f6",
            "summary": "Added rate limiting",
        }
        gates_data = {
            "attempt": 1,
            "gates": [
                {
                    "name": "ruff",
                    "status": "passed",
                    "output": "0 errors",
                },
                {
                    "name": "pytest",
                    "status": "passed",
                    "output": "12 passed",
                },
            ],
        }
        review_data = {
            "decision": "approve",
            "feedback": "Clean implementation.",
        }
        monitor_data = {
            "decision": "approve",
        }

        md = _build_pipeline_summary_markdown(
            task_id="add-rate-limiting",
            result_data=result_data,
            gates_data=gates_data,
            review_data=review_data,
            monitor_data=monitor_data,
            ci_status="success",
        )

        # Header section
        assert "## Nubi Pipeline Summary" in md
        assert "`add-rate-limiting`" in md

        # Executor section
        assert "### Executor" in md
        assert "✅ Success" in md
        assert "`a1b2c3d`" in md  # commit short
        assert "Added rate limiting" in md

        # Gates section
        assert "### Gates" in md
        assert "| ruff | ✅ pass |" in md
        assert "0 errors" in md
        assert "| pytest | ✅ pass |" in md
        assert "12 passed" in md

        # Reviewer section
        assert "### Reviewer" in md
        assert "✅ Approve" in md
        assert "Clean implementation." in md

        # Monitor section
        assert "### Monitor" in md
        assert "✅ Approve" in md
        assert "✅ Success" in md  # CI status

        # Footer
        assert "<!-- nubi-pipeline-summary -->" in md
        assert "[Nubi](https://github.com/kuuji/nubi)" in md

    def test_skipped_reviewer(self) -> None:
        """Reviewer was skipped — show skipped status."""
        result_data = {"status": "success", "commit_sha": "abc123", "summary": "Done"}
        gates_data = {"attempt": 1, "gates": []}
        review_data = None
        monitor_data = {"decision": "approve"}

        md = _build_pipeline_summary_markdown(
            task_id="test-task",
            result_data=result_data,
            gates_data=gates_data,
            review_data=review_data,
            monitor_data=monitor_data,
            ci_status="success",
        )

        assert "### Reviewer" in md
        assert "⏭ Skipped" in md

    def test_missing_monitor(self) -> None:
        """Monitor data missing — show not found."""
        result_data = {"status": "success", "commit_sha": "abc", "summary": "ok"}
        md = _build_pipeline_summary_markdown(
            task_id="test",
            result_data=result_data,
            gates_data=None,
            review_data=None,
            monitor_data=None,
            ci_status="timed_out",
        )

        assert "### Monitor" in md
        assert "❓ Not found" in md
        assert "⏭ Timed Out" in md  # CI status from env

    def test_missing_result_data(self) -> None:
        """Executor result missing — show not found."""
        md = _build_pipeline_summary_markdown(
            task_id="test",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
            ci_status="success",
        )

        assert "### Executor" in md
        assert "❓ Not found" in md

    def test_gate_attempt_count(self) -> None:
        """Multiple gate attempts shown."""
        gates_data = {
            "attempt": 3,
            "gates": [
                {"name": "ruff", "status": "failed", "output": "3 errors"},
            ],
        }

        md = _build_pipeline_summary_markdown(
            task_id="test",
            result_data=None,
            gates_data=gates_data,
            review_data=None,
            monitor_data=None,
            ci_status="success",
        )

        assert "**Attempts:** 3" in md

    def test_long_gate_output_truncated(self) -> None:
        """Long gate output is truncated at 200 chars."""
        long_output = "x" * 300
        gates_data = {
            "attempt": 1,
            "gates": [
                {"name": "ruff", "status": "failed", "output": long_output},
            ],
        }

        md = _build_pipeline_summary_markdown(
            task_id="test",
            result_data=None,
            gates_data=gates_data,
            review_data=None,
            monitor_data=None,
            ci_status="success",
        )

        assert ("x" * 200) in md
        assert ("x" * 300) not in md
        assert "..." in md

    def test_ci_status_emoji(self) -> None:
        """CI status is rendered with correct emoji."""
        md = _build_pipeline_summary_markdown(
            task_id="test",
            result_data={"status": "success", "commit_sha": "abc", "summary": ""},
            gates_data=None,
            review_data=None,
            monitor_data=None,
            ci_status="success",
        )
        assert "| CI Status | ✅ Success |" in md

        md = _build_pipeline_summary_markdown(
            task_id="test",
            result_data={"status": "success", "commit_sha": "abc", "summary": ""},
            gates_data=None,
            review_data=None,
            monitor_data=None,
            ci_status="timed_out",
        )
        assert "| CI Status | ⏭ Timed Out |" in md


class TestPostPipelineSummary:
    """Tests for post_pipeline_summary API call logic."""

    @patch("nubi.tools.github_api._read_branch_file_raw")
    @patch("nubi.tools.github_api.httpx.post")
    def test_posts_new_comment(self, mock_post: MagicMock, mock_read: MagicMock) -> None:
        """When no existing comment found, posts a new one."""
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": 999},
        )
        mock_read.return_value = "{}"

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/test-task",
            token="tok",
        )

        assert "Pipeline summary posted" in result
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/issues/42/comments" in call_args[0][0]
        assert "<!-- nubi-pipeline-summary -->" in call_args[1]["json"]["body"]

    @patch("nubi.tools.github_api._read_branch_file_raw")
    @patch("nubi.tools.github_api.httpx.get")
    @patch("nubi.tools.github_api.httpx.patch")
    def test_updates_existing_comment(
        self, mock_patch: MagicMock, mock_get: MagicMock, mock_read: MagicMock
    ) -> None:
        """When existing comment found with marker, updates it instead of posting new."""
        mock_read.return_value = "{}"

        def get_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            url = args[0]
            if "/comments" in url:
                mock_instance = MagicMock()
                mock_instance.status_code = 200
                mock_instance.json.return_value = [
                    {"id": 123, "body": "Old comment <!-- nubi-pipeline-summary -->"},
                    {"id": 456, "body": "Some other comment"},
                ]
                return mock_instance
            mock_instance = MagicMock()
            mock_instance.status_code = 200
            mock_instance.json.return_value = {"content": ""}
            return mock_instance

        mock_get.side_effect = get_side_effect
        mock_patch.return_value = MagicMock(status_code=200)

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/test-task",
            token="tok",
        )

        assert "Pipeline summary updated" in result
        mock_patch.assert_called_once()
        call_args = mock_patch.call_args
        assert "/issues/comments/123" in call_args[0][0]

    @patch("nubi.tools.github_api._read_branch_file_raw")
    @patch("nubi.tools.github_api.httpx.get")
    def test_no_existing_comment_posts_new(self, mock_get: MagicMock, mock_read: MagicMock) -> None:
        """When no comment with marker exists, posts a new one."""
        mock_read.return_value = "{}"

        def get_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            url = args[0]
            if "/comments" in url:
                mock_instance = MagicMock()
                mock_instance.status_code = 200
                mock_instance.json.return_value = [
                    {"id": 789, "body": "Some other comment without marker"},
                ]
                return mock_instance
            mock_instance = MagicMock()
            mock_instance.status_code = 200
            mock_instance.json.return_value = {"content": ""}
            return mock_instance

        mock_get.side_effect = get_side_effect

        # Mock the post to avoid actual HTTP call
        with patch("nubi.tools.github_api.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=201,
                json=lambda: {"id": 999},
            )

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="tok",
            )

            assert "Pipeline summary posted" in result
