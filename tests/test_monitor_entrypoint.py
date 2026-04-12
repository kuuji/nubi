"""Tests for nubi.monitor_entrypoint — monitor PR flow and decision handling."""

from __future__ import annotations

from nubi.agents.monitor_result import MonitorConcern, MonitorDecision, MonitorResult
from nubi.monitor_entrypoint import _build_pr_body


class TestBuildPrBody:
    def test_approve_includes_decision_label(self) -> None:
        audit = MonitorResult(decision=MonitorDecision.APPROVE, summary="All good")
        body = _build_pr_body("Fix bug", audit)
        assert "Approved" in body

    def test_flag_includes_decision_label(self) -> None:
        audit = MonitorResult(decision=MonitorDecision.FLAG, summary="Concerns found")
        body = _build_pr_body("Fix bug", audit)
        assert "Flagged" in body

    def test_ci_failed_includes_decision_label(self) -> None:
        audit = MonitorResult(
            decision=MonitorDecision.CI_FAILED,
            summary="CI checks failure",
        )
        body = _build_pr_body("Fix bug", audit)
        assert "CI Failed" in body

    def test_escalate_includes_decision_label(self) -> None:
        audit = MonitorResult(decision=MonitorDecision.ESCALATE, summary="Timed out")
        body = _build_pr_body("Fix bug", audit)
        assert "Escalated" in body

    def test_includes_summary(self) -> None:
        audit = MonitorResult(decision=MonitorDecision.APPROVE, summary="Looks great")
        body = _build_pr_body("Fix bug", audit)
        assert "Looks great" in body

    def test_includes_pr_summary_when_available(self) -> None:
        audit = MonitorResult(
            decision=MonitorDecision.APPROVE,
            summary="short",
            pr_summary="## Detailed\nRich description here",
        )
        body = _build_pr_body("Fix bug", audit)
        assert "Rich description here" in body
        assert "short" not in body

    def test_includes_concerns(self) -> None:
        audit = MonitorResult(
            decision=MonitorDecision.FLAG,
            summary="Issues found",
            concerns=[
                MonitorConcern(severity="major", area="security", description="Hardcoded key"),
            ],
        )
        body = _build_pr_body("Fix bug", audit)
        assert "Concerns" in body
        assert "Hardcoded key" in body
        assert "major" in body

    def test_includes_ci_feedback(self) -> None:
        audit = MonitorResult(
            decision=MonitorDecision.CI_FAILED,
            summary="CI failed",
            ci_feedback="### lint\nruff found 3 errors",
        )
        body = _build_pr_body("Fix bug", audit)
        assert "CI Feedback" in body
        assert "ruff found 3 errors" in body

    def test_no_ci_feedback_section_when_empty(self) -> None:
        audit = MonitorResult(decision=MonitorDecision.APPROVE, summary="All good")
        body = _build_pr_body("Fix bug", audit)
        assert "CI Feedback" not in body

    def test_includes_nubi_footer(self) -> None:
        audit = MonitorResult(decision=MonitorDecision.APPROVE, summary="Done")
        body = _build_pr_body("Fix bug", audit)
        assert "Nubi" in body

    def test_falls_back_to_description_when_no_summary(self) -> None:
        audit = MonitorResult(decision=MonitorDecision.APPROVE, summary="")
        body = _build_pr_body("Fix the login bug", audit)
        assert "Fix the login bug" in body
