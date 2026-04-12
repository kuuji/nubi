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
from nubi.agents.monitor_result import MonitorConcern, MonitorDecision, MonitorResult
from nubi.agents.result import ExecutorResult
from nubi.agents.review_result import ReviewDecision, ReviewResult
from nubi.crd.defaults import CANCEL_ANNOTATION, RETRY_ANNOTATION
from tests.integration.helpers import (
    await_phase,
    create_taskspec,
    get_taskspec_status,
    patch_taskspec_annotation,
)
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


def _approve_monitor(pr_url: str = "") -> MonitorResult:
    return MonitorResult(
        decision=MonitorDecision.APPROVE,
        summary="Pipeline audit passed",
        pr_url=pr_url,
    )


def _flag_monitor() -> MonitorResult:
    return MonitorResult(
        decision=MonitorDecision.FLAG,
        summary="Security concern found",
        concerns=[
            MonitorConcern(
                severity="major",
                area="security",
                description="Hardcoded API key detected",
            )
        ],
    )


class TestHappyPath:
    """Scenario 1: executor ok, gates pass, reviewer approve, monitor approve → Done."""

    async def test_executor_gates_reviewer_monitor_approve(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        scenario_store.set_executor_result(task_name, _ok_executor())
        scenario_store.set_gates_result(task_name, _ok_gates())
        scenario_store.set_review_result(task_name, _approve_review())
        scenario_store.set_monitor_result(
            task_name, _approve_monitor("https://github.com/test/repo/pull/1")
        )

        await create_taskspec(task_name, review_enabled=True)
        phase = await await_phase(task_name, "Done", timeout=60)

        assert phase == "Done"
        status = await get_taskspec_status(task_name)
        assert status["stages"]["executor"]["status"] == "complete"
        assert status["stages"]["reviewer"]["decision"] == "approve"
        assert status["stages"]["monitor"]["decision"] == "approve"


class TestReviewerRequestChangesLoop:
    """Scenario 2: reviewer request-changes → executor retry → approve → monitor → Done."""

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

        # Monitor approves after reviewer approves
        scenario_store.set_monitor_result(task_name, _approve_monitor())

        await create_taskspec(task_name, review_enabled=True)
        phase = await await_phase(task_name, "Done", timeout=90)

        assert phase == "Done"
        status = await get_taskspec_status(task_name)
        assert status["stages"]["reviewer"]["decision"] == "approve"
        assert status["stages"]["monitor"]["decision"] == "approve"


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


class TestMonitorFlagConcerns:
    """Scenario 8: monitor flags concerns → Done (not Failed)."""

    async def test_monitor_flag_still_done(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        scenario_store.set_executor_result(task_name, _ok_executor())
        scenario_store.set_gates_result(task_name, _ok_gates())
        scenario_store.set_review_result(task_name, _approve_review())
        scenario_store.set_monitor_result(task_name, _flag_monitor())

        await create_taskspec(task_name, review_enabled=True)
        phase = await await_phase(task_name, "Done", timeout=60)

        assert phase == "Done"
        status = await get_taskspec_status(task_name)
        assert status["stages"]["monitor"]["decision"] == "flag"
        assert len(status["stages"]["monitor"]["concerns"]) > 0


class TestMonitorDisabled:
    """Scenario 9: monitoring disabled, reviewer approve → Done directly."""

    async def test_monitor_disabled_skips_monitor(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        scenario_store.set_executor_result(task_name, _ok_executor())
        scenario_store.set_gates_result(task_name, _ok_gates())
        scenario_store.set_review_result(task_name, _approve_review())

        await create_taskspec(task_name, review_enabled=True, monitoring_summary=False)
        phase = await await_phase(task_name, "Done", timeout=45)

        assert phase == "Done"
        status = await get_taskspec_status(task_name)
        assert status["stages"]["reviewer"]["decision"] == "approve"
        # No monitor stage
        assert (
            "monitor" not in status.get("stages", {})
            or status["stages"].get("monitor", {}).get("status", "pending") == "pending"
        )


class TestRetryFailedTask:
    """Scenario 10: executor fails → Failed → retry annotation → re-runs → Done."""

    async def test_failed_then_retry_succeeds(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        # Attempt 1: no results registered → executor read fails → Failed
        await create_taskspec(task_name, review_enabled=False)
        phase = await await_phase(task_name, "Failed", timeout=30)
        assert phase == "Failed"

        # Register passing results for the retry attempt
        scenario_store.set_executor_result(task_name, _ok_executor("retry-sha"))
        scenario_store.set_gates_result(task_name, _ok_gates())

        # Trigger retry via annotation
        import time

        await patch_taskspec_annotation(task_name, RETRY_ANNOTATION, str(int(time.time())))
        phase = await await_phase(task_name, "Done", timeout=60)

        assert phase == "Done"
        status = await get_taskspec_status(task_name)
        assert status["stages"]["executor"]["status"] == "complete"


class TestRetryEscalatedTask:
    """Scenario 11: gate fail → Escalated → retry → succeeds → Done."""

    async def test_escalated_then_retry_succeeds(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        # Attempt 1: gates fail, max_retries=1 → Escalated
        scenario_store.set_executor_result(task_name, _ok_executor())
        scenario_store.set_gates_result(task_name, _failed_gates(attempt=1))

        await create_taskspec(task_name, review_enabled=False, max_retries=1)
        phase = await await_phase(task_name, "Escalated", timeout=30)
        assert phase == "Escalated"

        # Register passing results for retry
        scenario_store.set_executor_result(task_name, _ok_executor("retry-sha"))
        scenario_store.set_gates_result(task_name, _ok_gates())

        import time

        await patch_taskspec_annotation(task_name, RETRY_ANNOTATION, str(int(time.time())))
        phase = await await_phase(task_name, "Done", timeout=60)

        assert phase == "Done"


class TestCancelRunningTask:
    """Scenario 12: task executing → cancel annotation → Cancelled."""

    async def test_cancel_stops_pipeline(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        # Don't register results — executor will be "running" when we cancel
        await create_taskspec(task_name, review_enabled=True)
        phase = await await_phase(task_name, "Executing", timeout=15)
        assert phase == "Executing"

        import time

        await patch_taskspec_annotation(task_name, CANCEL_ANNOTATION, str(int(time.time())))
        phase = await await_phase(task_name, "Cancelled", timeout=15)

        assert phase == "Cancelled"


class TestCancelTerminalTaskIgnored:
    """Scenario 13: completed task → cancel annotation → stays Done."""

    async def test_cancel_done_task_stays_done(
        self,
        task_name: str,
        scenario_store: ScenarioResultStore,
    ) -> None:
        scenario_store.set_executor_result(task_name, _ok_executor())
        scenario_store.set_gates_result(task_name, _ok_gates())

        await create_taskspec(task_name, review_enabled=False)
        phase = await await_phase(task_name, "Done", timeout=30)
        assert phase == "Done"

        import time

        await patch_taskspec_annotation(task_name, CANCEL_ANNOTATION, str(int(time.time())))

        # Wait briefly, verify phase hasn't changed
        import asyncio

        await asyncio.sleep(2)
        phase = await get_taskspec_status(task_name)
        assert phase.get("phase") == "Done"
