"""Tests for nubi.tools.github_api — GitHub API helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.tools.github_api import (
    _build_pipeline_summary_markdown,
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


class TestBuildPipelineSummaryMarkdown:
    """Tests for the markdown formatting function."""

    def test_complete_data(self) -> None:
        """Test formatting with complete data for all stages."""
        from nubi.agents.gate_result import GateCategory, GateResult, GatesResult, GateStatus
        from nubi.agents.monitor_result import MonitorDecision, MonitorResult
        from nubi.agents.result import ExecutorResult
        from nubi.agents.review_result import ReviewDecision, ReviewResult

        executor = ExecutorResult(
            status="success",
            commit_sha="a1b2c3d4e5f6",
            summary="Added rate limiting middleware",
            files_changed=["src/middleware.py"],
        )
        gates = GatesResult(
            discovered=[],
            gates=[
                GateResult(
                    name="ruff",
                    category=GateCategory.LINT,
                    status=GateStatus.PASSED,
                    output="0 errors",
                ),
                GateResult(
                    name="pytest",
                    category=GateCategory.TEST,
                    status=GateStatus.PASSED,
                    output="12 passed, 0 failed",
                ),
            ],
            overall_passed=True,
        )
        review = ReviewResult(
            decision=ReviewDecision.APPROVE,
            feedback="Clean implementation. Token bucket is the right choice.",
        )
        monitor = MonitorResult(
            decision=MonitorDecision.APPROVE,
            summary="All checks passed",
            ci_status="success",
        )

        markdown = _build_pipeline_summary_markdown(
            task_id="add-rate-limiting-a1b2c3",
            branch="nubi/add-rate-limiting-a1b2c3",
            executor=executor,
            gates=gates,
            review=review,
            monitor=monitor,
        )

        assert "## Nubi Pipeline Summary" in markdown
        assert "**Task:** `add-rate-limiting-a1b2c3`" in markdown
        assert "### Executor" in markdown
        assert "✅ Complete" in markdown
        assert "`a1b2c3d4`" in markdown  # First 8 chars of commit_sha (12 char sha, take [:8])
        assert "### Gates" in markdown
        assert "| ruff | ✅ pass |" in markdown
        assert "| pytest | ✅ pass |" in markdown
        assert "### Reviewer" in markdown
        assert "✅ Approve" in markdown
        assert "### Monitor" in markdown
        assert "✅ Approve" in markdown
        assert "✅ success" in markdown
        assert "<!-- nubi-pipeline-summary -->" not in markdown  # marker added by caller

    def test_skipped_review(self) -> None:
        """Test formatting when review is skipped."""
        from nubi.agents.gate_result import GatesResult
        from nubi.agents.monitor_result import MonitorDecision, MonitorResult
        from nubi.agents.result import ExecutorResult

        executor = ExecutorResult(
            status="success",
            commit_sha="abc123",
            summary="Test changes",
        )
        gates = GatesResult(discovered=[], gates=[], overall_passed=True)
        monitor = MonitorResult(decision=MonitorDecision.APPROVE)

        markdown = _build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            executor=executor,
            gates=gates,
            review=None,
            monitor=monitor,
        )

        assert "### Reviewer" in markdown
        assert "⏭ Skipped" in markdown
        assert "| Decision | ⏭ Skipped |" in markdown

    def test_skipped_executor(self) -> None:
        """Test formatting when executor is skipped."""
        from nubi.agents.monitor_result import MonitorDecision, MonitorResult

        monitor = MonitorResult(decision=MonitorDecision.APPROVE)

        markdown = _build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            executor=None,
            gates=None,
            review=None,
            monitor=monitor,
        )

        assert "### Executor" in markdown
        assert "⏭ Skipped" in markdown
        assert "| Status | ⏭ Skipped |" in markdown
        assert "### Gates" in markdown
        assert "| - | ⏭ Skipped |" in markdown

    def test_failed_gates(self) -> None:
        """Test formatting when a gate fails."""
        from nubi.agents.gate_result import GateCategory, GateResult, GatesResult, GateStatus
        from nubi.agents.monitor_result import MonitorDecision, MonitorResult
        from nubi.agents.result import ExecutorResult

        executor = ExecutorResult(
            status="success",
            commit_sha="abc123",
        )
        gates = GatesResult(
            discovered=[],
            gates=[
                GateResult(
                    name="ruff",
                    category=GateCategory.LINT,
                    status=GateStatus.FAILED,
                    output="3 errors",
                    error="unused import",
                ),
                GateResult(
                    name="pytest",
                    category=GateCategory.TEST,
                    status=GateStatus.SKIPPED,
                    output="",
                ),
            ],
            overall_passed=False,
        )
        monitor = MonitorResult(decision=MonitorDecision.APPROVE)

        markdown = _build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            executor=executor,
            gates=gates,
            review=None,
            monitor=monitor,
        )

        assert "| ruff | ❌ fail |" in markdown
        assert "| pytest | ⏭ skipped |" in markdown

    def test_ci_failed_monitor(self) -> None:
        """Test formatting when CI fails."""
        from nubi.agents.monitor_result import MonitorDecision, MonitorResult

        monitor = MonitorResult(
            decision=MonitorDecision.CI_FAILED,
            ci_status="failure",
        )

        markdown = _build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            executor=None,
            gates=None,
            review=None,
            monitor=monitor,
        )

        assert "🔄 CI Failed" in markdown
        assert "❌ failure" in markdown


class TestPostPipelineSummary:
    """Tests for the post_pipeline_summary function."""

    @patch("nubi.tools.github_api.httpx.post")
    def test_posts_new_comment(self, mock_post: MagicMock) -> None:
        """Test posting a new pipeline summary comment."""
        mock_post.return_value = MagicMock(status_code=201)

        with patch("nubi.tools.github_api.read_branch_file") as mock_read:
            mock_read.side_effect = [
                '{"status": "success", "commit_sha": "abc123", "summary": "test"}',
                '{"discovered": [], "gates": [], "overall_passed": true}',
                '{"decision": "approve", "feedback": "looks good"}',
                '{"decision": "approve", "ci_status": "success"}',
            ]

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="tok",
            )

        assert "posted" in result
        assert "42" in result
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/issues/42/comments" in call_args[0][0]
        assert "<!-- nubi-pipeline-summary -->" in call_args[1]["json"]["body"]
        assert "## Nubi Pipeline Summary" in call_args[1]["json"]["body"]

    @patch("nubi.tools.github_api.httpx.get")
    def test_updates_existing_comment(self, mock_get: MagicMock) -> None:
        """Test updating an existing pipeline summary comment."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"id": 999, "body": "<!-- nubi-pipeline-summary -->\nPrevious summary"},
                {"id": 1000, "body": "Some other comment"},
            ],
        )

        with patch("nubi.tools.github_api.httpx.patch") as mock_patch, patch(
            "nubi.tools.github_api.read_branch_file"
        ) as mock_read:
            mock_read.side_effect = [
                '{"status": "success", "commit_sha": "abc123", "summary": "test"}',
                '{"discovered": [], "gates": [], "overall_passed": true}',
                '{"decision": "approve", "feedback": "looks good"}',
                '{"decision": "approve", "ci_status": "success"}',
            ]
            mock_patch.return_value = MagicMock(status_code=200)

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="tok",
            )

        assert "updated" in result
        assert "42" in result
        mock_patch.assert_called_once()
        call_args = mock_patch.call_args
        assert "/comments/999" in call_args[0][0]

    @patch("nubi.tools.github_api.httpx.get")
    def test_creates_new_when_no_existing_comment(self, mock_get: MagicMock) -> None:
        """Test creating new comment when no existing marker found."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"id": 1000, "body": "Some other comment"}],
        )

        with patch("nubi.tools.github_api.httpx.post") as mock_post, patch(
            "nubi.tools.github_api.read_branch_file"
        ) as mock_read:
            mock_read.side_effect = [
                '{"status": "success", "commit_sha": "abc123", "summary": "test"}',
                '{"discovered": [], "gates": [], "overall_passed": true}',
                '{"decision": "approve", "feedback": "looks good"}',
                '{"decision": "approve", "ci_status": "success"}',
            ]
            mock_post.return_value = MagicMock(status_code=201)

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="tok",
            )

        assert "posted" in result
        mock_post.assert_called_once()

    def test_handles_missing_pr_number(self) -> None:
        """Test error handling for invalid PR URL."""
        result = post_pipeline_summary(
            pr_url="not-a-valid-url",
            repo="kuuji/nubi",
            branch="nubi/test-task",
            token="tok",
        )

        assert "Error" in result
        assert "Could not extract PR number" in result

    def test_handles_api_error(self) -> None:
        """Test error handling for API errors when posting comment."""
        with patch("nubi.tools.github_api.httpx.get") as mock_get, patch(
            "nubi.tools.github_api.httpx.post"
        ) as mock_post, patch(
            "nubi.tools.github_api.read_branch_file"
        ) as mock_read:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: [],
            )
            mock_post.return_value = MagicMock(status_code=403, text="Forbidden")
            mock_read.side_effect = [
                '{"status": "success", "commit_sha": "abc123", "summary": "test"}',
                '{"discovered": [], "gates": [], "overall_passed": true}',
                '{"decision": "approve", "feedback": "looks good"}',
                '{"decision": "approve", "ci_status": "success"}',
            ]

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="tok",
            )

        assert "Error" in result
        assert "403" in result

    @patch("nubi.tools.github_api.httpx.post")
    def test_handles_missing_artifact_files(self, mock_post: MagicMock) -> None:
        """Test graceful handling when artifact files are missing."""
        mock_post.return_value = MagicMock(status_code=201)

        with patch("nubi.tools.github_api.read_branch_file") as mock_read:
            mock_read.return_value = "Error: GitHub API returned 404 for .nubi/test/result.json"

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="tok",
            )

        assert "posted" in result
        mock_post.assert_called_once()
        body = mock_post.call_args[1]["json"]["body"]
        assert "⏭ Skipped" in body
