"""Tests for nubi.agents.result — ExecutorResult model and write helper."""

from __future__ import annotations

import json
from pathlib import Path

from nubi.agents.result import ExecutorResult, result_file_path, write_result

TASK_ID = "test-task-1"


class TestExecutorResult:
    def test_success_result(self) -> None:
        r = ExecutorResult(status="success", commit_sha="abc123", summary="did stuff")
        assert r.status == "success"
        assert r.commit_sha == "abc123"

    def test_failure_result(self) -> None:
        r = ExecutorResult(status="failure", error="boom")
        assert r.status == "failure"
        assert r.error == "boom"

    def test_defaults(self) -> None:
        r = ExecutorResult(status="success")
        assert r.commit_sha == ""
        assert r.summary == ""
        assert r.files_changed == []
        assert r.error == ""

    def test_round_trip_json(self) -> None:
        r = ExecutorResult(status="success", commit_sha="abc", files_changed=["a.py", "b.py"])
        data = json.loads(r.model_dump_json())
        r2 = ExecutorResult.model_validate(data)
        assert r == r2

    def test_result_file_path_function(self) -> None:
        assert result_file_path("my-task") == ".nubi/my-task/result.json"


class TestWriteResult:
    def test_writes_json_file(self, tmp_path: Path) -> None:
        r = ExecutorResult(status="success", commit_sha="abc")
        write_result(r, str(tmp_path), TASK_ID)
        result_path = tmp_path / ".nubi" / TASK_ID / "result.json"
        assert result_path.exists()
        data = json.loads(result_path.read_text())
        assert data["status"] == "success"
        assert data["commit_sha"] == "abc"

    def test_creates_nubi_dir(self, tmp_path: Path) -> None:
        r = ExecutorResult(status="failure")
        write_result(r, str(tmp_path), TASK_ID)
        assert (tmp_path / ".nubi" / TASK_ID).is_dir()

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        r1 = ExecutorResult(status="failure", error="first")
        write_result(r1, str(tmp_path), TASK_ID)
        r2 = ExecutorResult(status="success", summary="second")
        write_result(r2, str(tmp_path), TASK_ID)
        data = json.loads((tmp_path / ".nubi" / TASK_ID / "result.json").read_text())
        assert data["status"] == "success"
