"""Tests for nubi.agents.executor — agent creation and model factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nubi.agents.executor import (
    EXECUTOR_SYSTEM_PROMPT,
    create_executor_agent,
    create_model,
)


class TestCreateModel:
    @patch("strands.models.anthropic.AnthropicModel")
    def test_anthropic(self, mock_cls: MagicMock) -> None:
        create_model("anthropic", "sk-test")
        mock_cls.assert_called_once()

    @patch("strands.models.bedrock.BedrockModel")
    def test_bedrock(self, mock_cls: MagicMock) -> None:
        create_model("bedrock", "")
        mock_cls.assert_called_once()

    @patch("strands.models.openai.OpenAIModel")
    def test_openai(self, mock_cls: MagicMock) -> None:
        create_model("openai", "sk-test")
        mock_cls.assert_called_once()

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_model("unknown", "key")


class TestCreateExecutorAgent:
    @patch("nubi.agents.executor.Agent")
    @patch("nubi.agents.executor.create_model")
    def test_creates_agent(self, mock_model: MagicMock, mock_agent: MagicMock) -> None:
        create_executor_agent(
            tools=[MagicMock()],
            description="fix bug",
            repo="kuuji/app",
            base_branch="main",
            task_branch="nubi/task-1",
            provider="anthropic",
            api_key="key",
        )
        mock_agent.assert_called_once()

    @patch("nubi.agents.executor.Agent")
    @patch("nubi.agents.executor.create_model")
    def test_system_prompt_contains_description(
        self, mock_model: MagicMock, mock_agent: MagicMock
    ) -> None:
        create_executor_agent(
            tools=[],
            description="add rate limiting",
            repo="kuuji/app",
            base_branch="main",
            task_branch="nubi/task-1",
        )
        call_kwargs = mock_agent.call_args.kwargs
        assert "add rate limiting" in call_kwargs["system_prompt"]

    @patch("nubi.agents.executor.Agent")
    @patch("nubi.agents.executor.create_model")
    def test_system_prompt_contains_repo(
        self, mock_model: MagicMock, mock_agent: MagicMock
    ) -> None:
        create_executor_agent(
            tools=[],
            description="task",
            repo="kuuji/my-app",
            base_branch="main",
            task_branch="nubi/task-1",
        )
        call_kwargs = mock_agent.call_args.kwargs
        assert "kuuji/my-app" in call_kwargs["system_prompt"]

    @patch("nubi.agents.executor.Agent")
    @patch("nubi.agents.executor.create_model")
    def test_callback_handler_is_none(self, mock_model: MagicMock, mock_agent: MagicMock) -> None:
        create_executor_agent(tools=[], description="t", repo="r", base_branch="b", task_branch="t")
        call_kwargs = mock_agent.call_args.kwargs
        assert call_kwargs["callback_handler"] is None


class TestSystemPrompt:
    def test_has_placeholders(self) -> None:
        assert "{description}" in EXECUTOR_SYSTEM_PROMPT
        assert "{repo}" in EXECUTOR_SYSTEM_PROMPT
        assert "{base_branch}" in EXECUTOR_SYSTEM_PROMPT
        assert "{task_branch}" in EXECUTOR_SYSTEM_PROMPT
        assert "{max_attempts}" in EXECUTOR_SYSTEM_PROMPT
        assert "{max_cc}" in EXECUTOR_SYSTEM_PROMPT

    def test_format_works(self) -> None:
        result = EXECUTOR_SYSTEM_PROMPT.format(
            description="test",
            repo="owner/repo",
            base_branch="main",
            task_branch="nubi/t1",
            max_attempts=3,
            max_cc=10,
        )
        assert "test" in result
        assert "owner/repo" in result
