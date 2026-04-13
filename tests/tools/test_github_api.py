"""Tests for nubi.tools.github_api — GitHub API helpers."""

from __future__ import annotations

from unittest.mock import ANY, MagicMock, patch

from nubi.agents.gate_result import GateCategory, GateDiscovery, GateResult, GatesResult, GateStatus
from nubi.agents.monitor_result import MonitorDecision, MonitorResult
from nubi.agents.result import ExecutorResult
from nubi.agents.review_result import ReviewDecision, ReviewResult
from nubi.tools.github_api import (
    _PIPELINE_SUMMARY_MARKER,
    _build_executor_section,
    _build_gates_section,
    _build_monitor_section,
    _build_reviewer_section,
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


# ----------------------------------------------------------------------
# Pipeline summary tests
# ----------------------------------------------------------------------


class TestFormatPipelineSummaryMarkdown:
    """Tests for format_pipeline_summary_markdown."""

    def test_complete_data(self) -> None:
        """Full data produces a well-formed markdown table for each section."""
        executor = ExecutorResult(
            status="success",
            commit_sha="a1b2c3d4e5f6",
            summary="Added rate limiting middleware",
            files_changed=["src/middleware.py"],
        )
        gates = GatesResult(
            discovered=[GateDiscovery(name="ruff", category=GateCategory.LINT, applies_to=["."])],
            gates=[
                GateResult(
                    name="ruff",
                    category=GateCategory.LINT,
                    status=GateStatus.PASSED,
                    output="0 errors",
                    command="ruff check .",
                ),
                GateResult(
                    name="pytest",
                    category=GateCategory.TEST,
                    status=GateStatus.PASSED,
                    output="12 passed, 0 failed",
                    command="pytest",
                ),
            ],
            overall_passed=True,
            attempt=1,
        )
        review = ReviewResult(
            decision=ReviewDecision.APPROVE,
            feedback="Clean implementation. Token bucket is the right choice.",
            summary="Looks good",
            issues=[],
        )
        monitor = MonitorResult(
            decision=MonitorDecision.APPROVE,
            summary="All checks pass",
            ci_status="success",
        )

        md = format_pipeline_summary_markdown(
            executor_result=executor,
            gates_result=gates,
            review_result=review,
            monitor_result=monitor,
            task_id="add-rate-limiting",
            branch="nubi/add-rate-limiting",
        )

        assert _PIPELINE_SUMMARY_MARKER in md
        assert "## Nubi Pipeline Summary" in md
        assert "**Task:** `add-rate-limiting`" in md
        # Executor section
        assert "### Executor" in md
        assert "✅ Success" in md
        assert executor.commit_sha[:8] in md  # First 8 chars of commit_sha
        # Gates section
        assert "### Gates" in md
        assert "| ruff" in md
        assert "| pytest" in md
        assert "✅ passed" in md
        # Reviewer section
        assert "### Reviewer" in md
        assert "✅ Approve" in md
        # Monitor section
        assert "### Monitor" in md
        assert "✅ Approve" in md
        assert "✅ success" in md
        # Footer
        assert ".nubi/add-rate-limiting/" in md

    def test_missing_executor(self) -> None:
        """Missing executor shows a Skipped indicator."""
        md = format_pipeline_summary_markdown(
            executor_result=None,
            gates_result=None,
            review_result=None,
            monitor_result=None,
            task_id="test-task",
            branch="nubi/test-task",
        )
        assert "### Executor" in md
        assert "Skipped" in md or "N/A" in md or "Not available" in md

    def test_skipped_review(self) -> None:
        """Skipped review stage shows 'Skipped' in the reviewer section."""
        executor = ExecutorResult(status="success", commit_sha="abc123", summary="Done")
        monitor = MonitorResult(decision=MonitorDecision.APPROVE)
        md = format_pipeline_summary_markdown(
            executor_result=executor,
            gates_result=None,
            review_result=None,
            monitor_result=monitor,
            task_id="test",
            branch="nubi/test",
        )
        section_lines = md.split("### Reviewer")[1].split("### Monitor")[0]
        assert "Skipped" in section_lines

    def test_failed_gates_attempt_details(self) -> None:
        """Gates with attempt > 1 include a collapsible details block."""
        gates = GatesResult(
            discovered=[],
            gates=[
                GateResult(
                    name="ruff",
                    category=GateCategory.LINT,
                    status=GateStatus.FAILED,
                    error="unused import",
                    command="ruff check .",
                ),
            ],
            overall_passed=False,
            attempt=2,
        )
        executor = ExecutorResult(status="success", commit_sha="abc123", summary="Done")
        md = format_pipeline_summary_markdown(
            executor_result=executor,
            gates_result=gates,
            review_result=None,
            monitor_result=None,
            task_id="test",
            branch="nubi/test",
        )
        assert "<details>" in md
        assert "Gate details (attempt 2" in md
        assert "❌ failed" in md

    def test_ci_failure_in_monitor(self) -> None:
        """CI failure shows a failure emoji in the monitor section."""
        monitor = MonitorResult(
            decision=MonitorDecision.CI_FAILED,
            summary="CI checks failed",
            ci_status="failure",
            ci_feedback="Test suite failed",
        )
        md = format_pipeline_summary_markdown(
            executor_result=None,
            gates_result=None,
            review_result=None,
            monitor_result=monitor,
            task_id="test",
            branch="nubi/test",
        )
        section_lines = md.split("### Monitor")[1]
        assert "❌ failure" in section_lines


class TestBuildExecutorSection:
    def test_with_result(self) -> None:
        result = ExecutorResult(status="success", commit_sha="abc12345", summary="Fixed bug")
        md = _build_executor_section(result, "")
        assert "✅ Success" in md
        assert "`abc12345`" in md
        assert "Fixed bug" in md

    def test_none(self) -> None:
        md = _build_executor_section(None, "")
        assert "### Executor" in md
        assert "Skipped" in md or "N/A" in md


class TestBuildGatesSection:
    def test_no_data(self) -> None:
        md = _build_gates_section(None, [])
        assert "### Gates" in md
        assert "No gates data" in md

    def test_with_gates(self) -> None:
        gates = GatesResult(
            discovered=[],
            gates=[
                GateResult(name="ruff", category=GateCategory.LINT, status=GateStatus.PASSED),
                GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.SKIPPED),
            ],
            overall_passed=True,
        )
        md = _build_gates_section(gates, [])
        assert "| ruff" in md
        assert "✅ passed" in md
        assert "⏭ skipped" in md


