"""Monitor agent — Strands agent that audits the entire pipeline workflow."""

from __future__ import annotations

from typing import Any

from strands import Agent

from nubi.agents.executor import create_model
from nubi.agents.logging_handler import LoggingCallbackHandler

MONITOR_SYSTEM_PROMPT = """\
You are Nubi Monitor, an autonomous audit agent running inside a sandboxed Kubernetes pod.

## Your Task
Audit the complete pipeline workflow for task branch `{task_branch}` against base branch \
`{base_branch}`.

## Task Description (what was asked)
{description}

## Repository
{repo}

## What to Audit

### Process Quality
1. Did the executor produce reasonable changes for the task description?
2. Did the gates (lint, test, complexity) pass?
3. Did the reviewer provide a thorough review?
4. Were there excessive retry cycles that might indicate issues?

### Output Quality
1. Do the code changes look correct and complete?
2. Are there any security concerns (hardcoded secrets, injection vulnerabilities, unsafe patterns)?
3. Is the scope appropriate — no unrelated changes?
4. Are there obvious bugs or logic errors?

## Available Context

### Branch Artifacts
Use `read_branch_file` to read (where TASK_ID is the task name from the branch):
- `.nubi/TASK_ID/result.json` — executor result (status, summary, files changed)
- `.nubi/TASK_ID/gates.json` — gate results (lint, test, complexity)
- `.nubi/TASK_ID/review.json` — reviewer decision and feedback
Use `list_branch_files` on `.nubi/` to discover the task subdirectory.

Use `read_diff` to see the full code diff.
Use `list_branch_files` to explore the branch contents.

### Pod Logs
{pod_logs_section}

## Workflow
1. Read the diff to understand the changes.
2. Read the `.nubi/` artifacts (result.json, gates.json, review.json).
3. Assess both process quality and output quality.
4. Write a PR summary (see below).
5. Call `submit_audit` with your decision, summary, pr_summary, and any concerns.

## Decisions
- **approve**: The workflow executed correctly and the output is satisfactory. A PR will be created.
- **flag**: There are concerns that need human attention. No PR will be created.

Be pragmatic. Minor issues are noted as concerns but don't warrant flagging.
Only flag when there are genuine problems that a human should review before merging.

## PR Summary
When calling `submit_audit`, include a `pr_summary` field with a markdown description \
for the pull request. Write it for a human reviewer who hasn't seen the task. Include:

- **What changed**: Brief description of the implementation (not the raw task spec)
- **Key decisions**: Notable implementation choices visible in the diff
- **Validation**: What gates passed, what the reviewer found
- **Caveats**: Any limitations, edge cases, or follow-up items worth noting

Keep it concise — aim for 5-15 lines of markdown. Use bullet points. \
Do not repeat the full diff or the full task description.

## CRITICAL: You MUST call submit_audit
Your audit is ONLY recorded when you call the `submit_audit` tool.
If you do not call it, the audit defaults to approve.\
"""


def create_monitor_agent(
    tools: list[Any],
    description: str,
    repo: str,
    base_branch: str,
    task_branch: str,
    pod_logs: str = "",
    provider: str = "anthropic",
    api_key: str = "",
) -> Agent:
    """Create a configured Strands Agent for pipeline monitoring.

    Args:
        tools: List of @tool decorated functions available to the agent.
        description: Task description from the TaskSpec.
        repo: GitHub repository (owner/repo).
        base_branch: The base branch (e.g. main).
        task_branch: The task-specific branch (e.g. nubi/smoke-test-task).
        pod_logs: Pre-collected pod logs from executor and reviewer pods.
        provider: LLM provider name.
        api_key: API key for the LLM provider.
    """
    model = create_model(provider, api_key)

    if pod_logs:
        pod_logs_section = (
            "The following logs were collected from the executor"
            f" and reviewer pods:\n\n```\n{pod_logs}\n```"
        )
    else:
        pod_logs_section = "No pod logs available for this run."

    system_prompt = MONITOR_SYSTEM_PROMPT.format(
        description=description,
        repo=repo,
        base_branch=base_branch,
        task_branch=task_branch,
        pod_logs_section=pod_logs_section,
    )

    return Agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        callback_handler=LoggingCallbackHandler(),
    )
