"""Tests for ontrack.py"""

import grp
import logging
import os
import pwd
import sys
import tempfile
import textwrap

import pytest
import yaml

# Ensure the repo root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ontrack import (
    _build_directory_entry,
    _uid_to_username,
    format_size,
    get_directory_stats,
    get_group_members,
    get_group_subdirectories,
    get_username,
    load_config,
    main,
    report_directory,
)


# ---------------------------------------------------------------------------
# _uid_to_username (caching helper)
# ---------------------------------------------------------------------------


def test_uid_to_username_current_user():
    """_uid_to_username returns the current user's login name."""
    uid = os.getuid()
    expected = pwd.getpwuid(uid).pw_name
    assert _uid_to_username(uid) == expected


def test_uid_to_username_unknown_uid():
    """_uid_to_username returns the UID as a string for unknown UIDs."""
    # Use a UID that is almost certainly not assigned on any real system.
    # 2**31 - 1 is the maximum 32-bit signed integer and well beyond any
    # normal UID range.
    missing_uid = 2**31 - 1
    known_uids = {e.pw_uid for e in pwd.getpwall()}
    if missing_uid in known_uids:
        pytest.skip("UID 2**31-1 unexpectedly exists on this system")
    assert _uid_to_username(missing_uid) == str(missing_uid)


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


# ---------------------------------------------------------------------------
# get_group_members
# ---------------------------------------------------------------------------


def test_get_group_members_current_user():
    """The current user should appear in their own primary group's member set."""
    current_uid = os.getuid()
    current_gid = os.getgid()
    current_user = pwd.getpwuid(current_uid).pw_name
    group_name = grp.getgrgid(current_gid).gr_name

    members = get_group_members(group_name)
    assert isinstance(members, set)
    assert current_user in members


def test_get_group_members_invalid_group():
    with pytest.raises(ValueError, match="not found"):
        get_group_members("__nonexistent_group_xyz__")


# ---------------------------------------------------------------------------
# get_directory_stats with group filter
# ---------------------------------------------------------------------------


def test_get_directory_stats_group_matches_current_user():
    """Files owned by the current user are counted when their group is used."""
    current_uid = os.getuid()
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    with tempfile.TemporaryDirectory() as tmpdir:
        path_a = os.path.join(tmpdir, "a.txt")
        with open(path_a, "w") as f:
            f.write("hello")  # 5 bytes

        stats = get_directory_stats(tmpdir, group=group_name)
        assert stats["file_count"] == 1
        assert stats["total_size"] == 5


def test_get_directory_stats_group_excludes_files():
    """When a group has no matching file owners, counts should be zero."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path_a = os.path.join(tmpdir, "a.txt")
        with open(path_a, "w") as f:
            f.write("hello")

        # Use a group that the current user does not belong to (root group
        # members typically don't include regular users).  Find the first
        # group whose member set does not include the current user.
        current_user = pwd.getpwuid(os.getuid()).pw_name
        other_group = None
        for g in grp.getgrall():
            members = set(g.gr_mem)
            # Also add primary-group users
            for pw_entry in pwd.getpwall():
                if pw_entry.pw_gid == g.gr_gid:
                    members.add(pw_entry.pw_name)
            if current_user not in members:
                other_group = g.gr_name
                break

        if other_group is None:
            pytest.skip("No group found that excludes the current user")

        stats = get_directory_stats(tmpdir, group=other_group)
        assert stats["file_count"] == 0
        assert stats["total_size"] == 0


# ---------------------------------------------------------------------------
# report_directory with group
# ---------------------------------------------------------------------------


def test_report_directory_with_group(capsys):
    """report_directory prints the Group line when a group is supplied."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "sample.txt"), "w") as f:
            f.write("data")

        report_directory(tmpdir, group=group_name)
        captured = capsys.readouterr()
        assert "Group" in captured.out
        assert group_name in captured.out


def test_report_directory_without_group_no_group_line(capsys):
    """report_directory does not print a Group line when no group is given."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "sample.txt"), "w") as f:
            f.write("data")

        report_directory(tmpdir)
        captured = capsys.readouterr()
        assert "Group" not in captured.out


# ---------------------------------------------------------------------------
# main with group
# ---------------------------------------------------------------------------


def test_main_with_group(tmp_path, capsys):
    """main reports first-level subdirectories owned by group members."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Create a subdirectory owned by the current user (who is in group_name).
    user_subdir = data_dir / "user_dir"
    user_subdir.mkdir()
    (user_subdir / "file.txt").write_text("hello world")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\n")

    main(str(config_file), group=group_name)
    captured = capsys.readouterr()
    assert str(user_subdir) in captured.out
    assert "Group" in captured.out
    assert group_name in captured.out


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------


