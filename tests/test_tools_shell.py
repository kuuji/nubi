"""Tests for nubi.tools.shell — sandboxed shell execution."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from nubi.tools.shell import configure, run_shell


class TestRunShell:
    @patch("nubi.tools.shell.subprocess.run")
    def test_returns_stdout(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0)
        assert "hello" in run_shell(command="echo hello")

    @patch("nubi.tools.shell.subprocess.run")
    def test_includes_stderr(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.return_value = MagicMock(stdout="", stderr="warning\n", returncode=0)
        assert "warning" in run_shell(command="cmd")

    @patch("nubi.tools.shell.subprocess.run")
    def test_nonzero_exit_code(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.return_value = MagicMock(stdout="", stderr="err\n", returncode=1)
        assert "exit code: 1" in run_shell(command="false")

    @patch("nubi.tools.shell.subprocess.run")
    def test_truncates_long_output(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        long_output = "\n".join(f"line {i}" for i in range(500))
        mock_run.return_value = MagicMock(stdout=long_output, stderr="", returncode=0)
        result = run_shell(command="cmd")
        assert "truncated" in result
        assert "line 499" in result

    @patch("nubi.tools.shell.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 60)
        assert "timed out" in run_shell(command="sleep 999")

    @patch("nubi.tools.shell.subprocess.run")
    def test_uses_workspace_cwd(self, mock_run: MagicMock) -> None:
        configure("/my/workspace")
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        run_shell(command="ls")
        assert mock_run.call_args.kwargs.get("cwd") == "/my/workspace"
