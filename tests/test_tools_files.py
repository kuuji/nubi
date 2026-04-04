"""Tests for nubi.tools.files — workspace-scoped file operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from nubi.tools.files import configure, file_list, file_read, file_write


class TestFileRead:
    def test_reads_file(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        (tmp_path / "hello.txt").write_text("world")
        assert file_read(path="hello.txt") == "world"

    def test_reads_nested_file(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "file.txt").write_text("nested")
        assert file_read(path="sub/file.txt") == "nested"

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        with pytest.raises(ValueError, match="Absolute"):
            file_read(path="/etc/passwd")

    def test_rejects_traversal(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        with pytest.raises(ValueError, match="escapes"):
            file_read(path="../../../etc/passwd")


class TestFileWrite:
    def test_writes_file(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        file_write(path="out.txt", content="data")
        assert (tmp_path / "out.txt").read_text() == "data"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        file_write(path="a/b/c.txt", content="deep")
        assert (tmp_path / "a" / "b" / "c.txt").read_text() == "deep"

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        with pytest.raises(ValueError, match="Absolute"):
            file_write(path="/tmp/evil.txt", content="bad")

    def test_rejects_traversal(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        with pytest.raises(ValueError, match="escapes"):
            file_write(path="../../evil.txt", content="bad")


class TestFileList:
    def test_lists_directory(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        result = file_list(path=".")
        assert "a.txt" in result
        assert "b.txt" in result

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        with pytest.raises(ValueError, match="Absolute"):
            file_list(path="/etc")

    def test_not_a_directory(self, tmp_path: Path) -> None:
        configure(str(tmp_path))
        (tmp_path / "file.txt").write_text("x")
        with pytest.raises(ValueError, match="Not a directory"):
            file_list(path="file.txt")
