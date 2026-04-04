"""Tests for nubi.controller.handlers — kopf event handlers."""

import logging

import pytest
from pydantic import ValidationError

from nubi.controller.handlers import on_job_status_change, on_taskspec_created

VALID_SPEC: dict = {
    "description": "Implement feature X",
    "type": "code-change",
    "inputs": {"repo": "kuuji/test"},
}

INVALID_SPEC: dict = {
    "description": "Bad task",
    "type": "banana",
    "inputs": {"repo": "kuuji/test"},
}


class FakePatch:
    """Mimics kopf.Patch — a dict-like object for status updates."""

    def __init__(self):
        self.status: dict = {}


# -- on_taskspec_created — valid spec -----------------------------------------


class TestOnTaskSpecCreated:
    @pytest.mark.asyncio
    async def test_sets_phase_to_pending(self):
        patch = FakePatch()
        await on_taskspec_created(
            spec=VALID_SPEC,
            patch=patch,
            name="test-task-1",
            namespace="nubi-system",
        )
        assert patch.status.get("phase") == "Pending"

    @pytest.mark.asyncio
    async def test_returns_dict_with_message(self):
        patch = FakePatch()
        result = await on_taskspec_created(
            spec=VALID_SPEC,
            patch=patch,
            name="test-task-1",
            namespace="nubi-system",
        )
        assert isinstance(result, dict)
        assert "message" in result


# -- on_taskspec_created — invalid spec ---------------------------------------


class TestOnTaskSpecCreatedInvalid:
    @pytest.mark.asyncio
    async def test_invalid_spec_raises(self):
        patch = FakePatch()
        with pytest.raises((ValidationError, ValueError)):
            await on_taskspec_created(
                spec=INVALID_SPEC,
                patch=patch,
                name="bad-task",
                namespace="nubi-system",
            )


# -- on_job_status_change — logs task-id and stage ----------------------------


class TestOnJobStatusChange:
    @pytest.mark.asyncio
    async def test_logs_task_id_and_stage(self, caplog):
        labels = {
            "nubi.io/task-id": "task-abc-123",
            "nubi.io/stage": "executor",
        }
        with caplog.at_level(logging.INFO):
            await on_job_status_change(
                labels=labels,
                name="job-xyz",
                namespace="nubi-task-abc",
                status={},
            )
        combined = caplog.text
        assert "task-abc-123" in combined
        assert "executor" in combined
