"""Tests for nubi.agents.reviewer — reviewer agent creation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.agents.reviewer import (
    REVIEWER_SYSTEM_PROMPT,
    create_reviewer_agent,
)


class TestCreateReviewerAgent:
    @patch("nubi.agents.reviewer.Agent")
    @patch("nubi.agents.reviewer.create_model")
    def test_creates_agent(self, mock_model: MagicMock, mock_agent: MagicMock) -> None:
        create_reviewer_agent(
            tools=[MagicMock()],
            description="fix bug",
            repo="kuuji/app",
            base_branch="main",
            task_branch="nubi/task-1",
            review_focus=[],
            provider="anthropic",
            api_key="key",
        )
        mock_agent.assert_called_once()

    @patch("nubi.agents.reviewer.Agent")
    @patch("nubi.agents.reviewer.create_model")
    def test_system_prompt_contains_description(
        self, mock_model: MagicMock, mock_agent: MagicMock
    ) -> None:
        create_reviewer_agent(
            tools=[],
            description="add rate limiting",
            repo="kuuji/app",
            base_branch="main",
            task_branch="nubi/task-1",
            review_focus=[],
        )
        call_kwargs = mock_agent.call_args.kwargs
        assert "add rate limiting" in call_kwargs["system_prompt"]

    @patch("nubi.agents.reviewer.Agent")
    @patch("nubi.agents.reviewer.create_model")
    def test_system_prompt_contains_repo(
        self, mock_model: MagicMock, mock_agent: MagicMock
    ) -> None:
        create_reviewer_agent(
            tools=[],
            description="task",
            repo="kuuji/my-app",
            base_branch="main",
            task_branch="nubi/task-1",
            review_focus=[],
        )
        call_kwargs = mock_agent.call_args.kwargs
        assert "kuuji/my-app" in call_kwargs["system_prompt"]

    @patch("nubi.agents.reviewer.Agent")
    @patch("nubi.agents.reviewer.create_model")
    def test_system_prompt_contains_branches(
        self, mock_model: MagicMock, mock_agent: MagicMock
    ) -> None:
        create_reviewer_agent(
            tools=[],
            description="task",
            repo="kuuji/app",
            base_branch="develop",
            task_branch="nubi/task-99",
            review_focus=[],
        )
        call_kwargs = mock_agent.call_args.kwargs
        assert "develop" in call_kwargs["system_prompt"]
        assert "nubi/task-99" in call_kwargs["system_prompt"]

    @patch("nubi.agents.reviewer.Agent")
    @patch("nubi.agents.reviewer.create_model")
    def test_system_prompt_contains_review_focus(
        self, mock_model: MagicMock, mock_agent: MagicMock
    ) -> None:
        create_reviewer_agent(
            tools=[],
            description="task",
            repo="kuuji/app",
            base_branch="main",
            task_branch="nubi/task-1",
            review_focus=["security", "performance"],
        )
        call_kwargs = mock_agent.call_args.kwargs
        assert "security" in call_kwargs["system_prompt"]
        assert "performance" in call_kwargs["system_prompt"]

    @patch("nubi.agents.reviewer.Agent")
    @patch("nubi.agents.reviewer.create_model")
    def test_empty_review_focus(self, mock_model: MagicMock, mock_agent: MagicMock) -> None:
        create_reviewer_agent(
            tools=[],
            description="task",
            repo="kuuji/app",
            base_branch="main",
            task_branch="nubi/task-1",
            review_focus=[],
        )
        call_kwargs = mock_agent.call_args.kwargs
        assert "No specific focus areas" in call_kwargs["system_prompt"]

    @patch("nubi.agents.reviewer.Agent")
    @patch("nubi.agents.reviewer.create_model")
    def test_callback_handler_is_logging(
        self, mock_model: MagicMock, mock_agent: MagicMock
    ) -> None:
        from nubi.agents.logging_handler import LoggingCallbackHandler

        create_reviewer_agent(
            tools=[],
            description="t",
            repo="r",
            base_branch="b",
            task_branch="t",
            review_focus=[],
        )
        call_kwargs = mock_agent.call_args.kwargs
        assert isinstance(call_kwargs["callback_handler"], LoggingCallbackHandler)


class TestReviewerSystemPrompt:
    def test_has_placeholders(self) -> None:
        assert "{description}" in REVIEWER_SYSTEM_PROMPT
        assert "{repo}" in REVIEWER_SYSTEM_PROMPT
        assert "{base_branch}" in REVIEWER_SYSTEM_PROMPT
        assert "{task_branch}" in REVIEWER_SYSTEM_PROMPT
        assert "{review_focus}" in REVIEWER_SYSTEM_PROMPT

    def test_format_works(self) -> None:
        result = REVIEWER_SYSTEM_PROMPT.format(
            description="fix the auth bug",
            repo="owner/repo",
            base_branch="main",
            task_branch="nubi/t1",
            review_focus="- security\n- correctness",
        )
        assert "fix the auth bug" in result
        assert "owner/repo" in result
        assert "submit_review" in result
