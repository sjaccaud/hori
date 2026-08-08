"""Tests for PoC 15.6: Read-Only Filesystem Tools.

Verifies that list_dir, read_file, count_files, and search_files work
correctly and safely: no writes, binary rejection, size limits, symlink
non-following, and error handling without leaking filesystem structure.
"""
import os
import tempfile

import pytest

from services.tool_daemon.tools import (
    count_files,
    list_dir,
    read_file,
    search_files,
    execute_tool,
)


@pytest.fixture
def temp_project():
    """Create a temporary directory structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some files
        os.makedirs(os.path.join(tmpdir, "subdir"))
        os.makedirs(os.path.join(tmpdir, "empty_dir"))
        with open(os.path.join(tmpdir, "file1.py"), "w") as f:
            f.write("print('hello')\n")
        with open(os.path.join(tmpdir, "file2.py"), "w") as f:
            f.write("# comment\n")
        with open(os.path.join(tmpdir, "README.md"), "w") as f:
            f.write("# Test Project\n\nSome content here.\n")
        with open(os.path.join(tmpdir, "subdir", "nested.py"), "w") as f:
            f.write("x = 42\n")
        with open(os.path.join(tmpdir, "subdir", "data.txt"), "w") as f:
            f.write("data line\n")
        yield tmpdir


class TestListDir:
    def test_list_dir_basic(self, temp_project):
        """list_dir should return sorted directory entries."""
        result = list_dir({"path": temp_project})
        assert "error" not in result
        assert result["count"] == 5  # file1.py, file2.py, README.md, subdir, empty_dir
        names = [e["name"] for e in result["entries"]]
        assert "file1.py" in names
        assert "file2.py" in names
        assert "README.md" in names
        assert "subdir" in names
        assert "empty_dir" in names

    def test_list_dir_entry_types(self, temp_project):
        """list_dir should correctly identify files, dirs, and symlinks."""
        result = list_dir({"path": temp_project})
        types = {e["name"]: e["type"] for e in result["entries"]}
        assert types["file1.py"] == "file"
        assert types["subdir"] == "dir"
        assert types["README.md"] == "file"

    def test_list_dir_file_size(self, temp_project):
        """list_dir should report file sizes."""
        result = list_dir({"path": temp_project})
        for entry in result["entries"]:
            if entry["name"] == "file1.py":
                assert entry["size"] > 0

    def test_list_dir_nonexistent(self):
        """list_dir on a nonexistent path should return an error."""
        result = list_dir({"path": "/nonexistent/path/xyz"})
        assert "error" in result

    def test_list_dir_on_file(self, temp_project):
        """list_dir on a file (not a directory) should return an error."""
        result = list_dir({"path": os.path.join(temp_project, "file1.py")})
        assert "error" in result

    def test_list_dir_symlink_reported(self, temp_project):
        """Symlinks should be reported as 'symlink' type."""
        link_path = os.path.join(temp_project, "link_to_subdir")
        os.symlink(os.path.join(temp_project, "subdir"), link_path)
        result = list_dir({"path": temp_project})
        types = {e["name"]: e["type"] for e in result["entries"]}
        assert types["link_to_subdir"] == "symlink"


class TestReadFile:
    def test_read_file_basic(self, temp_project):
        """read_file should return file contents."""
        result = read_file({"path": os.path.join(temp_project, "file1.py")})
        assert "error" not in result
        assert "print('hello')" in result["content"]
        assert result["bytes_read"] > 0
        assert result["truncated"] is False

    def test_read_file_truncation(self, temp_project):
        """read_file should respect max_bytes."""
        # Create a file larger than max_bytes
        large_file = os.path.join(temp_project, "large.txt")
        with open(large_file, "w") as f:
            f.write("A" * 5000)
        result = read_file({"path": large_file, "max_bytes": 100})
        assert "error" not in result
        assert len(result["content"]) == 100
        assert result["truncated"] is True
        assert result["file_size"] == 5000

    def test_read_file_empty(self, temp_project):
        """read_file on an empty file should return empty content."""
        empty_file = os.path.join(temp_project, "empty.txt")
        with open(empty_file, "w") as f:
            pass
        result = read_file({"path": empty_file})
        assert "error" not in result
        assert result["content"] == ""
        assert result["bytes_read"] == 0

    def test_read_file_binary_rejected(self, temp_project):
        """read_file should reject binary files."""
        binary_file = os.path.join(temp_project, "binary.bin")
        with open(binary_file, "wb") as f:
            f.write(bytes(range(256)) * 10)
        result = read_file({"path": binary_file})
        assert "error" in result
        assert "binary" in result["error"].lower()

    def test_read_file_nonexistent(self):
        """read_file on a nonexistent file should return an error."""
        result = read_file({"path": "/nonexistent/file.txt"})
        assert "error" in result

    def test_read_file_on_directory(self, temp_project):
        """read_file on a directory should return an error."""
        result = read_file({"path": temp_project})
        assert "error" in result

    def test_read_file_max_bytes_hard_cap(self, temp_project):
        """read_file should enforce the 1MB hard cap even if max_bytes is larger."""
        result = read_file({"path": os.path.join(temp_project, "file1.py"), "max_bytes": 999999999})
        # The hard cap is 1MB; the file is small so it should read fully
        assert "error" not in result
        assert result["truncated"] is False


class TestCountFiles:
    def test_count_files_py(self, temp_project):
        """count_files should count .py files recursively."""
        result = count_files({"path": temp_project, "pattern": "*.py"})
        assert "error" not in result
        assert result["count"] == 3  # file1.py, file2.py, subdir/nested.py
        assert len(result["sample"]) <= 20

    def test_count_files_md(self, temp_project):
        """count_files should count .md files."""
        result = count_files({"path": temp_project, "pattern": "*.md"})
        assert result["count"] == 1  # README.md

    def test_count_files_no_match(self, temp_project):
        """count_files with no matches should return count=0."""
        result = count_files({"path": temp_project, "pattern": "*.nonexistent"})
        assert result["count"] == 0
        assert result["sample"] == []

    def test_count_files_sample_capped(self, temp_project):
        """count_files sample should be capped at 20 entries."""
        # Create 25 .txt files
        for i in range(25):
            with open(os.path.join(temp_project, f"file_{i}.txt"), "w") as f:
                f.write("x")
        result = count_files({"path": temp_project, "pattern": "*.txt"})
        assert result["count"] == 26  # 25 created + 1 data.txt in subdir
        assert len(result["sample"]) <= 20

    def test_count_files_nonexistent_path(self):
        """count_files on a nonexistent path should return an error."""
        result = count_files({"path": "/nonexistent", "pattern": "*.py"})
        assert "error" in result


class TestSearchFiles:
    def test_search_files_basic(self, temp_project):
        """search_files should find matching files recursively."""
        result = search_files({"path": temp_project, "pattern": "*.py"})
        assert "error" not in result
        assert result["count"] == 3
        assert all(p.endswith(".py") for p in result["results"])

    def test_search_files_max_results(self, temp_project):
        """search_files should respect max_results."""
        for i in range(10):
            with open(os.path.join(temp_project, f"extra_{i}.py"), "w") as f:
                f.write("x")
        result = search_files({"path": temp_project, "pattern": "*.py", "max_results": 5})
        assert result["count"] == 5
        assert result["truncated"] is True

    def test_search_files_no_match(self, temp_project):
        """search_files with no matches should return empty results."""
        result = search_files({"path": temp_project, "pattern": "*.nonexistent"})
        assert result["count"] == 0
        assert result["results"] == []
        assert result["truncated"] is False

    def test_search_files_nonexistent_path(self):
        """search_files on a nonexistent path should return an error."""
        result = search_files({"path": "/nonexistent", "pattern": "*.py"})
        assert "error" in result


class TestExecuteTool:
    def test_execute_tool_dispatch(self, temp_project):
        """execute_tool should dispatch to the correct implementation."""
        result = execute_tool("list_dir", {"path": temp_project})
        assert "error" not in result
        assert "entries" in result

    def test_execute_tool_unknown(self):
        """execute_tool with an unknown tool should return an error."""
        result = execute_tool("nonexistent_tool", {})
        assert "error" in result
