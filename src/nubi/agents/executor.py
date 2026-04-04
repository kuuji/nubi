"""Executor agent — Strands agent that implements tasks on a git branch."""

from __future__ import annotations

import os
from typing import Any

from strands import Agent

EXECUTOR_SYSTEM_PROMPT = """\
You are Nubi Executor, an autonomous coding agent running inside a sandboxed Kubernetes pod.

## Your Task
{description}

## Context
- Repository: {repo}
- Branch: {branch}
- Working directory: /workspace (the cloned repo)

## Constraints
- You MUST complete your work by making git commits and pushing to the branch.
- You run as an unprivileged user in a read-only root filesystem.
- /workspace is your only writable directory.
- You have a limited time budget. Work efficiently.
- Do NOT attempt to access the Kubernetes API or any external services not related to your task.

## Workflow
1. Understand the codebase: read relevant files, check existing patterns.
2. Plan your approach before writing code.
3. Implement the changes.
4. Verify your work: run tests if a test suite exists, check for syntax errors.
5. Commit your changes with a clear, descriptive commit message.
6. Push to the branch.

## Quality Standards
- Follow existing code conventions and patterns in the repository.
- Write clean, well-tested code.
- If tests exist, make sure they pass after your changes.
- Do not introduce security vulnerabilities or hardcoded secrets.

When you are finished, state what you did and list the files you changed.\
"""


def create_model(provider: str, api_key: str) -> Any:
    """Create a Strands model instance for the given provider.

    Args:
        provider: One of "anthropic", "bedrock", "openai".
        api_key: API key for the provider (not used for bedrock).
    """
    if provider == "anthropic":
        from strands.models.anthropic import AnthropicModel

        return AnthropicModel(
            model_id=os.environ.get("NUBI_MODEL_ID", "claude-sonnet-4-20250514"),
            max_tokens=16384,
            client_args={"api_key": api_key},
        )
    elif provider == "bedrock":
        from strands.models.bedrock import BedrockModel

        return BedrockModel(
            model_id=os.environ.get("NUBI_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"),
            max_tokens=16384,
        )
    elif provider == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            model_id=os.environ.get("NUBI_MODEL_ID", "gpt-4o"),
            client_args={"api_key": api_key},
        )
    else:
        raise ValueError(
            f"Unknown LLM provider: {provider!r}. Use 'anthropic', 'bedrock', or 'openai'."
        )


def create_executor_agent(
    tools: list[Any],
    description: str,
    repo: str,
    branch: str,
    provider: str = "anthropic",
    api_key: str = "",
) -> Agent:
    """Create a configured Strands Agent for executor work.

    Args:
        tools: List of @tool decorated functions available to the agent.
        description: Task description from the TaskSpec.
        repo: GitHub repository (owner/repo).
        branch: Git branch to work on.
        provider: LLM provider name.
        api_key: API key for the LLM provider.
    """
    model = create_model(provider, api_key)
    system_prompt = EXECUTOR_SYSTEM_PROMPT.format(
        description=description,
        repo=repo,
        branch=branch,
    )
    return Agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        callback_handler=None,
    )
