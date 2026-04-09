"""Tests for nubi.tools.check — subagent-based diagnostic tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.tools.check import CHECK_SYSTEM_PROMPT, configure, run_check


class TestRunCheck:
    @patch("nubi.tools.check.Agent")
    @patch("nubi.tools.check._create_check_model")
    def test_returns_subagent_response(
        self, mock_model: MagicMock, mock_agent_cls: MagicMock
    ) -> None:
        configure("/workspace")
        mock_agent = MagicMock()
        mock_agent.return_value = "**Status**: PASS\n**Total issues**: 0"
        mock_agent_cls.return_value = mock_agent

        result = run_check(command="mypy src/")
        assert "PASS" in result
        mock_agent.assert_called_once()

    @patch("nubi.tools.check.Agent")
    @patch("nubi.tools.check._create_check_model")
    def test_subagent_gets_readonly_tools(
        self, mock_model: MagicMock, mock_agent_cls: MagicMock
    ) -> None:
        configure("/workspace")
        mock_agent = MagicMock()
        mock_agent.return_value = "done"
        mock_agent_cls.return_value = mock_agent

        run_check(command="ruff check src/")

        call_kwargs = mock_agent_cls.call_args.kwargs
        tool_names = {t.__name__ for t in call_kwargs["tools"]}
        assert "run_shell" in tool_names
        assert "file_read" in tool_names
        assert "file_write" not in tool_names
        assert "git_commit" not in tool_names

    @patch("nubi.tools.check.Agent")
    @patch("nubi.tools.check._create_check_model")
    def test_subagent_gets_system_prompt(
        self, mock_model: MagicMock, mock_agent_cls: MagicMock
    ) -> None:
        configure("/workspace")
        mock_agent = MagicMock()
        mock_agent.return_value = "done"
        mock_agent_cls.return_value = mock_agent

        run_check(command="pytest tests/")

        call_kwargs = mock_agent_cls.call_args.kwargs
        assert call_kwargs["system_prompt"] == CHECK_SYSTEM_PROMPT

    @patch("nubi.tools.check.Agent")
    @patch("nubi.tools.check._create_check_model")
    def test_command_in_prompt(self, mock_model: MagicMock, mock_agent_cls: MagicMock) -> None:
        configure("/workspace")
        mock_agent = MagicMock()
        mock_agent.return_value = "done"
        mock_agent_cls.return_value = mock_agent

        run_check(command="mypy --strict src/nubi/")

        prompt_arg = mock_agent.call_args[0][0]
        assert "mypy --strict src/nubi/" in prompt_arg

    @patch("nubi.tools.check.Agent")
    @patch("nubi.tools.check._create_check_model")
    def test_timeout_in_prompt(self, mock_model: MagicMock, mock_agent_cls: MagicMock) -> None:
        configure("/workspace")
        mock_agent = MagicMock()
        mock_agent.return_value = "done"
        mock_agent_cls.return_value = mock_agent

        run_check(command="pytest tests/", timeout=300)

        prompt_arg = mock_agent.call_args[0][0]
        assert "timeout=300" in prompt_arg

    @patch("nubi.tools.check._create_check_model", side_effect=RuntimeError("model failed"))
    def test_exception_returns_error_string(self, mock_model: MagicMock) -> None:
        configure("/workspace")

        result = run_check(command="mypy src/")
        assert "run_check failed" in result
        assert "model failed" in result

    @patch("nubi.tools.check.Agent")
    @patch("nubi.tools.check._create_check_model")
    def test_workspace_in_prompt(self, mock_model: MagicMock, mock_agent_cls: MagicMock) -> None:
        configure("/custom/workspace")
        mock_agent = MagicMock()
        mock_agent.return_value = "done"
        mock_agent_cls.return_value = mock_agent

        run_check(command="ruff check .")

        prompt_arg = mock_agent.call_args[0][0]
        assert "/custom/workspace" in prompt_arg


class TestCreateCheckModel:
    @patch("nubi.agents.executor.create_model")
    def test_uses_check_model_id(self, mock_create: MagicMock) -> None:
        from nubi.tools.check import _create_check_model

        with patch.dict(
            "os.environ",
            {
                "NUBI_LLM_PROVIDER": "openai",
                "LLM_API_KEY": "test",
                "NUBI_CHECK_MODEL_ID": "cheap-model",
            },
        ):
            _create_check_model()
            mock_create.assert_called_once_with("openai", "test", model_id="cheap-model")

    @patch("nubi.agents.executor.create_model")
    def test_falls_back_to_default(self, mock_create: MagicMock) -> None:
        from nubi.tools.check import _create_check_model

        with patch.dict(
            "os.environ",
            {"NUBI_LLM_PROVIDER": "anthropic", "LLM_API_KEY": "test"},
            clear=False,
        ):
            import os

            os.environ.pop("NUBI_CHECK_MODEL_ID", None)
            _create_check_model()
            mock_create.assert_called_once_with("anthropic", "test", model_id=None)


class TestConstants:
    def test_system_prompt_mentions_run_shell(self) -> None:
        assert "run_shell" in CHECK_SYSTEM_PROMPT

    def test_system_prompt_mentions_file_read(self) -> None:
        assert "file_read" in CHECK_SYSTEM_PROMPT