def test_main_logs_directories(tmp_path, caplog):
    """main logs the list of directories supplied in the config."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("hello")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\n")

    with caplog.at_level(logging.INFO, logger="ontrack"):
        main(str(config_file))

    assert "Directories supplied" in caplog.text
    assert str(data_dir) in caplog.text


def test_main_logs_group_members(tmp_path, caplog):
    """main logs the users found in the supplied group."""
    current_uid = os.getuid()
    current_gid = os.getgid()
    current_user = pwd.getpwuid(current_uid).pw_name
    group_name = grp.getgrgid(current_gid).gr_name

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("hello")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\n")

    with caplog.at_level(logging.INFO, logger="ontrack"):
        main(str(config_file), group=group_name)

    assert "Users found in group" in caplog.text
    assert group_name in caplog.text
    assert current_user in caplog.text


def test_main_no_group_logging_skipped(tmp_path, caplog):
    """main does not log group members when no group is supplied."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("hello")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\n")

    with caplog.at_level(logging.INFO, logger="ontrack"):
        main(str(config_file))

    assert "Users found in group" not in caplog.text


# ---------------------------------------------------------------------------
# get_group_subdirectories
# ---------------------------------------------------------------------------


def test_get_group_subdirectories_returns_owned_subdirs(tmp_path):
    """Subdirectories owned by the current user are returned when in the group."""
    current_uid = os.getuid()
    current_gid = os.getgid()
    current_user = pwd.getpwuid(current_uid).pw_name
    group_name = grp.getgrgid(current_gid).gr_name
    members = get_group_members(group_name)

    parent = tmp_path / "parent"
    parent.mkdir()
    owned_sub = parent / "owned"
    owned_sub.mkdir()

    result = get_group_subdirectories(str(parent), members)
    assert str(owned_sub) in result
    assert current_user  # sanity-check that we know the user


def test_get_group_subdirectories_empty_dir(tmp_path):
    """An empty directory yields an empty list."""
    parent = tmp_path / "empty"
    parent.mkdir()
    result = get_group_subdirectories(str(parent), {"anyone"})
    assert result == []


def test_get_group_subdirectories_excludes_non_member(tmp_path):
    """Subdirectories owned by users not in the group are excluded."""
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "sub").mkdir()

    # Use an empty member set so nothing matches.
    result = get_group_subdirectories(str(parent), set())
    assert result == []


def test_get_group_subdirectories_ignores_files(tmp_path):
    """Regular files inside the parent directory are not returned."""
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "file.txt").write_text("data")

    current_user = pwd.getpwuid(os.getuid()).pw_name
    result = get_group_subdirectories(str(parent), {current_user})
    assert result == []


def test_get_group_subdirectories_nonexistent_parent():
    """A non-existent parent directory returns an empty list (no exception)."""
    result = get_group_subdirectories("/nonexistent/path/xyz_abc", {"anyone"})
    assert result == []


def test_get_group_subdirectories_only_first_level(tmp_path):
    """Only immediate children are inspected; nested subdirectories are ignored."""
    current_user = pwd.getpwuid(os.getuid()).pw_name
    members = {current_user}

    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child"
    child.mkdir()
    grandchild = child / "grandchild"
    grandchild.mkdir()

    result = get_group_subdirectories(str(parent), members)
    assert str(child) in result
    assert str(grandchild) not in result


# ---------------------------------------------------------------------------
# main – group from config file
# ---------------------------------------------------------------------------


def test_main_group_from_config(tmp_path, capsys):
    """main reads the group from the config file when --group is not supplied."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user_subdir = data_dir / "user_dir"
    user_subdir.mkdir()
    (user_subdir / "file.txt").write_text("content")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\ngroup: {group_name}\n")

    main(str(config_file))  # no group kwarg; should come from config
    captured = capsys.readouterr()
    assert str(user_subdir) in captured.out
    assert group_name in captured.out


def test_main_cli_group_overrides_config(tmp_path, capsys):
    """CLI --group takes precedence over the group key in the config file."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user_subdir = data_dir / "user_dir"
    user_subdir.mkdir()
    (user_subdir / "file.txt").write_text("content")

    config_file = tmp_path / "config.yaml"
    # Config contains a bogus group; the CLI group should win.
    config_file.write_text(f"directories:\n  - {data_dir}\ngroup: __bogus_group__\n")

    main(str(config_file), group=group_name)
    captured = capsys.readouterr()
    assert str(user_subdir) in captured.out
    assert group_name in captured.out


