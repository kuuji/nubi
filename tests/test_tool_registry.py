"""Tests for nubi.tools — tool registry and filtering."""

from __future__ import annotations

from nubi.tools import TOOL_GROUPS, get_tools


class TestToolGroups:
    def test_shell_group(self) -> None:
        assert "shell" in TOOL_GROUPS
        assert len(TOOL_GROUPS["shell"]) == 1

    def test_git_group(self) -> None:
        assert "git" in TOOL_GROUPS
        assert len(TOOL_GROUPS["git"]) == 5

    def test_file_read_group(self) -> None:
        assert "file_read" in TOOL_GROUPS

    def test_file_write_group(self) -> None:
        assert "file_write" in TOOL_GROUPS

    def test_file_list_group(self) -> None:
        assert "file_list" in TOOL_GROUPS


class TestGetTools:
    def test_returns_shell(self) -> None:
        assert len(get_tools(["shell"], "/tmp/test")) == 1

    def test_git_expands_to_five(self) -> None:
        assert len(get_tools(["git"], "/tmp/test")) == 5

    def test_multiple_groups(self) -> None:
        assert len(get_tools(["shell", "git", "file_read"], "/tmp/test")) == 7

    def test_empty_list(self) -> None:
        assert get_tools([], "/tmp/test") == []

    def test_unknown_tool_ignored(self) -> None:
        assert get_tools(["nonexistent"], "/tmp/test") == []
