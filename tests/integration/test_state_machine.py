"""Integration tests for the controller state machine.

These tests run against a real k3d cluster with the kopf controller in-process.
LLM and GitHub API calls are mocked via the ScenarioResultStore.
Agent containers use a fake alpine image that sleeps + exits.

Run: pytest tests/integration/ -v
Prerequisites: ./scripts/integration-setup.sh
"""

from __future__ import annotations

import pytest

from nubi.agents.gate_result import GateCategory, GateResult, GatesResult, GateStatus
from nubi.agents.result import ExecutorResult
from nubi.agents.review_result import ReviewDecision, ReviewResult
from tests.integration.helpers import await_phase, create_taskspec, get_taskspec_status
from tests.integration.scenario_store import ScenarioResultStore

pytestmark = pytest.mark.integration


def _ok_executor(sha: str = "abc123") -> ExecutorResult:
    return ExecutorResult(status="success", commit_sha=sha, summary="Done")


def _ok_gates(attempt: int = 1) -> GatesResult:
    return GatesResult(discovered=[], gates=[], overall_passed=True, attempt=attempt)


def _failed_gates(attempt: int = 1) -> GatesResult:
    return GatesResult(
        discovered=[],
        gates=[
            GateResult(
                name="pytest",
                category=GateCategory.TEST,
                status=GateStatus.FAILED,
                output="2 tests failed",
            )
        ],
        overall_passed=False,
        attempt=attempt,
    )


def _approve_review() -> ReviewResult:
    return ReviewResult(decision=ReviewDecision.APPROVE, feedback="LGTM", summary="Approved")


def _request_changes_review(feedback: str = "Fix tests") -> ReviewResult:
    return ReviewResult(
        decision=ReviewDecision.REQUEST_CHANGES,
        feedback=feedback,
        summary="Needs work",
    )


def _reject_review() -> ReviewResult:
    return ReviewResult(
        decision=ReviewDecision.REJECT,
        feedback="Fundamentally wrong",
        summary="Rejected",
    )


class TestHappyPath:
    """Scenario 1: executor ok, gates pass, reviewer approve → Done."""

    async def test_executor_gates_reviewer_approve(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        scenario_store.set_executor_result(task_name, _ok_executor())
        scenario_store.set_gates_result(task_name, _ok_gates())
        scenario_store.set_review_result(task_name, _approve_review())

        await create_taskspec(task_name, review_enabled=True)
        phase = await await_phase(task_name, "Done", timeout=45)

        assert phase == "Done"
        status = await get_taskspec_status(task_name)
        assert status["stages"]["executor"]["status"] == "complete"
        assert status["stages"]["reviewer"]["decision"] == "approve"


class TestReviewerRequestChangesLoop:
    """Scenario 2: reviewer request-changes → executor retry → approve → Done."""

    async def test_request_changes_then_approve(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        # Attempt 1: executor ok, gates pass, reviewer requests changes
        scenario_store.set_executor_result(task_name, _ok_executor("sha1"), attempt=1)
        scenario_store.set_gates_result(task_name, _ok_gates(attempt=1), attempt=1)
        scenario_store.set_review_result(
            task_name, _request_changes_review("Missing error handling"), attempt=1
        )

        # Attempt 2: executor fixes, gates pass, reviewer approves
        scenario_store.set_executor_result(task_name, _ok_executor("sha2"), attempt=2)
        scenario_store.set_gates_result(task_name, _ok_gates(attempt=1), attempt=2)
        scenario_store.set_review_result(task_name, _approve_review(), attempt=2)

        await create_taskspec(task_name, review_enabled=True)
        phase = await await_phase(task_name, "Done", timeout=60)

        assert phase == "Done"
        status = await get_taskspec_status(task_name)
        assert status["stages"]["reviewer"]["decision"] == "approve"


class TestGateMaxRetriesEscalated:
    """Scenario 3: gates fail at max retries → Escalated.

    Note: gate retry (attempt < max) is handled inside the executor entrypoint's
    gate loop, not at the controller level. The controller only sees the final
    gate result. If gates failed after all entrypoint retries, the controller
    escalates based on max_retries.
    """

    async def test_gate_fail_escalates(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        scenario_store.set_executor_result(task_name, _ok_executor())
        scenario_store.set_gates_result(task_name, _failed_gates(attempt=1))

        await create_taskspec(task_name, review_enabled=True, max_retries=1)
        phase = await await_phase(task_name, "Escalated", timeout=30)

        assert phase == "Escalated"


class TestReviewerReject:
    """Scenario 5: executor ok, gates pass, reviewer reject → Escalated."""

    async def test_reject_escalates(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        scenario_store.set_executor_result(task_name, _ok_executor())
        scenario_store.set_gates_result(task_name, _ok_gates())
        scenario_store.set_review_result(task_name, _reject_review())

        await create_taskspec(task_name, review_enabled=True)
        phase = await await_phase(task_name, "Escalated", timeout=45)

        assert phase == "Escalated"
        status = await get_taskspec_status(task_name)
        assert status["stages"]["reviewer"]["decision"] == "reject"


class TestExecutorJobFails:
    """Scenario 6: executor Job exits with error → Failed."""

    async def test_job_failure(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        # No results registered — the Job fails before producing any
        await create_taskspec(
            task_name,
            review_enabled=True,
            timeout="5s",  # Very short — fake agent sleeps 2s then exits 0,
            # but the Job will complete before timeout. We need to make it fail.
            # Use a separate approach: register no results, so when the controller
            # reads them it gets a ResultError → Failed.
        )

        # The fake agent will succeed (exit 0), but reading results will fail
        # because nothing is registered in the scenario store.
        phase = await await_phase(task_name, "Failed", timeout=30)
        assert phase == "Failed"


class TestReviewDisabled:
    """Scenario 7: review disabled, executor ok, gates pass → Done."""

    async def test_review_disabled_goes_to_done(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        scenario_store.set_executor_result(task_name, _ok_executor())
        scenario_store.set_gates_result(task_name, _ok_gates())

        await create_taskspec(task_name, review_enabled=False)
        phase = await await_phase(task_name, "Done", timeout=30)

        assert phase == "Done"
        status = await get_taskspec_status(task_name)
        assert status["stages"]["executor"]["status"] == "complete"
        # No reviewer stage
        assert (
            "reviewer" not in status.get("stages", {})
            or status["stages"].get("reviewer", {}).get("status", "pending") == "pending"
        )