def test_main_with_group_invalid_parent_dir(tmp_path, capsys):
    """A configured directory that does not exist emits a warning and is skipped."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    config_file = tmp_path / "config.yaml"
    config_file.write_text("directories:\n  - /nonexistent/path/xyz\n")

    main(str(config_file), group=group_name)
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


# ---------------------------------------------------------------------------
# light mode
# ---------------------------------------------------------------------------


def test_report_directory_light_mode_omits_stats(capsys):
    """In light mode, Files and Total size lines are not printed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "sample.txt"), "w") as f:
            f.write("data")

        report_directory(tmpdir, light=True)
        captured = capsys.readouterr()
        assert "Directory" in captured.out
        assert "Username" in captured.out
        assert "Files" not in captured.out
        assert "Total size" not in captured.out


def test_report_directory_light_mode_does_not_call_get_stats(monkeypatch):
    """In light mode, get_directory_stats is never called."""
    called = []

    import ontrack as rd

    original = rd.get_directory_stats
    monkeypatch.setattr(rd, "get_directory_stats", lambda *a, **kw: called.append(1) or original(*a, **kw))

    with tempfile.TemporaryDirectory() as tmpdir:
        report_directory(tmpdir, light=True)

    assert called == [], "get_directory_stats should not be called in light mode"


def test_main_light_mode_omits_stats(tmp_path, capsys):
    """main in light mode does not print file count or size."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("hello world")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\n")

    main(str(config_file), light=True)
    captured = capsys.readouterr()
    assert str(data_dir) in captured.out
    assert "Files" not in captured.out
    assert "Total size" not in captured.out


# ---------------------------------------------------------------------------
# YAML output (--output)
# ---------------------------------------------------------------------------


def test_main_output_writes_yaml(tmp_path):
    """When output is given, a valid YAML file is written instead of printing."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("hello world")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\n")

    output_file = str(tmp_path / "report.yaml")
    main(str(config_file), output=output_file)

    with open(output_file) as fh:
        report = yaml.safe_load(fh)

    assert isinstance(report, list)
    assert len(report) == 1
    entry = report[0]
    assert entry["directory"] == str(data_dir)
    assert "username" in entry
    assert "file_count" in entry
    assert "total_size" in entry
    assert "total_size_human" in entry


def test_main_output_does_not_print(tmp_path, capsys):
    """When output is given, nothing is printed to stdout."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("content")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\n")

    output_file = str(tmp_path / "report.yaml")
    main(str(config_file), output=output_file)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_main_output_light_mode(tmp_path):
    """YAML output in light mode omits file_count, total_size, total_size_human."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("hello")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"directories:\n  - {data_dir}\n")

    output_file = str(tmp_path / "report.yaml")
    main(str(config_file), light=True, output=output_file)

    with open(output_file) as fh:
        report = yaml.safe_load(fh)

    assert isinstance(report, list)
    entry = report[0]
    assert entry["directory"] == str(data_dir)
    assert "file_count" not in entry
    assert "total_size" not in entry
    assert "total_size_human" not in entry


# ---------------------------------------------------------------------------
# get_directory_stats with show_progress
# ---------------------------------------------------------------------------


def test_get_directory_stats_with_progress(tmp_path):
    """get_directory_stats works correctly when show_progress=True."""
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world!")

    stats = get_directory_stats(str(tmp_path), show_progress=True)
    assert stats["file_count"] == 2
    assert stats["total_size"] == 11


# ---------------------------------------------------------------------------
# _build_directory_entry
# ---------------------------------------------------------------------------


def test_build_directory_entry_valid(tmp_path):
    """_build_directory_entry returns a dict with all expected keys."""
    (tmp_path / "f.txt").write_text("data")
    entry = _build_directory_entry(str(tmp_path))
    assert entry is not None
    assert entry["directory"] == str(tmp_path)
    assert "username" in entry
    assert "file_count" in entry
    assert "total_size" in entry
    assert "total_size_human" in entry


def test_build_directory_entry_light(tmp_path):
    """_build_directory_entry in light mode omits stats keys."""
    (tmp_path / "f.txt").write_text("data")
    entry = _build_directory_entry(str(tmp_path), light=True)
    assert entry is not None
    assert "file_count" not in entry
    assert "total_size" not in entry
    assert "total_size_human" not in entry


def test_build_directory_entry_invalid(capsys):
    """_build_directory_entry returns None and warns for a non-existent path."""
    result = _build_directory_entry("/nonexistent/path/xyz_abc")
    assert result is None
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


def test_build_directory_entry_with_group(tmp_path):
    """_build_directory_entry includes the group key when group is supplied."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name
    (tmp_path / "f.txt").write_text("data")
    entry = _build_directory_entry(str(tmp_path), group=group_name)
    assert entry is not None
    assert entry["group"] == group_name

