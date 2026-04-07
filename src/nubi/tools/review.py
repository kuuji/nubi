"""Review tool — structured output channel for the reviewer agent."""

from __future__ import annotations

from strands import tool

from nubi.agents.review_result import ReviewDecision, ReviewIssue, ReviewResult

_review_result: ReviewResult | None = None


def get_review_result() -> ReviewResult | None:
    """Return the review result captured by submit_review, or None if not called."""
    return _review_result


def reset_review_result() -> None:
    """Reset the captured review result. Used in tests."""
    global _review_result
    _review_result = None


@tool
def submit_review(
    decision: str,
    feedback: str,
    summary: str,
    issues: list[dict[str, object]] | None = None,
) -> str:
    """Submit your final code review decision.

    You MUST call this tool exactly once at the end of your review.

    Args:
        decision: One of "approve", "request-changes", or "reject".
        feedback: Detailed explanation of your decision.
        summary: One-sentence summary of the review outcome.
        issues: Optional list of specific issues found. Each dict should have:
            severity (critical/major/minor/suggestion), file, line, description.
    """
    global _review_result

    try:
        parsed_decision = ReviewDecision(decision)
    except ValueError:
        return f"Invalid decision: {decision!r}. Must be one of: approve, request-changes, reject."

    parsed_issues = []
    for item in issues or []:
        parsed_issues.append(ReviewIssue.model_validate(item))

    _review_result = ReviewResult(
        decision=parsed_decision,
        feedback=feedback,
        summary=summary,
        issues=parsed_issues,
    )

    return f"Review submitted: {parsed_decision.value}"
