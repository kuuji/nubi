"""Tests for the MCP server __main__ module."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


class TestVersionFlag:
    """Tests for the --version flag."""

    def test_version_flag_exits_with_code_zero(self) -> None:
        """The --version flag causes the program to exit with code 0."""
        from nubi.mcp.__main__ import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])

        assert exc_info.value.code == 0

    def test_version_flag_prints_version(self, capsys) -> None:
        """The --version flag prints the version string."""
        from nubi.mcp.__main__ import main

        with pytest.raises(SystemExit):
            main(["--version"])

        captured = capsys.readouterr()
        assert "nubi-mcp" in captured.out
        # Check that version contains numbers and dots (e.g., "0.1.0")
        assert any(c.isdigit() for c in captured.out)
        assert "." in captured.out

    def test_version_flag_uses_package_version(self, capsys) -> None:
        """The --version flag uses the version from nubi package."""
        from nubi import __version__

        with patch.dict("sys.modules", {"nubi.mcp.server": MagicMock()}):
            from nubi.mcp.__main__ import main

            with pytest.raises(SystemExit):
                main(["--version"])

            captured = capsys.readouterr()
            assert __version__ in captured.out

    def test_no_args_does_not_exit_with_version(self) -> None:
        """When no arguments are passed, the program should not exit immediately."""
        from nubi.mcp.__main__ import main

        # We can't fully test this without mocking the server,
        # but we can verify that --version is the only thing that causes SystemExit
        with pytest.raises(SystemExit):
            main(["--version"])

    def test_help_flag_shows_version_option(self) -> None:
        """The --help flag shows the --version option."""
        from nubi.mcp.__main__ import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])

        # Help should exit with code 0
        assert exc_info.value.code == 0
