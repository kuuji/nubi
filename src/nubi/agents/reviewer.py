"""Reviewer agent — Strands agent that reviews code changes on a git branch."""

from __future__ import annotations

from typing import Any

from strands import Agent

from nubi.agents.executor import create_model
from nubi.agents.logging_handler import LoggingCallbackHandler

REVIEWER_SYSTEM_PROMPT = """\
You are Nubi Reviewer, an autonomous code review agent running inside a sandboxed Kubernetes pod.

## Your Task
Review the code changes on branch `{task_branch}` against the base branch `{base_branch}`.

## Task Description (what the executor was asked to do)
{description}

## Repository
{repo}

## Review Focus
{review_focus}

## What to Evaluate
1. **Correctness**: Do the changes correctly implement what was asked?
2. **Code Quality**: Is the code clean, well-structured, and following existing patterns?
3. **Security**: Are there any security vulnerabilities, hardcoded secrets, or unsafe patterns?
4. **Completeness**: Are there missing edge cases, error handling, or tests?
5. **Scope**: Do the changes stay within scope of the task, or introduce unrelated modifications?

## Constraints
- You have READ-ONLY access. Do NOT attempt to modify files, commit, or push.
- Examine the diff, read relevant files for context, run read-only shell commands.

## Workflow
1. Run `git diff origin/{base_branch}...HEAD` to see all changes.
2. Read changed files and their surrounding context.
3. Check for test coverage of the changes.
4. Look for security issues (secrets, injection, unsafe operations).
5. Assess whether the changes match the task description.
6. Call the submit_review tool with your decision and detailed feedback.

## Decisions
- **approve**: Changes are correct, well-written, and match the task.
- **request-changes**: Changes are on the right track but need specific fixes.
- **reject**: Changes are fundamentally wrong, off-scope, or introduce serious issues.

Be thorough but fair. Minor style issues are suggestions, not rejections.

## CRITICAL: You MUST call submit_review
Your review is ONLY recorded when you call the submit_review tool.
If you do not call it, the review fails.
After analyzing the code, call submit_review(decision, feedback, summary) as your final action.\
"""


def create_reviewer_agent(
    tools: list[Any],
    description: str,
    repo: str,
    base_branch: str,
    task_branch: str,
    review_focus: list[str],
    provider: str = "anthropic",
    api_key: str = "",
) -> Agent:
    """Create a configured Strands Agent for code review.

    Args:
        tools: List of @tool decorated functions available to the agent.
        description: Task description from the TaskSpec.
        repo: GitHub repository (owner/repo).
        base_branch: The base branch (e.g. main).
        task_branch: The task-specific branch (e.g. nubi/smoke-test-task).
        review_focus: Additional focus areas from spec.review.focus.
        provider: LLM provider name.
        api_key: API key for the LLM provider.
    """
    model = create_model(provider, api_key)

    focus_text = (
        "\n".join(f"- {f}" for f in review_focus) if review_focus else "No specific focus areas."
    )

    system_prompt = REVIEWER_SYSTEM_PROMPT.format(
        description=description,
        repo=repo,
        base_branch=base_branch,
        task_branch=task_branch,
        review_focus=focus_text,
    )

    return Agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        callback_handler=LoggingCallbackHandler(),
    )
