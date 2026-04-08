"""Tests for nubi.agents.review_result — ReviewResult, ReviewIssue, ReviewDecision models."""

from __future__ import annotations

import json

from nubi.agents.review_result import (
    ReviewDecision,
    ReviewIssue,
    ReviewResult,
    review_file_path,
    write_review_result,
)

TASK_ID = "test-task-1"


class TestReviewDecision:
    EXPECTED_VALUES = ["approve", "request-changes", "reject"]

    def test_all_decisions_exist(self) -> None:
        for value in self.EXPECTED_VALUES:
            assert ReviewDecision(value) == value

    def test_decision_count(self) -> None:
        assert len(ReviewDecision) == 3

    def test_decision_is_string_enum(self) -> None:
        assert isinstance(ReviewDecision.APPROVE, str)
        assert ReviewDecision.APPROVE == "approve"


class TestReviewIssue:
    def test_create_with_required_fields(self) -> None:
        issue = ReviewIssue(severity="critical", description="SQL injection")
        assert issue.severity == "critical"
        assert issue.description == "SQL injection"

    def test_defaults(self) -> None:
        issue = ReviewIssue(severity="minor")
        assert issue.file == ""
        assert issue.line is None
        assert issue.description == ""

    def test_full_fields(self) -> None:
        issue = ReviewIssue(
            severity="major",
            file="src/auth.py",
            line=42,
            description="Missing input validation",
        )
        assert issue.file == "src/auth.py"
        assert issue.line == 42

    def test_round_trip_json(self) -> None:
        issue = ReviewIssue(
            severity="suggestion",
            file="src/utils.py",
            line=10,
            description="Consider using a list comprehension",
        )
        data = json.loads(issue.model_dump_json())
        issue2 = ReviewIssue.model_validate(data)
        assert issue == issue2


class TestReviewResult:
    def test_create_approve(self) -> None:
        result = ReviewResult(decision=ReviewDecision.APPROVE)
        assert result.decision == ReviewDecision.APPROVE

    def test_defaults(self) -> None:
        result = ReviewResult(decision=ReviewDecision.APPROVE)
        assert result.feedback == ""
        assert result.summary == ""
        assert result.issues == []

    def test_full_fields(self) -> None:
        result = ReviewResult(
            decision=ReviewDecision.REQUEST_CHANGES,
            feedback="Missing error handling in the API endpoint",
            summary="Code works but needs better error handling",
            issues=[
                ReviewIssue(
                    severity="major", file="src/api.py", line=55, description="No try/except"
                ),
            ],
        )
        assert result.decision == ReviewDecision.REQUEST_CHANGES
        assert len(result.issues) == 1
        assert result.issues[0].severity == "major"

    def test_round_trip_json(self) -> None:
        result = ReviewResult(
            decision=ReviewDecision.REJECT,
            feedback="Changes don't match the task description",
            summary="Wrong implementation",
            issues=[
                ReviewIssue(severity="critical", description="Completely wrong approach"),
                ReviewIssue(severity="minor", file="README.md", description="Typo"),
            ],
        )
        data = json.loads(result.model_dump_json())
        result2 = ReviewResult.model_validate(data)
        assert result2.decision == ReviewDecision.REJECT
        assert len(result2.issues) == 2

    def test_request_changes_decision(self) -> None:
        result = ReviewResult(decision=ReviewDecision.REQUEST_CHANGES)
        assert result.decision == "request-changes"


class TestReviewFilePath:
    def test_review_file_path_function(self) -> None:
        assert review_file_path("my-task") == ".nubi/my-task/review.json"


class TestWriteReviewResult:
    def test_writes_json_file(self, tmp_path: str) -> None:
        result = ReviewResult(decision=ReviewDecision.APPROVE, summary="LGTM")
        write_review_result(result, tmp_path, TASK_ID)
        review_path = f"{tmp_path}/.nubi/{TASK_ID}/review.json"
        with open(review_path) as f:
            data = json.load(f)
        assert data["decision"] == "approve"
        assert data["summary"] == "LGTM"

    def test_creates_nubi_dir(self, tmp_path: str) -> None:
        result = ReviewResult(decision=ReviewDecision.APPROVE)
        write_review_result(result, tmp_path, TASK_ID)
        import os

        assert os.path.isdir(f"{tmp_path}/.nubi/{TASK_ID}")

    def test_overwrites_existing(self, tmp_path: str) -> None:
        result1 = ReviewResult(decision=ReviewDecision.REQUEST_CHANGES, feedback="Fix tests")
        write_review_result(result1, tmp_path, TASK_ID)

        result2 = ReviewResult(decision=ReviewDecision.APPROVE, feedback="All good")
        write_review_result(result2, tmp_path, TASK_ID)

        review_path = f"{tmp_path}/.nubi/{TASK_ID}/review.json"
        with open(review_path) as f:
            data = json.load(f)
        assert data["decision"] == "approve"
        assert data["feedback"] == "All good"

    def test_writes_with_issues(self, tmp_path: str) -> None:
        result = ReviewResult(
            decision=ReviewDecision.REJECT,
            feedback="Security vulnerability found",
            issues=[
                ReviewIssue(
                    severity="critical",
                    file="src/auth.py",
                    line=10,
                    description="Hardcoded secret",
                ),
            ],
        )
        write_review_result(result, tmp_path, TASK_ID)
        review_path = f"{tmp_path}/.nubi/{TASK_ID}/review.json"
        with open(review_path) as f:
            data = json.load(f)
        assert data["issues"][0]["severity"] == "critical"
        assert data["issues"][0]["file"] == "src/auth.py"
