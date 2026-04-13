"""Tests for nubi.tools.github_api — GitHub API helpers."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

from nubi.tools.github_api import (
    PIPELINE_SUMMARY_MARKER,
    _decision_icon,
    _format_gates_table,
    _gate_result_label,
    _gate_status_icon,
    build_pipeline_summary_markdown,
    post_pipeline_summary,
)


class TestPrNumberFromUrl:
    def test_standard_url(self) -> None:
        from nubi.tools.github_api import _pr_number_from_url

        assert _pr_number_from_url("https://github.com/kuuji/nubi/pull/42") == 42

    def test_trailing_slash(self) -> None:
        from nubi.tools.github_api import _pr_number_from_url

        assert _pr_number_from_url("https://github.com/kuuji/nubi/pull/42/") == 42

    def test_invalid_url(self) -> None:
        from nubi.tools.github_api import _pr_number_from_url

        assert _pr_number_from_url("https://github.com/kuuji/nubi") is None

    def test_non_numeric(self) -> None:
        from nubi.tools.github_api import _pr_number_from_url

        assert _pr_number_from_url("https://github.com/kuuji/nubi/pull/abc") is None

    def test_empty(self) -> None:
        from nubi.tools.github_api import _pr_number_from_url

        assert _pr_number_from_url("") is None


class TestUpdatePrFromUrl:
    @patch("nubi.tools.github_api.httpx.patch")
    def test_updates_pr(self, mock_patch: MagicMock) -> None:
        from nubi.tools.github_api import configure, update_pr_from_url

        configure(repo="kuuji/nubi", base_branch="main", task_branch="nubi/t1", token="tok")
        mock_patch.return_value = MagicMock(status_code=200)

        update_pr_from_url("https://github.com/kuuji/nubi/pull/42", "title", "body")

        mock_patch.assert_called_once()
        call_args = mock_patch.call_args
        assert "/pulls/42" in call_args[0][0]
        assert call_args[1]["json"] == {"title": "title", "body": "body"}

    @patch("nubi.tools.github_api.httpx.patch")
    def test_invalid_url_skips(self, mock_patch: MagicMock) -> None:
        from nubi.tools.github_api import update_pr_from_url

        update_pr_from_url("not-a-url", "title", "body")
        mock_patch.assert_not_called()


class TestMarkPrReady:
    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api.httpx.get")
    def test_marks_ready_via_graphql(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        from nubi.tools.github_api import configure, mark_pr_ready

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
        from nubi.tools.github_api import configure, mark_pr_ready

        configure(repo="kuuji/nubi", base_branch="main", task_branch="nubi/t1", token="tok")
        mock_get.return_value = MagicMock(status_code=404)

        mark_pr_ready("https://github.com/kuuji/nubi/pull/42")

        mock_post.assert_not_called()

    @patch("nubi.tools.github_api.httpx.post")
    @patch("nubi.tools.github_api.httpx.get")
    def test_skips_on_invalid_url(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        from nubi.tools.github_api import mark_pr_ready

        mark_pr_ready("not-a-url")

        mock_get.assert_not_called()
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Pipeline summary tests
# ---------------------------------------------------------------------------


class TestGateHelpers:
    def test_gate_status_icon_passed(self) -> None:
        assert _gate_status_icon("passed") == "✅"

    def test_gate_status_icon_failed(self) -> None:
        assert _gate_status_icon("failed") == "❌"

    def test_gate_status_icon_skipped(self) -> None:
        assert _gate_status_icon("skipped") == "⏭"

    def test_gate_status_icon_unknown(self) -> None:
        assert _gate_status_icon("anything") == "❓"

    def test_gate_status_icon_none(self) -> None:
        assert _gate_status_icon(None) == "❓"

    def test_gate_result_label_pass(self) -> None:
        assert _gate_result_label("passed") == "pass"

    def test_gate_result_label_fail(self) -> None:
        assert _gate_result_label("failed") == "fail"

    def test_gate_result_label_skipped(self) -> None:
        assert _gate_result_label("skipped") == "skipped"

    def test_gate_result_label_unknown(self) -> None:
        assert _gate_result_label("unknown") == "unknown"


class TestDecisionIcon:
    def test_approve(self) -> None:
        assert _decision_icon("approve") == "✅"

    def test_flag(self) -> None:
        assert _decision_icon("flag") == "🚩"

    def test_ci_failed(self) -> None:
        assert _decision_icon("ci-failed") == "❌"

    def test_escalate(self) -> None:
        assert _decision_icon("escalate") == "⚠️"

    def test_skipped(self) -> None:
        assert _decision_icon("skipped") == "⏭"

    def test_unknown(self) -> None:
        assert _decision_icon("anything") == "❓"


class TestFormatGatesTable:
    def test_empty_gates(self) -> None:
        result = _format_gates_table([])
        assert "| Gate | Result | Details |" in result

    def test_single_passed_gate(self) -> None:
        gates = [
            {
                "name": "ruff",
                "status": "passed",
                "output": "0 errors",
            }
        ]
        result = _format_gates_table(gates)
        assert "ruff" in result
        assert "✅ pass" in result
        assert "0 errors" in result

    def test_failed_gate(self) -> None:
        gates = [
            {
                "name": "ruff",
                "status": "failed",
                "output": "3 errors: E302, E501, W503",
            }
        ]
        result = _format_gates_table(gates)
        assert "ruff" in result
        assert "❌ fail" in result

    def test_skipped_gate(self) -> None:
        gates = [
            {
                "name": "pytest",
                "status": "skipped",
                "output": "blocked by lint failure",
            }
        ]
        result = _format_gates_table(gates)
        assert "pytest" in result
        assert "⏭ skipped" in result

    def test_diff_size_gate_shows_stats(self) -> None:
        gates = [
            {
                "name": "diff_size",
                "status": "passed",
                "output": "src/a.py | 10 +\nb.txt       |  5 -\n2 files changed",
            }
        ]
        result = _format_gates_table(gates)
        assert "diff_size" in result
        assert "src/a.py" in result

    def test_gate_with_pipe_in_output_escaped(self) -> None:
        gates = [
            {
                "name": "custom",
                "status": "passed",
                "output": "a | b | c\nx | y | z",
            }
        ]
        result = _format_gates_table(gates)
        # Pipe characters should be escaped with backslash
        assert "\\|" in result


class TestBuildPipelineSummaryMarkdown:
    def test_includes_header(self) -> None:
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )
        assert "## Nubi Pipeline Summary" in result
        assert "`test-task`" in result
        assert "`nubi/test-task`" in result

    def test_includes_marker(self) -> None:
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )
        assert PIPELINE_SUMMARY_MARKER in result

    def test_executor_section_complete(self) -> None:
        result_data = {
            "overall_passed": True,
            "attempt": 2,
            "commit_sha": "a1b2c3d4e5f6",
            "summary": "Task completed successfully",
            "gates": [],
        }
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=result_data,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )
        assert "### Executor" in result
        assert "✅ Complete" in result
        assert "2" in result  # attempt
        assert "`a1b2c3d4`" in result  # commit short (8 chars)

    def test_executor_section_failed(self) -> None:
        result_data = {
            "overall_passed": False,
            "attempt": 1,
            "summary": "Task failed",
        }
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=result_data,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )
        assert "❌ Failed" in result

    def test_executor_section_missing(self) -> None:
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )
        assert "### Executor" in result
        assert "⏭ Not available" in result

    def test_gates_section_complete(self) -> None:
        gates_data = {
            "gates": [
                {
                    "name": "ruff",
                    "status": "passed",
                    "output": "0 errors",
                    "attempt": 1,
                },
                {
                    "name": "pytest",
                    "status": "passed",
                    "output": "10 passed",
                    "attempt": 1,
                },
            ]
        }
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=gates_data,
            review_data=None,
            monitor_data=None,
        )
        assert "### Gates" in result
        assert "ruff" in result
        assert "pytest" in result

    def test_gates_section_with_failed_attempts(self) -> None:
        gates_data = {
            "gates": [
                {
                    "name": "ruff",
                    "status": "failed",
                    "output": "3 errors",
                    "attempt": 1,
                },
                {
                    "name": "ruff",
                    "status": "passed",
                    "output": "0 errors",
                    "attempt": 2,
                },
            ]
        }
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=gates_data,
            review_data=None,
            monitor_data=None,
        )
        assert "<details>" in result
        assert "Gate details (attempt 1 — failed)" in result

    def test_gates_section_missing(self) -> None:
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )
        assert "### Gates" in result
        assert "⏭ No data" in result

    def test_reviewer_section_approved(self) -> None:
        review_data = {
            "decision": "approve",
            "feedback": "Clean implementation. Good work!",
        }
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=review_data,
            monitor_data=None,
        )
        assert "### Reviewer" in result
        assert "✅ Approve" in result
        assert "Clean implementation" in result

    def test_reviewer_section_skipped(self) -> None:
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )
        assert "### Reviewer" in result
        assert "⏭ Skipped" in result

    def test_monitor_section_approved(self) -> None:
        monitor_data = {
            "decision": "approve",
            "ci_status": "success",
        }
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=monitor_data,
        )
        assert "### Monitor" in result
        assert "✅ Approve" in result
        assert "CI Status" in result

    def test_monitor_section_ci_failed(self) -> None:
        monitor_data = {
            "decision": "ci-failed",
            "ci_status": "failure",
        }
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=monitor_data,
        )
        assert "### Monitor" in result
        assert "❌ Ci-Failed" in result

    def test_monitor_section_skipped(self) -> None:
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=None,
            monitor_data=None,
        )
        assert "### Monitor" in result
        assert "⏭ Skipped" in result

    def test_all_sections_present(self) -> None:
        result_data = {"overall_passed": True, "gates": []}
        gates_data = {"gates": []}
        review_data = {"decision": "approve", "feedback": "ok"}
        monitor_data = {"decision": "approve", "ci_status": "success"}

        result = build_pipeline_summary_markdown(
            task_id="my-task",
            branch="nubi/my-task",
            result_data=result_data,
            gates_data=gates_data,
            review_data=review_data,
            monitor_data=monitor_data,
        )

        assert "### Executor" in result
        assert "### Gates" in result
        assert "### Reviewer" in result
        assert "### Monitor" in result
        assert PIPELINE_SUMMARY_MARKER in result

    def test_feedback_truncated_at_300_chars(self) -> None:
        review_data = {
            "decision": "approve",
            "feedback": "A" * 500,
        }
        result = build_pipeline_summary_markdown(
            task_id="test-task",
            branch="nubi/test-task",
            result_data=None,
            gates_data=None,
            review_data=review_data,
            monitor_data=None,
        )
        # Should have truncation
        assert "..." in result


class TestPostPipelineSummary:
    @patch("nubi.tools.github_api._find_existing_summary_comment")
    @patch("nubi.tools.github_api._post_new_comment")
    def test_posts_new_comment_when_none_exists(
        self, mock_post: MagicMock, mock_find: MagicMock
    ) -> None:
        mock_find.return_value = None
        mock_post.return_value = True

        with patch("nubi.tools.github_api._read_artifact") as mock_read:
            mock_read.return_value = None

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="ghp_test",
            )

        assert result is True
        mock_find.assert_called_once_with(42, "kuuji/nubi", "ghp_test")
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == 42  # pr_number
        assert call_args[0][1] == "kuuji/nubi"  # repo
        assert call_args[0][2] == "ghp_test"  # token
        assert "## Nubi Pipeline Summary" in call_args[0][3]

    @patch("nubi.tools.github_api._find_existing_summary_comment")
    @patch("nubi.tools.github_api._update_existing_comment")
    def test_updates_existing_comment(self, mock_update: MagicMock, mock_find: MagicMock) -> None:
        mock_find.return_value = 12345
        mock_update.return_value = True

        with patch("nubi.tools.github_api._read_artifact") as mock_read:
            mock_read.return_value = None

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="ghp_test",
            )

        assert result is True
        mock_find.assert_called_once_with(42, "kuuji/nubi", "ghp_test")
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == 12345  # comment_id
        assert call_args[0][1] == "kuuji/nubi"  # repo
        assert call_args[0][2] == "ghp_test"  # token

    def test_invalid_pr_url_returns_false(self) -> None:
        with patch("nubi.tools.github_api._read_artifact"):
            result = post_pipeline_summary(
                pr_url="not-a-url",
                repo="kuuji/nubi",
                branch="nubi/test-task",
                token="ghp_test",
            )
        assert result is False

    @patch("nubi.tools.github_api._find_existing_summary_comment")
    @patch("nubi.tools.github_api._post_new_comment")
    def test_reads_all_artifact_files(self, mock_post: MagicMock, mock_find: MagicMock) -> None:
        mock_find.return_value = None
        mock_post.return_value = True

        with patch("nubi.tools.github_api._read_artifact") as mock_read:
            mock_read.side_effect = [
                {"overall_passed": True},  # result.json
                {"gates": []},  # gates.json
                {"decision": "approve"},  # review.json
                {"decision": "approve"},  # monitor.json
            ]

            result = post_pipeline_summary(
                pr_url="https://github.com/kuuji/nubi/pull/42",
                repo="kuuji/nubi",
                branch="nubi/my-task-123",
                token="ghp_test",
            )

        assert result is True
        # Verify all four artifact files were read
        assert mock_read.call_count == 4
        calls = mock_read.call_args_list
        assert ".nubi/my-task-123/result.json" in calls[0][0]
        assert ".nubi/my-task-123/gates.json" in calls[1][0]
        assert ".nubi/my-task-123/review.json" in calls[2][0]
        assert ".nubi/my-task-123/monitor.json" in calls[3][0]


class TestReadArtifact:
    @patch("nubi.tools.github_api.httpx.get")
    def test_returns_parsed_json_on_success(self, mock_get: MagicMock) -> None:
        from nubi.tools.github_api import _read_artifact

        data = {"key": "value"}
        content_b64 = base64.b64encode(json.dumps(data).encode()).decode()
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"content": content_b64},
        )

        result = _read_artifact(
            ".nubi/task/result.json",
            repo="kuuji/nubi",
            branch="nubi/task",
            token="test-token",
        )

        assert result == data
        mock_get.assert_called_once()

    @patch("nubi.tools.github_api.httpx.get")
    def test_returns_none_on_404(self, mock_get: MagicMock) -> None:
        from nubi.tools.github_api import _read_artifact

        mock_get.return_value = MagicMock(status_code=404)

        result = _read_artifact(
            ".nubi/task/missing.json",
            repo="kuuji/nubi",
            branch="nubi/task",
            token="test-token",
        )

        assert result is None

    @patch("nubi.tools.github_api.httpx.get")
    def test_returns_none_on_invalid_json(self, mock_get: MagicMock) -> None:
        from nubi.tools.github_api import _read_artifact

        content_b64 = base64.b64encode(b"not valid json{{").decode()
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"content": content_b64},
        )

        result = _read_artifact(
            ".nubi/task/bad.json",
            repo="kuuji/nubi",
            branch="nubi/task",
            token="test-token",
        )

        assert result is None
