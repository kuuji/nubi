"""Scenario result store — serves pre-canned results for integration tests.

Replaces the GitHub API calls in results.py. The controller's handler functions
are patched to call this store instead of hitting GitHub.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from nubi.agents.gate_result import GatesResult
from nubi.agents.monitor_result import MonitorResult
from nubi.agents.result import ExecutorResult
from nubi.agents.review_result import ReviewResult
from nubi.exceptions import ResultError

logger = logging.getLogger(__name__)

_BRANCH_RE = re.compile(r"^nubi/(.+)$")


def _task_id_from_branch(branch: str) -> str:
    """Extract task ID from a branch name like 'nubi/my-task'."""
    match = _BRANCH_RE.match(branch)
    if not match:
        raise ResultError(f"Cannot extract task ID from branch: {branch}")
    return match.group(1)


class ScenarioResultStore:
    """Holds pre-canned results keyed by (task_id, attempt).

    For single-attempt scenarios, use attempt=1 (the default).
    For multi-attempt scenarios, register results for each attempt number.
    The store tracks how many times each task's results have been read
    to serve the correct attempt's results.
    """

    def __init__(self) -> None:
        self._executor: dict[tuple[str, int], ExecutorResult] = {}
        self._gates: dict[tuple[str, int], GatesResult] = {}
        self._reviews: dict[tuple[str, int], ReviewResult] = {}
        self._monitors: dict[tuple[str, int], MonitorResult] = {}
        self._call_counts: dict[tuple[str, str], int] = {}

    def set_executor_result(self, task_id: str, result: ExecutorResult, attempt: int = 1) -> None:
        self._executor[(task_id, attempt)] = result

    def set_gates_result(self, task_id: str, result: GatesResult, attempt: int = 1) -> None:
        self._gates[(task_id, attempt)] = result

    def set_review_result(self, task_id: str, result: ReviewResult, attempt: int = 1) -> None:
        self._reviews[(task_id, attempt)] = result

    def set_monitor_result(self, task_id: str, result: MonitorResult, attempt: int = 1) -> None:
        self._monitors[(task_id, attempt)] = result

    def _next_attempt(self, task_id: str, result_type: str) -> int:
        """Increment and return the call count for (task_id, result_type)."""
        key = (task_id, result_type)
        count = self._call_counts.get(key, 0) + 1
        self._call_counts[key] = count
        return count

    def _lookup(self, store: dict[tuple[str, int], Any], task_id: str, result_type: str) -> Any:
        attempt = self._next_attempt(task_id, result_type)
        result = store.get((task_id, attempt))
        if result is None:
            # Fall back to the highest registered attempt
            fallback = max(
                (a for (t, a) in store if t == task_id),
                default=None,
            )
            if fallback is not None:
                result = store[(task_id, fallback)]
        if result is None:
            raise ResultError(f"No {result_type} result for task {task_id} attempt {attempt}")
        logger.info("ScenarioStore: %s for %s attempt %d", result_type, task_id, attempt)
        return result

    async def read_executor_result(self, repo: str, branch: str, token: str) -> ExecutorResult:
        task_id = _task_id_from_branch(branch)
        return self._lookup(self._executor, task_id, "executor")

    async def read_gates_result(self, repo: str, branch: str, token: str) -> GatesResult:
        task_id = _task_id_from_branch(branch)
        return self._lookup(self._gates, task_id, "gates")

    async def read_review_result(self, repo: str, branch: str, token: str) -> ReviewResult:
        task_id = _task_id_from_branch(branch)
        return self._lookup(self._reviews, task_id, "review")

    async def read_monitor_result(self, repo: str, branch: str, token: str) -> MonitorResult:
        task_id = _task_id_from_branch(branch)
        return self._lookup(self._monitors, task_id, "monitor")

    def reset(self) -> None:
        """Clear all registered results and call counts."""
        self._executor.clear()
        self._gates.clear()
        self._reviews.clear()
        self._monitors.clear()
        self._call_counts.clear()
