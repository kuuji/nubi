"""Read executor results from GitHub API."""

from __future__ import annotations

import base64
import json
import logging

import aiohttp

from nubi.agents.gate_result import GatesResult, gates_file_path
from nubi.agents.monitor_result import MonitorResult, monitor_file_path
from nubi.agents.result import ExecutorResult, result_file_path
from nubi.agents.review_result import ReviewResult, review_file_path
from nubi.exceptions import ResultError

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


def _task_id_from_branch(branch: str) -> str:
    """Extract task ID from a branch name like 'nubi/my-task'."""
    if branch.startswith("nubi/"):
        return branch[len("nubi/") :]
    return branch


async def read_executor_result(repo: str, branch: str, token: str) -> ExecutorResult:
    """Read .nubi/{task_id}/result.json from the task branch via GitHub REST API."""
    task_id = _task_id_from_branch(branch)
    file_path = result_file_path(task_id)
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    params = {"ref": branch}

    try:
        async with aiohttp.ClientSession() as session:  # noqa: SIM117
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise ResultError(
                        f"GitHub API returned {resp.status} for {file_path} "
                        f"on {repo}@{branch}: {body}"
                    )
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise ResultError(f"HTTP error reading result from {repo}@{branch}: {exc}") from exc

    try:
        content_b64 = data["content"]
        content_bytes = base64.b64decode(content_b64)
        result_dict = json.loads(content_bytes)
        return ExecutorResult.model_validate(result_dict)
    except (KeyError, json.JSONDecodeError, Exception) as exc:
        raise ResultError(f"Failed to parse result from {repo}@{branch}: {exc}") from exc


async def read_gates_result(repo: str, branch: str, token: str) -> GatesResult:
    """Read .nubi/{task_id}/gates.json from the task branch via GitHub REST API."""
    task_id = _task_id_from_branch(branch)
    file_path = gates_file_path(task_id)
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    params = {"ref": branch}

    try:
        async with aiohttp.ClientSession() as session:  # noqa: SIM117
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise ResultError(
                        f"GitHub API returned {resp.status} for {file_path} "
                        f"on {repo}@{branch}: {body}"
                    )
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise ResultError(f"HTTP error reading gates result from {repo}@{branch}: {exc}") from exc

    try:
        content_b64 = data["content"]
        content_bytes = base64.b64decode(content_b64)
        result_dict = json.loads(content_bytes)
        return GatesResult.model_validate(result_dict)
    except (KeyError, json.JSONDecodeError, Exception) as exc:
        raise ResultError(f"Failed to parse gates result from {repo}@{branch}: {exc}") from exc


async def read_review_result(repo: str, branch: str, token: str) -> ReviewResult:
    """Read .nubi/{task_id}/review.json from the task branch via GitHub REST API."""
    task_id = _task_id_from_branch(branch)
    file_path = review_file_path(task_id)
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    params = {"ref": branch}

    try:
        async with aiohttp.ClientSession() as session:  # noqa: SIM117
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise ResultError(
                        f"GitHub API returned {resp.status} for {file_path} "
                        f"on {repo}@{branch}: {body}"
                    )
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise ResultError(f"HTTP error reading review result from {repo}@{branch}: {exc}") from exc

    try:
        content_b64 = data["content"]
        content_bytes = base64.b64decode(content_b64)
        result_dict = json.loads(content_bytes)
        return ReviewResult.model_validate(result_dict)
    except (KeyError, json.JSONDecodeError, Exception) as exc:
        raise ResultError(f"Failed to parse review result from {repo}@{branch}: {exc}") from exc


async def read_monitor_result(repo: str, branch: str, token: str) -> MonitorResult:
    """Read .nubi/{task_id}/monitor.json from the task branch via GitHub REST API."""
    task_id = _task_id_from_branch(branch)
    file_path = monitor_file_path(task_id)
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    params = {"ref": branch}

    try:
        async with aiohttp.ClientSession() as session:  # noqa: SIM117
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise ResultError(
                        f"GitHub API returned {resp.status} for {file_path} "
                        f"on {repo}@{branch}: {body}"
                    )
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise ResultError(f"HTTP error reading monitor result from {repo}@{branch}: {exc}") from exc

    try:
        content_b64 = data["content"]
        content_bytes = base64.b64decode(content_b64)
        result_dict = json.loads(content_bytes)
        return MonitorResult.model_validate(result_dict)
    except (KeyError, json.JSONDecodeError, Exception) as exc:
        raise ResultError(f"Failed to parse monitor result from {repo}@{branch}: {exc}") from exc
