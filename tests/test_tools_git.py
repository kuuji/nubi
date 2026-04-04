"""Tests for nubi.tools.git — git operations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.tools.git import (
    configure,
    git_clone,
    git_commit,
    git_diff,
    git_log,
    git_push,
    git_status,
)


class TestGitClone:
    @patch("nubi.tools.git.subprocess.run")
    def test_clones_with_token(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        git_clone("kuuji/repo", "main", "tok123", "/workspace")
        clone_call = mock_run.call_args_list[0]
        assert "x-access-token:tok123" in str(clone_call)
        assert "kuuji/repo" in str(clone_call)

    @patch("nubi.tools.git.subprocess.run")
    def test_configures_git_user(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        git_clone("kuuji/repo", "main", "tok", "/workspace")
        calls_str = str(mock_run.call_args_list)
        assert "user.email" in calls_str
        assert "user.name" in calls_str

    @patch("nubi.tools.git.subprocess.run")
    def test_creates_branch_if_not_exists(self, mock_run: MagicMock) -> None:
        returns = [
            MagicMock(returncode=0, stdout="", stderr=""),  # clone
            MagicMock(returncode=0),  # config email
            MagicMock(returncode=0),  # config name
            MagicMock(returncode=1, stdout="", stderr="error"),  # checkout fails
            MagicMock(returncode=0, stdout="", stderr=""),  # checkout -b succeeds
        ]
        mock_run.side_effect = returns
        git_clone("kuuji/repo", "feat/new", "tok", "/workspace")
        last_call = mock_run.call_args_list[-1]
        assert "-b" in str(last_call)


class TestGitDiff:
    @patch("nubi.tools.git.subprocess.run")
    def test_shows_diff(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.return_value = MagicMock(returncode=0, stdout="+ new line", stderr="")
        result = git_diff()
        assert "+ new line" in result

    @patch("nubi.tools.git.subprocess.run")
    def test_no_changes(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert "No changes" in git_diff()


class TestGitLog:
    @patch("nubi.tools.git.subprocess.run")
    def test_shows_log(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123 initial commit", stderr="")
        assert "abc123" in git_log()


class TestGitCommit:
    @patch("nubi.tools.git.subprocess.run")
    def test_stages_and_commits(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.return_value = MagicMock(returncode=0, stdout="committed", stderr="")
        git_commit(message="test commit")
        calls_str = str(mock_run.call_args_list)
        assert "add" in calls_str
        assert "commit" in calls_str


class TestGitPush:
    @patch("nubi.tools.git.subprocess.run")
    def test_pushes(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.return_value = MagicMock(returncode=0, stdout="pushed", stderr="")
        git_push()
        assert "push" in str(mock_run.call_args_list)


class TestGitStatus:
    @patch("nubi.tools.git.subprocess.run")
    def test_shows_status(self, mock_run: MagicMock) -> None:
        configure("/workspace")
        mock_run.return_value = MagicMock(returncode=0, stdout="On branch main", stderr="")
        assert "On branch" in git_status()
