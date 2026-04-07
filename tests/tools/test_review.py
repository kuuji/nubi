"""Tests for nubi.tools.review — submit_review tool."""

from __future__ import annotations

from nubi.agents.review_result import ReviewDecision
from nubi.tools.review import get_review_result, reset_review_result, submit_review


class TestSubmitReview:
    def setup_method(self) -> None:
        reset_review_result()

    def test_approve(self) -> None:
        result = submit_review(
            decision="approve",
            feedback="Code looks good",
            summary="LGTM",
        )
        assert "approve" in result
        review = get_review_result()
        assert review is not None
        assert review.decision == ReviewDecision.APPROVE
        assert review.feedback == "Code looks good"
        assert review.summary == "LGTM"

    def test_request_changes(self) -> None:
        submit_review(
            decision="request-changes",
            feedback="Missing error handling",
            summary="Needs work",
        )
        review = get_review_result()
        assert review is not None
        assert review.decision == ReviewDecision.REQUEST_CHANGES

    def test_reject(self) -> None:
        submit_review(
            decision="reject",
            feedback="Wrong approach",
            summary="Not acceptable",
        )
        review = get_review_result()
        assert review is not None
        assert review.decision == ReviewDecision.REJECT

    def test_invalid_decision_returns_error(self) -> None:
        result = submit_review(
            decision="maybe",
            feedback="Not sure",
            summary="Hmm",
        )
        assert "Invalid decision" in result
        assert get_review_result() is None

    def test_with_issues(self) -> None:
        submit_review(
            decision="request-changes",
            feedback="Found issues",
            summary="Fix these",
            issues=[
                {
                    "severity": "critical",
                    "file": "src/auth.py",
                    "line": 42,
                    "description": "SQL injection",
                },
                {"severity": "minor", "description": "Typo in docstring"},
            ],
        )
        review = get_review_result()
        assert review is not None
        assert len(review.issues) == 2
        assert review.issues[0].severity == "critical"
        assert review.issues[0].file == "src/auth.py"
        assert review.issues[0].line == 42
        assert review.issues[1].severity == "minor"

    def test_no_issues(self) -> None:
        submit_review(
            decision="approve",
            feedback="All good",
            summary="Clean",
        )
        review = get_review_result()
        assert review is not None
        assert review.issues == []

    def test_none_issues(self) -> None:
        submit_review(
            decision="approve",
            feedback="All good",
            summary="Clean",
            issues=None,
        )
        review = get_review_result()
        assert review is not None
        assert review.issues == []


class TestGetReviewResult:
    def setup_method(self) -> None:
        reset_review_result()

    def test_returns_none_before_submit(self) -> None:
        assert get_review_result() is None

    def test_returns_result_after_submit(self) -> None:
        submit_review(decision="approve", feedback="ok", summary="ok")
        assert get_review_result() is not None


class TestResetReviewResult:
    def test_reset_clears_result(self) -> None:
        submit_review(decision="approve", feedback="ok", summary="ok")
        assert get_review_result() is not None
        reset_review_result()
        assert get_review_result() is None