class TestBuildReviewerSection:
    def test_with_result(self) -> None:
        review = ReviewResult(
            decision=ReviewDecision.APPROVE,
            feedback="Looks good",
        )
        md = _build_reviewer_section(review)
        assert "✅ Approve" in md
        assert "Looks good" in md

    def test_none(self) -> None:
        md = _build_reviewer_section(None)
        assert "### Reviewer" in md
        assert "Skipped" in md


class TestBuildMonitorSection:
    def test_with_result(self) -> None:
        monitor = MonitorResult(
            decision=MonitorDecision.FLAG,
            ci_status="success",
        )
        md = _build_monitor_section(monitor)
        assert "⚠ Flag" in md
        assert "✅ success" in md

    def test_none(self) -> None:
        md = _build_monitor_section(None)
        assert "### Monitor" in md
        assert "Skipped" in md


class TestPostPipelineSummary:
    """Tests for the full post_pipeline_summary function."""

    @patch("nubi.tools.github_api._find_existing_pipeline_comment")
    @patch("nubi.tools.github_api._post_pr_comment")
    def test_posts_new_comment(self, mock_post: MagicMock, mock_find: MagicMock) -> None:
        """When no existing comment is found, posts a new one."""
        mock_find.return_value = None
        mock_post.return_value = True

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/task-id",
            token="tok",
        )

        assert result == "Pipeline summary posted"
        mock_find.assert_called_once_with(42)
        mock_post.assert_called_once()
        # Verify the body contains our marker (check positional args)
        # Body is passed as positional arg 2 to _post_pr_comment(pr_number, body)
        assert _PIPELINE_SUMMARY_MARKER in mock_post.call_args[0][1]

    @patch("nubi.tools.github_api._find_existing_pipeline_comment")
    @patch("nubi.tools.github_api._update_pr_comment")
    def test_updates_existing_comment(self, mock_update: MagicMock, mock_find: MagicMock) -> None:
        """When an existing comment is found, updates it instead of posting new."""
        mock_find.return_value = 123456
        mock_update.return_value = True

        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/task-id",
            token="tok",
        )

        assert result == "Pipeline summary updated: comment 123456"
        mock_find.assert_called_once_with(42)
        mock_update.assert_called_once_with(123456, ANY)
        # Verify the body passed to update contains our marker
        call_args = mock_update.call_args[0]
        assert _PIPELINE_SUMMARY_MARKER in call_args[1]

    @patch("nubi.tools.github_api._find_existing_pipeline_comment")
    @patch("nubi.tools.github_api._post_pr_comment")
    def test_invalid_pr_url(self, mock_post: MagicMock, mock_find: MagicMock) -> None:
        """Invalid PR URL returns an error without API calls."""
        result = post_pipeline_summary(
            pr_url="not-a-url",
            repo="kuuji/nubi",
            branch="nubi/task-id",
            token="tok",
        )
        assert "Error" in result
        mock_find.assert_not_called()
        mock_post.assert_not_called()

    @patch("nubi.tools.github_api._find_existing_pipeline_comment")
    @patch("nubi.tools.github_api._post_pr_comment")
    def test_missing_artifact_files_handled(
        self, mock_post: MagicMock, mock_find: MagicMock
    ) -> None:
        """Missing artifact files are handled gracefully — no exception raised."""
        mock_find.return_value = None
        mock_post.return_value = True

        # read_branch_file will return error strings for missing files
        result = post_pipeline_summary(
            pr_url="https://github.com/kuuji/nubi/pull/42",
            repo="kuuji/nubi",
            branch="nubi/nonexistent-task",
            token="tok",
        )

        assert result == "Pipeline summary posted"
