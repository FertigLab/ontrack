"""Tests for report_directory.py"""

import os
import sys
import tempfile
import textwrap

import pytest
import yaml

# Ensure the repo root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_directory import (
    format_size,
    get_directory_stats,
    get_username,
    load_config,
    main,
    report_directory,
)


# ---------------------------------------------------------------------------
# format_size
# ---------------------------------------------------------------------------


def test_format_size_bytes():
    assert format_size(512) == "512.00 B"


def test_format_size_kilobytes():
    assert format_size(2048) == "2.00 KB"


def test_format_size_megabytes():
    assert format_size(1024 * 1024) == "1.00 MB"


def test_format_size_gigabytes():
    assert format_size(1024 ** 3) == "1.00 GB"


# ---------------------------------------------------------------------------
# get_directory_stats
# ---------------------------------------------------------------------------


def test_get_directory_stats_empty_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        stats = get_directory_stats(tmpdir)
        assert stats["file_count"] == 0
        assert stats["total_size"] == 0


def test_get_directory_stats_with_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create two files with known sizes
        path_a = os.path.join(tmpdir, "a.txt")
        path_b = os.path.join(tmpdir, "b.txt")
        with open(path_a, "w") as f:
            f.write("hello")  # 5 bytes
        with open(path_b, "w") as f:
            f.write("world!")  # 6 bytes

        stats = get_directory_stats(tmpdir)
        assert stats["file_count"] == 2
        assert stats["total_size"] == 11


def test_get_directory_stats_nested():
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir = os.path.join(tmpdir, "sub")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "nested.txt"), "w") as f:
            f.write("abc")  # 3 bytes

        stats = get_directory_stats(tmpdir)
        assert stats["file_count"] == 1
        assert stats["total_size"] == 3


# ---------------------------------------------------------------------------
# get_username
# ---------------------------------------------------------------------------


def test_get_username_returns_string():
    with tempfile.TemporaryDirectory() as tmpdir:
        username = get_username(tmpdir)
        assert isinstance(username, str)
        assert len(username) > 0


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config():
    config_data = {"directories": ["/tmp/test1", "/tmp/test2"]}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as tmp:
        yaml.dump(config_data, tmp)
        tmp_path = tmp.name

    try:
        loaded = load_config(tmp_path)
        assert loaded["directories"] == ["/tmp/test1", "/tmp/test2"]
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# report_directory
# ---------------------------------------------------------------------------


def test_report_directory_valid(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "sample.txt"), "w") as f:
            f.write("data")

        report_directory(tmpdir)
        captured = capsys.readouterr()
        assert "Directory" in captured.out
        assert "Username" in captured.out
        assert "Files" in captured.out
        assert "Total size" in captured.out
        assert tmpdir in captured.out


def test_report_directory_invalid(capsys):
    report_directory("/nonexistent/path/xyz")
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_no_directories(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("directories: []\n")

    with pytest.raises(SystemExit):
        main(str(config_file))


def test_main_with_valid_directory(tmp_path, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("hello world")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\n")

    main(str(config_file))
    captured = capsys.readouterr()
    assert str(data_dir) in captured.out
    assert "Files" in captured.out
    assert "Total size" in captured.out
