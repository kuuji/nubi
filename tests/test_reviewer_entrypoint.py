"""Tests for nubi.reviewer_entrypoint — reviewer container entrypoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.agents.review_result import ReviewDecision, ReviewResult

ENV_VARS = {
    "NUBI_TASK_ID": "task-1",
    "NUBI_REPO": "kuuji/app",
    "NUBI_BRANCH": "main",
    "NUBI_DESCRIPTION": "fix the bug",
    "NUBI_TOOLS": "shell,git_read,file_read,file_list,review",
    "NUBI_REVIEW_FOCUS": "security,performance",
    "NUBI_LLM_PROVIDER": "anthropic",
    "GITHUB_TOKEN": "tok-123",
    "LLM_API_KEY": "sk-test",
    "NUBI_WORKSPACE": "/tmp/test-workspace",
}


class TestMain:
    @patch("nubi.reviewer_entrypoint.subprocess.run")
    @patch("nubi.reviewer_entrypoint.create_reviewer_agent")
    @patch("nubi.reviewer_entrypoint.get_tools", return_value=[])
    @patch("nubi.reviewer_entrypoint.git_clone")
    @patch("nubi.reviewer_entrypoint.write_review_result")
    @patch(
        "nubi.reviewer_entrypoint.get_review_result",
        return_value=ReviewResult(
            decision=ReviewDecision.APPROVE,
            feedback="LGTM",
            summary="Approved",
        ),
    )
    @patch.dict("os.environ", ENV_VARS)
    def test_returns_zero_on_success(
        self,
        mock_get_review: MagicMock,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_agent = MagicMock()
        mock_agent.return_value = "Review done"
        mock_agent_factory.return_value = mock_agent
        mock_subprocess.return_value = MagicMock(stdout="", stderr="", returncode=0)

        from nubi.reviewer_entrypoint import main

        assert main() == 0

    @patch("nubi.reviewer_entrypoint.subprocess.run")
    @patch("nubi.reviewer_entrypoint.create_reviewer_agent")
    @patch("nubi.reviewer_entrypoint.get_tools", return_value=[])
    @patch("nubi.reviewer_entrypoint.git_clone")
    @patch("nubi.reviewer_entrypoint.write_review_result")
    @patch(
        "nubi.reviewer_entrypoint.get_review_result",
        return_value=ReviewResult(
            decision=ReviewDecision.APPROVE,
            feedback="ok",
            summary="ok",
        ),
    )
    @patch.dict("os.environ", ENV_VARS)
    def test_calls_git_clone(
        self,
        mock_get_review: MagicMock,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_agent = MagicMock()
        mock_agent.return_value = "Done"
        mock_agent_factory.return_value = mock_agent
        mock_subprocess.return_value = MagicMock(stdout="", stderr="", returncode=0)

        from nubi.reviewer_entrypoint import main

        main()
        mock_clone.assert_called_once_with("kuuji/app", "main", "tok-123", "/tmp/test-workspace")

    @patch("nubi.reviewer_entrypoint.subprocess.run")
    @patch("nubi.reviewer_entrypoint.create_reviewer_agent")
    @patch("nubi.reviewer_entrypoint.get_tools", return_value=[])
    @patch("nubi.reviewer_entrypoint.git_clone", side_effect=RuntimeError("clone failed"))
    @patch("nubi.reviewer_entrypoint.write_review_result")
    @patch.dict("os.environ", ENV_VARS)
    def test_returns_one_on_failure(
        self,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        from nubi.reviewer_entrypoint import main

        assert main() == 1

    @patch("nubi.reviewer_entrypoint.subprocess.run")
    @patch("nubi.reviewer_entrypoint.create_reviewer_agent")
    @patch("nubi.reviewer_entrypoint.get_tools", return_value=[])
    @patch("nubi.reviewer_entrypoint.git_clone")
    @patch("nubi.reviewer_entrypoint.write_review_result")
    @patch("nubi.reviewer_entrypoint.get_review_result", return_value=None)
    @patch.dict("os.environ", ENV_VARS)
    def test_defaults_to_reject_when_no_review_submitted(
        self,
        mock_get_review: MagicMock,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_agent = MagicMock()
        mock_agent.return_value = "I looked at the code"
        mock_agent_factory.return_value = mock_agent
        mock_subprocess.return_value = MagicMock(stdout="", stderr="", returncode=0)

        from nubi.reviewer_entrypoint import main

        main()
        mock_write.assert_called_once()
        result = mock_write.call_args[0][0]
        assert result.decision == ReviewDecision.REJECT

    @patch("nubi.reviewer_entrypoint.subprocess.run")
    @patch("nubi.reviewer_entrypoint.create_reviewer_agent")
    @patch("nubi.reviewer_entrypoint.get_tools", return_value=[])
    @patch("nubi.reviewer_entrypoint.git_clone")
    @patch("nubi.reviewer_entrypoint.write_review_result")
    @patch(
        "nubi.reviewer_entrypoint.get_review_result",
        return_value=ReviewResult(
            decision=ReviewDecision.REQUEST_CHANGES,
            feedback="Fix error handling",
            summary="Needs changes",
        ),
    )
    @patch.dict("os.environ", ENV_VARS)
    def test_passes_review_focus(
        self,
        mock_get_review: MagicMock,
        mock_write: MagicMock,
        mock_clone: MagicMock,
        mock_tools: MagicMock,
        mock_agent_factory: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        mock_agent = MagicMock()
        mock_agent.return_value = "Done"
        mock_agent_factory.return_value = mock_agent
        mock_subprocess.return_value = MagicMock(stdout="", stderr="", returncode=0)

        from nubi.reviewer_entrypoint import main

        main()
        call_kwargs = mock_agent_factory.call_args.kwargs
        assert call_kwargs["review_focus"] == ["security", "performance"]
