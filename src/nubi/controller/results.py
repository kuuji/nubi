"""Read executor results from GitHub API."""

from __future__ import annotations

import base64
import json
import logging

import aiohttp

from nubi.agents.gate_result import GATES_FILE_PATH, GatesResult
from nubi.agents.result import RESULT_FILE_PATH, ExecutorResult
from nubi.agents.review_result import REVIEW_FILE_PATH, ReviewResult
from nubi.exceptions import ResultError

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


async def read_executor_result(repo: str, branch: str, token: str) -> ExecutorResult:
    """Read .nubi/result.json from the task branch via GitHub REST API.

    Args:
        repo: GitHub repository (owner/repo).
        branch: Git branch name.
        token: GitHub token for authentication.

    Returns:
        Parsed ExecutorResult from the branch.

    Raises:
        ResultError: If the file can't be read or parsed.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{RESULT_FILE_PATH}"
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
                        f"GitHub API returned {resp.status} for {RESULT_FILE_PATH} "
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
    """Read .nubi/gates.json from the task branch via GitHub REST API.

    Args:
        repo: GitHub repository (owner/repo).
        branch: Git branch name.
        token: GitHub token for authentication.

    Returns:
        Parsed GatesResult from the branch.

    Raises:
        ResultError: If the file can't be read or parsed.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{GATES_FILE_PATH}"
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
                        f"GitHub API returned {resp.status} for {GATES_FILE_PATH} "
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
    """Read .nubi/review.json from the task branch via GitHub REST API.

    Args:
        repo: GitHub repository (owner/repo).
        branch: Git branch name.
        token: GitHub token for authentication.

    Returns:
        Parsed ReviewResult from the branch.

    Raises:
        ResultError: If the file can't be read or parsed.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{REVIEW_FILE_PATH}"
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
                        f"GitHub API returned {resp.status} for {REVIEW_FILE_PATH} "
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
