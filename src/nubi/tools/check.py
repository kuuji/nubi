"""Subagent-based check tool — runs diagnostics in a separate context."""

from __future__ import annotations

import logging
import os
from typing import Any

from strands import Agent, tool

from nubi.agents.logging_handler import LoggingCallbackHandler

logger = logging.getLogger(__name__)

_workspace: str = "/workspace"

CHECK_SYSTEM_PROMPT = """\
You are a diagnostic analysis agent. Your job is to:

1. Run the requested diagnostic command using run_shell
2. Read the FULL output carefully
3. If the output references specific files, you may use file_read to understand the code context
4. Produce a structured summary with:
   - **Status**: PASS or FAIL
   - **Total issues**: count of distinct errors/warnings
   - **Issues** (list all, up to 20): each with file path, line number, and description
   - **Recommendation**: one-sentence fix guidance if there are failures

Keep your response concise. The parent agent will act on your findings.
Do NOT suggest code changes — only report what the tool found.\
"""


def configure(workspace: str) -> None:
    """Set the workspace root for check tool."""
    global _workspace
    _workspace = workspace


def _create_check_model() -> Any:
    """Create a model for the check subagent.

    Uses NUBI_CHECK_MODEL_ID if set, otherwise falls back to the default model.
    """
    from nubi.agents.executor import create_model

    provider = os.environ.get("NUBI_LLM_PROVIDER", "anthropic")
    api_key = os.environ.get("LLM_API_KEY", "")
    check_model_id = os.environ.get("NUBI_CHECK_MODEL_ID")

    return create_model(provider, api_key, model_id=check_model_id)


@tool
def run_check(command: str, timeout: int = 120) -> str:
    """Run a diagnostic command via a subagent that analyzes the output.

    Use this for commands that produce large output (mypy, pytest, ruff, etc.).
    The subagent runs the command, reads the full output in its own context,
    and returns a structured summary of findings.

    Args:
        command: The diagnostic command to run (e.g. "mypy src/nubi/").
        timeout: Maximum seconds for the underlying command. Defaults to 120.
    """
    from nubi.tools.files import file_read
    from nubi.tools.shell import run_shell

    logger.info("run_check: spawning subagent for: %s", command)

    try:
        model = _create_check_model()

        subagent = Agent(
            model=model,
            tools=[run_shell, file_read],
            system_prompt=CHECK_SYSTEM_PROMPT,
            callback_handler=LoggingCallbackHandler(),
        )

        prompt = (
            f"Run this diagnostic command and analyze the results:\n\n"
            f"```\n{command}\n```\n\n"
            f"Working directory is {_workspace}. "
            f"Use run_shell with timeout={timeout} to execute it. "
            f"Then provide your structured analysis."
        )

        result = subagent(prompt)
        response_text = str(result)
        logger.info("run_check: subagent completed, response length=%d", len(response_text))
        return response_text

    except Exception as exc:
        error_msg = f"run_check failed for '{command}': {exc}"
        logger.error(error_msg)
        return error_msg
