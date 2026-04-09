"""Tests for nubi.tools.check — subagent-based diagnostic tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.tools.check import (
    CHECK_SYSTEM_PROMPT,
    configure,
    run_check,
    run_checks,
)


class TestRunCheck:
    @patch("nubi.tools.check._run_command", return_value=(0, "All good"))
    def test_pass_short_circuits(self, mock_cmd: MagicMock) -> None:
        """Passing command with small output skips subagent."""
        configure("/workspace")
        result = run_check(command="ruff check src/")
        assert "PASS" in result
        assert "All good" in result

    @patch("nubi.tools.check._analyze_with_subagent", return_value="**Status**: FAIL\n2 errors")
    @patch("nubi.tools.check._run_command", return_value=(1, "error on line 5\nerror on line 10"))
    def test_failure_spawns_subagent(self, mock_cmd: MagicMock, mock_analyze: MagicMock) -> None:
        """Failing command spawns subagent for analysis."""
        configure("/workspace")
        result = run_check(command="mypy src/")
        assert "FAIL" in result
        mock_analyze.assert_called_once()

    @patch(
        "nubi.tools.check._run_command",
        return_value=(0, "\n".join(f"line {i}" for i in range(50))),
    )
    @patch("nubi.tools.check._analyze_with_subagent", return_value="**Status**: PASS\nlarge output")
    def test_large_passing_output_spawns_subagent(
        self, mock_analyze: MagicMock, mock_cmd: MagicMock
    ) -> None:
        """Passing command with large output still spawns subagent."""
        configure("/workspace")
        run_check(command="pytest tests/ -v")
        mock_analyze.assert_called_once()

    @patch("nubi.tools.check._run_command", return_value=(1, "some error"))
    @patch(
        "nubi.tools.check._analyze_with_subagent",
        side_effect=RuntimeError("model crashed"),
    )
    def test_subagent_failure_falls_back_to_raw(
        self, mock_analyze: MagicMock, mock_cmd: MagicMock
    ) -> None:
        """If subagent fails, falls back to truncated raw output."""
        configure("/workspace")
        result = run_check(command="mypy src/")
        assert "FAIL" in result
        assert "some error" in result

    @patch("nubi.tools.check._run_command", return_value=(0, ""))
    def test_empty_output_passes(self, mock_cmd: MagicMock) -> None:
        configure("/workspace")
        result = run_check(command="ruff format --check src/")
        assert "PASS" in result


class TestRunChecks:
    @patch("nubi.tools.check._run_single_check")
    def test_runs_all_commands(self, mock_single: MagicMock) -> None:
        mock_single.return_value = "**Status**: PASS"
        configure("/workspace")

        result = run_checks(commands=["ruff check src/", "mypy src/", "pytest tests/"])

        assert mock_single.call_count == 3
        assert "ruff check src/" in result
        assert "mypy src/" in result
        assert "pytest tests/" in result

    @patch("nubi.tools.check._run_single_check")
    def test_returns_results_in_order(self, mock_single: MagicMock) -> None:
        def side_effect(cmd: str, timeout: int) -> str:
            return f"Result for {cmd}"

        mock_single.side_effect = side_effect
        configure("/workspace")

        result = run_checks(commands=["cmd_a", "cmd_b", "cmd_c"])

        # Results should appear in submission order
        pos_a = result.index("cmd_a")
        pos_b = result.index("cmd_b")
        pos_c = result.index("cmd_c")
        assert pos_a < pos_b < pos_c

    @patch("nubi.tools.check._run_single_check", side_effect=RuntimeError("boom"))
    def test_handles_individual_failures(self, mock_single: MagicMock) -> None:
        configure("/workspace")

        result = run_checks(commands=["bad_cmd"])
        assert "ERROR" in result
        assert "boom" in result


class TestRunCommand:
    def test_successful_command(self) -> None:
        from nubi.tools.check import _run_command

        configure("/tmp")
        code, output = _run_command("echo hello", timeout=10)
        assert code == 0
        assert "hello" in output

    def test_failing_command(self) -> None:
        from nubi.tools.check import _run_command

        configure("/tmp")
        code, output = _run_command("false", timeout=10)
        assert code != 0

    def test_timeout(self) -> None:
        from nubi.tools.check import _run_command

        configure("/tmp")
        code, output = _run_command("sleep 60", timeout=1)
        assert code == 1
        assert "timed out" in output


class TestAnalyzeWithSubagent:
    @patch("nubi.tools.check.Agent")
    @patch("nubi.tools.check._create_check_model")
    def test_passes_output_in_prompt(
        self, mock_model: MagicMock, mock_agent_cls: MagicMock
    ) -> None:
        from nubi.tools.check import _analyze_with_subagent

        mock_agent = MagicMock()
        mock_agent.return_value = "analysis result"
        mock_agent_cls.return_value = mock_agent

        result = _analyze_with_subagent("mypy src/", 1, "error: line 5")
        assert "analysis result" in result

        prompt = mock_agent.call_args[0][0]
        assert "mypy src/" in prompt
        assert "error: line 5" in prompt
        assert "Exit code" in prompt or "exit code" in prompt.lower()

    @patch("nubi.tools.check.Agent")
    @patch("nubi.tools.check._create_check_model")
    def test_subagent_has_file_read_only(
        self, mock_model: MagicMock, mock_agent_cls: MagicMock
    ) -> None:
        from nubi.tools.check import _analyze_with_subagent

        mock_agent = MagicMock()
        mock_agent.return_value = "done"
        mock_agent_cls.return_value = mock_agent

        _analyze_with_subagent("ruff check", 0, "clean")

        call_kwargs = mock_agent_cls.call_args.kwargs
        tool_names = {t.__name__ for t in call_kwargs["tools"]}
        assert "file_read" in tool_names
        assert "run_shell" not in tool_names


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
        import os

        from nubi.tools.check import _create_check_model

        with patch.dict(
            "os.environ",
            {"NUBI_LLM_PROVIDER": "anthropic", "LLM_API_KEY": "test"},
            clear=False,
        ):
            os.environ.pop("NUBI_CHECK_MODEL_ID", None)
            _create_check_model()
            mock_create.assert_called_once_with("anthropic", "test", model_id=None)


class TestConstants:
    def test_system_prompt_mentions_file_read(self) -> None:
        assert "file_read" in CHECK_SYSTEM_PROMPT
