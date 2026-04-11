"""Tests for nubi.entrypoint — agent container entrypoint."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

ENV_VARS = {
    "NUBI_TASK_ID": "task-1",
    "NUBI_REPO": "kuuji/app",
    "NUBI_BRANCH": "main",
    "NUBI_DESCRIPTION": "fix the bug",
    "NUBI_TOOLS": "shell,git,file_read",
    "NUBI_LLM_PROVIDER": "anthropic",
    "GITHUB_TOKEN": "tok-123",
    "LLM_API_KEY": "sk-test",
    "NUBI_WORKSPACE": "/tmp/test-workspace",
}


class TestMain:
    @patch("nubi.entrypoint.subprocess.run")
    @patch("nubi.entrypoint.create_executor_agent")
    @patch("nubi.entrypoint.get_tools", return_value=[])
    @patch("nubi.entrypoint.git_clone")
    @patch("nubi.entrypoint.write_result")
    @patch.dict("os.environ", ENV_VARS)
    def test_returns_zero_on_success(
        self,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        os.makedirs("/tmp/test-workspace/.git/info", exist_ok=True)
        mock_agent = MagicMock()
        mock_agent.return_value = "Done"
        mock_agent_factory.return_value = mock_agent
        mock_subprocess.return_value = MagicMock(stdout="abc123\n", stderr="", returncode=0)

        from nubi.entrypoint import main

        assert main() == 0

    @patch("nubi.entrypoint.subprocess.run")
    @patch("nubi.entrypoint.create_executor_agent")
    @patch("nubi.entrypoint.get_tools", return_value=[])
    @patch("nubi.entrypoint.git_clone")
    @patch("nubi.entrypoint.write_result")
    @patch.dict("os.environ", ENV_VARS)
    def test_calls_git_clone(
        self,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_agent = MagicMock()
        mock_agent.return_value = "Done"
        mock_agent_factory.return_value = mock_agent
        mock_subprocess.return_value = MagicMock(stdout="abc\n", stderr="", returncode=0)

        from nubi.entrypoint import main

        main()
        mock_clone.assert_called_once_with("kuuji/app", "main", "tok-123", "/tmp/test-workspace")

    @patch("nubi.entrypoint.subprocess.run")
    @patch("nubi.entrypoint.create_executor_agent")
    @patch("nubi.entrypoint.get_tools", return_value=[])
    @patch("nubi.entrypoint.git_clone", side_effect=RuntimeError("clone failed"))
    @patch("nubi.entrypoint.write_result")
    @patch.dict("os.environ", ENV_VARS)
    def test_returns_one_on_failure(
        self,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_subprocess.return_value = MagicMock(returncode=0)

        from nubi.entrypoint import main

        assert main() == 1

    @patch("nubi.entrypoint.subprocess.run")
    @patch("nubi.entrypoint.create_executor_agent")
    @patch("nubi.entrypoint.get_tools", return_value=[])
    @patch("nubi.entrypoint.git_clone")
    @patch("nubi.entrypoint.write_result")
    @patch.dict("os.environ", {**ENV_VARS, "NUBI_WORKSPACE": "/tmp/test-workspace-exclude"})
    def test_workspace_excludes_not_duplicated_on_rerun(
        self,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        workspace = "/tmp/test-workspace-exclude"
        exclude_path = os.path.join(workspace, ".git", "info", "exclude")
        os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
        # Start with an empty exclude file (as `git init` would produce).
        with open(exclude_path, "w") as f:
            f.write("")

        mock_agent = MagicMock()
        mock_agent.return_value = "Done"
        mock_agent_factory.return_value = mock_agent
        mock_subprocess.return_value = MagicMock(stdout="abc\n", stderr="", returncode=0)

        from nubi.entrypoint import main

        # First run writes the block.
        main()
        with open(exclude_path) as f:
            first = f.read()
        assert first.count("# nubi workspace excludes") == 1
        assert first.count(".nubi/") == 1

        # Second run must be a no-op on the exclude file.
        main()
        with open(exclude_path) as f:
            second = f.read()
        assert second == first

    @patch("nubi.entrypoint.subprocess.run")
    @patch("nubi.entrypoint.create_executor_agent")
    @patch("nubi.entrypoint.get_tools", return_value=[])
    @patch("nubi.entrypoint.git_clone")
    @patch("nubi.entrypoint.write_result")
    @patch.dict("os.environ", ENV_VARS)
    def test_writes_success_result(
        self,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        os.makedirs("/tmp/test-workspace/.git/info", exist_ok=True)
        mock_agent = MagicMock()
        mock_agent.return_value = "summary text"
        mock_agent_factory.return_value = mock_agent
        mock_subprocess.return_value = MagicMock(stdout="sha123\n", stderr="", returncode=0)

        from nubi.entrypoint import main

        main()
        mock_write.assert_called_once()
        result = mock_write.call_args[0][0]
        assert result.status == "success"
