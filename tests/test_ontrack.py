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
    _find_reporting_directories,
    _get_directory_metadata,
    _is_ignored,
    _is_on_track,
    _load_ontrack_yml,
    _run_du,
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
    config_data = {"paths": ["/tmp/test1", "/tmp/test2"]}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as tmp:
        yaml.dump(config_data, tmp)
        tmp_path = tmp.name

    try:
        loaded = load_config(tmp_path)
        assert loaded["paths"] == ["/tmp/test1", "/tmp/test2"]
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
    config_file.write_text("paths: []\n")

    with pytest.raises(SystemExit):
        main(str(config_file))


def test_main_with_valid_directory(tmp_path, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("hello world")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"paths:\n  - {data_dir}\n")

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

        stats = get_directory_stats(tmpdir, groups=[group_name])
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

        stats = get_directory_stats(tmpdir, groups=[other_group])
        assert stats["file_count"] == 0
        assert stats["total_size"] == 0


# ---------------------------------------------------------------------------
# report_directory with group
# ---------------------------------------------------------------------------


def test_report_directory_with_group(capsys):
    """report_directory prints the Group line when groups are supplied."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "sample.txt"), "w") as f:
            f.write("data")

        report_directory(tmpdir, groups=[group_name])
        captured = capsys.readouterr()
        assert "Group" in captured.out
        assert group_name in captured.out
def test_report_directory_without_group_no_group_line(capsys):
    """report_directory does not print a Group line when no groups are given."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "sample.txt"), "w") as f:
            f.write("data")

        report_directory(tmpdir)
        captured = capsys.readouterr()
        assert "Group     :" not in captured.out


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
    config_file.write_text(f"paths:\n  - {data_dir}\n")

    main(str(config_file), groups=[group_name])
    captured = capsys.readouterr()
    assert str(user_subdir) in captured.out
    assert "Group" in captured.out
    assert group_name in captured.out


def test_main_with_multiple_groups(tmp_path, capsys):
    """main accepts multiple groups and reports subdirectories owned by members of any."""
    current_uid = os.getuid()
    current_gid = os.getgid()
    current_user = pwd.getpwuid(current_uid).pw_name
    group_name = grp.getgrgid(current_gid).gr_name

    # Find a second group the current user belongs to, if one exists.
    second_group = group_name
    for g in grp.getgrall():
        if g.gr_name != group_name and current_user in g.gr_mem:
            second_group = g.gr_name
            break

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user_subdir = data_dir / "user_dir"
    user_subdir.mkdir()
    (user_subdir / "file.txt").write_text("hello world")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"paths:\n  - {data_dir}\n")

    main(str(config_file), groups=[group_name, second_group])
    captured = capsys.readouterr()
    assert str(user_subdir) in captured.out
    assert "Group" in captured.out
    assert group_name in captured.out


def test_main_multiple_groups_from_config(tmp_path, capsys):
    """main reads multiple groups from the config file's groups list."""
    current_uid = os.getuid()
    current_gid = os.getgid()
    current_user = pwd.getpwuid(current_uid).pw_name
    group_name = grp.getgrgid(current_gid).gr_name

    # Find a second group the current user belongs to, if one exists.
    second_group = group_name
    for g in grp.getgrall():
        if g.gr_name != group_name and current_user in g.gr_mem:
            second_group = g.gr_name
            break

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user_subdir = data_dir / "user_dir"
    user_subdir.mkdir()
    (user_subdir / "file.txt").write_text("content")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"paths:\n  - {data_dir}\ngroups:\n  - {group_name}\n  - {second_group}\n"
    )

    main(str(config_file))
    captured = capsys.readouterr()
    assert str(user_subdir) in captured.out
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
    config_file.write_text(f"paths:\n  - {data_dir}\n")

    with caplog.at_level(logging.INFO, logger="ontrack"):
        main(str(config_file))

    assert "Paths supplied" in caplog.text
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
    config_file.write_text(f"paths:\n  - {data_dir}\n")

    with caplog.at_level(logging.INFO, logger="ontrack"):
        main(str(config_file), groups=[group_name])

    assert "Users found in group" in caplog.text
    assert group_name in caplog.text
    assert current_user in caplog.text


def test_main_no_group_logging_skipped(tmp_path, caplog):
    """main does not log group members when no group is supplied."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("hello")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"paths:\n  - {data_dir}\n")

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


def test_get_group_subdirectories_descends_past_empty_intermediate_dir(tmp_path):
    """When an owned child contains only subdirectories, descent continues to the leaf."""
    current_user = pwd.getpwuid(os.getuid()).pw_name
    members = {current_user}

    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child"
    child.mkdir()
    grandchild = child / "grandchild"
    grandchild.mkdir()

    result = get_group_subdirectories(str(parent), members)
    # child contains only directories → not the reporting directory
    assert str(child) not in result
    # grandchild is empty (leaf) → it is the reporting directory
    assert str(grandchild) in result


def test_get_group_subdirectories_descends_through_owned_child_with_dotfile_and_subdirs(tmp_path):
    """When an owned child has only a hidden (dot) file and subdirs, descent continues into the subdirs."""
    current_user = pwd.getpwuid(os.getuid()).pw_name
    members = {current_user}

    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child"
    child.mkdir()
    (child / ".hidden").write_text("hidden")
    grandchild = child / "grandchild"
    grandchild.mkdir()

    result = get_group_subdirectories(str(parent), members, ignore_patterns=[".*"])
    # grandchild is the leaf (no subdirs) → it is the reporting directory
    assert str(grandchild) in result
    # child has no visible files → it is not the reporting directory
    assert str(child) not in result


def test_get_group_subdirectories_stops_at_owned_child_with_visible_file(tmp_path):
    """When an owned child contains a visible file, it is returned as the reporting directory."""
    current_user = pwd.getpwuid(os.getuid()).pw_name
    members = {current_user}

    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child"
    child.mkdir()
    (child / "data.txt").write_text("content")
    grandchild = child / "grandchild"
    grandchild.mkdir()

    result = get_group_subdirectories(str(parent), members)
    # child has a visible file → it is the reporting directory
    assert str(child) in result
    # descent stops at child
    assert str(grandchild) not in result


def test_get_group_subdirectories_reports_project_subdirs_not_owned_dir(tmp_path):
    """Project subdirs of an owned dir are reported when the owned dir has only a dotfile.

    This is the primary bug scenario: an owned directory that contains only a
    hidden dot-file (e.g. ``.gitconfig``) alongside project subdirectories
    should report the project subdirectories, not the owned directory itself.
    A hidden dot-file is not treated as a visible file and therefore does not
    halt descent.
    """
    current_user = pwd.getpwuid(os.getuid()).pw_name
    members = {current_user}

    parent = tmp_path / "parent"
    parent.mkdir()
    owned = parent / "owned"
    owned.mkdir()
    (owned / ".gitconfig").write_text("hidden config")
    project1 = owned / "project1"
    project1.mkdir()
    (project1 / "data.csv").write_text("data")
    project2 = owned / "project2"
    project2.mkdir()
    (project2 / "results.txt").write_text("results")

    result = get_group_subdirectories(str(parent), members, ignore_patterns=[".*"])
    assert str(project1) in result
    assert str(project2) in result
    assert str(owned) not in result


# ---------------------------------------------------------------------------
# _find_reporting_directories
# ---------------------------------------------------------------------------


def test_find_reporting_directories_with_file(tmp_path):
    """A directory containing a visible file but no subdirs is returned as-is (leaf)."""
    d = tmp_path / "dir"
    d.mkdir()
    (d / "file.txt").write_text("hello")

    result = _find_reporting_directories(str(d))
    assert result == [str(d)]


def test_find_reporting_directories_with_only_dotfile_no_subdirs(tmp_path):
    """A directory containing only a hidden dot-file and no subdirs is returned as-is."""
    d = tmp_path / "dir"
    d.mkdir()
    (d / ".hidden").write_text("hidden")

    result = _find_reporting_directories(str(d), ignore_patterns=[".*"])
    assert result == [str(d)]


def test_find_reporting_directories_visible_file_stops_descent(tmp_path):
    """A visible file in a directory stops descent even when subdirectories also exist."""
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "visible.txt").write_text("visible")
    child = parent / "child"
    child.mkdir()

    result = _find_reporting_directories(str(parent))
    assert str(parent) in result
    assert str(child) not in result


def test_find_reporting_directories_empty_dir(tmp_path):
    """An empty directory (no files, no subdirs) is returned as a reporting directory."""
    d = tmp_path / "empty"
    d.mkdir()

    result = _find_reporting_directories(str(d))
    assert result == [str(d)]


def test_find_reporting_directories_descends_through_dir_only(tmp_path):
    """A directory with only subdirs is not returned; descent continues."""
    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child"
    child.mkdir()
    (child / "data.txt").write_text("data")

    result = _find_reporting_directories(str(parent))
    assert str(parent) not in result
    assert str(child) in result


def test_find_reporting_directories_multi_level_descent(tmp_path):
    """Descent continues through multiple levels of dir-only directories."""
    a = tmp_path / "a"
    a.mkdir()
    b = a / "b"
    b.mkdir()
    c = b / "c"
    c.mkdir()
    (c / "file.txt").write_text("x")

    result = _find_reporting_directories(str(a))
    assert result == [str(c)]


def test_find_reporting_directories_multiple_subdirs(tmp_path):
    """Each subdirectory branch is reported independently."""
    root = tmp_path / "root"
    root.mkdir()
    branch1 = root / "branch1"
    branch1.mkdir()
    (branch1 / "f.txt").write_text("a")
    branch2 = root / "branch2"
    branch2.mkdir()
    leaf = branch2 / "leaf"
    leaf.mkdir()
    (leaf / "g.txt").write_text("b")

    result = _find_reporting_directories(str(root))
    assert str(branch1) in result
    assert str(leaf) in result
    assert str(branch2) not in result


def test_find_reporting_directories_with_dotfile_and_subdirs(tmp_path):
    """A hidden dot-file in a directory does not stop descent into its subdirectories.

    Structure:
        owned/
          .hidden_file     <- dot-file (hidden) directly in owned dir
          project1/
            data.csv       <- leaf with files
          project2/
            results.txt    <- leaf with files

    Expected: project1 and project2 are reported; owned is not.
    A hidden file is not treated as a visible file and therefore does not
    halt the descent into subdirectories.
    """
    owned = tmp_path / "owned"
    project1 = owned / "project1"
    project2 = owned / "project2"
    for d in (project1, project2):
        d.mkdir(parents=True)
    (owned / ".hidden_file").write_text("hidden")
    (project1 / "data.csv").write_text("data")
    (project2 / "results.txt").write_text("results")

    result = _find_reporting_directories(str(owned), ignore_patterns=[".*"])

    assert str(project1) in result
    assert str(project2) in result
    assert str(owned) not in result


def test_find_reporting_directories_files_and_subdir(tmp_path):
    """A directory with visible files is a reporting directory even if it has subdirectories.

    Structure:
        dir0/
          dir01/
            file010.txt
            file011.txt
            dir012/           <- NOT reported (dir01 has visible files, stops here)
          dir02/
            dir020/
              file0201.txt    <- leaf, IS reported

    Expected: dir01 and dir020 are reported; dir0, dir02, and dir012 are not.
    """
    dir0 = tmp_path / "dir0"
    dir01 = dir0 / "dir01"
    dir012 = dir01 / "dir012"
    dir02 = dir0 / "dir02"
    dir020 = dir02 / "dir020"

    for d in (dir012, dir020):
        d.mkdir(parents=True)

    (dir01 / "file010.txt").write_text("a")
    (dir01 / "file011.txt").write_text("b")
    (dir020 / "file0201.txt").write_text("c")

    result = _find_reporting_directories(str(dir0))

    assert str(dir01) in result
    assert str(dir020) in result
    assert str(dir0) not in result
    assert str(dir02) not in result
    assert str(dir012) not in result


# ---------------------------------------------------------------------------
# main – group from config file
# ---------------------------------------------------------------------------


def test_main_group_from_config(tmp_path, capsys):
    """main reads the groups from the config file when --groups is not supplied."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user_subdir = data_dir / "user_dir"
    user_subdir.mkdir()
    (user_subdir / "file.txt").write_text("content")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"paths:\n  - {data_dir}\ngroups:\n  - {group_name}\n")

    main(str(config_file))  # no groups kwarg; should come from config
    captured = capsys.readouterr()
    assert str(user_subdir) in captured.out
    assert group_name in captured.out


def test_main_cli_group_overrides_config(tmp_path, capsys):
    """CLI --groups takes precedence over the groups key in the config file."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user_subdir = data_dir / "user_dir"
    user_subdir.mkdir()
    (user_subdir / "file.txt").write_text("content")

    config_file = tmp_path / "config.yaml"
    # Config contains a bogus group; the CLI groups should win.
    config_file.write_text(f"paths:\n  - {data_dir}\ngroups:\n  - __bogus_group__\n")

    main(str(config_file), groups=[group_name])
    captured = capsys.readouterr()
    assert str(user_subdir) in captured.out
    assert group_name in captured.out


def test_main_with_group_invalid_parent_dir(tmp_path, capsys):
    """A configured directory that does not exist emits a warning and is skipped."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    config_file = tmp_path / "config.yaml"
    config_file.write_text("paths:\n  - /nonexistent/path/xyz\n")

    main(str(config_file), groups=[group_name])
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
    config_file.write_text(f"paths:\n  - {data_dir}\n")

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
    config_file.write_text(f"paths:\n  - {data_dir}\n")

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
    config_file.write_text(f"paths:\n  - {data_dir}\n")

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
    config_file.write_text(f"paths:\n  - {data_dir}\n")

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
    """_build_directory_entry includes the groups key when groups are supplied."""
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name
    (tmp_path / "f.txt").write_text("data")
    entry = _build_directory_entry(str(tmp_path), groups=[group_name])
    assert entry is not None
    assert entry["groups"] == [group_name]


def test_build_directory_entry_group_does_not_filter_stats(tmp_path):
    """_build_directory_entry counts all files even when groups are supplied.

    groups is used only as a display label on the entry; it must not be
    forwarded to get_directory_stats as a file-ownership filter.  The
    reporting directory has already been selected by group ownership, so all
    files inside it – regardless of who owns them – should be counted.
    """
    current_gid = os.getgid()
    group_name = grp.getgrgid(current_gid).gr_name

    # Two files: both owned by the current user (who is in group_name).
    # The total should be 2 regardless of the groups label.
    (tmp_path / "file1.txt").write_text("hello")
    (tmp_path / "file2.txt").write_text("world")

    # Call with groups= (display label only) and without groups= (baseline).
    entry_with_group = _build_directory_entry(str(tmp_path), groups=[group_name])
    entry_no_group = _build_directory_entry(str(tmp_path))

    assert entry_with_group is not None
    assert entry_no_group is not None
    # Stats must be identical regardless of whether groups is supplied.
    assert entry_with_group["file_count"] == entry_no_group["file_count"]
    assert entry_with_group["total_size"] == entry_no_group["total_size"]
    assert entry_with_group["file_count"] == 2


# ---------------------------------------------------------------------------
# _is_ignored
# ---------------------------------------------------------------------------


def test_is_ignored_matches_dot_star():
    """_is_ignored returns True for names matching '.*'."""
    assert _is_ignored(".hidden", [".*"]) is True
    assert _is_ignored(".gitignore", [".*"]) is True


def test_is_ignored_no_match():
    """_is_ignored returns False when no pattern matches."""
    assert _is_ignored("visible.txt", [".*"]) is False


def test_is_ignored_wildcard_extension():
    """_is_ignored matches a wildcard extension pattern."""
    assert _is_ignored("backup.tmp", ["*.tmp"]) is True
    assert _is_ignored("notes.txt", ["*.tmp"]) is False


def test_is_ignored_empty_patterns():
    """_is_ignored returns False for any name when the pattern list is empty."""
    assert _is_ignored(".hidden", []) is False
    assert _is_ignored("visible.txt", []) is False


def test_is_ignored_multiple_patterns():
    """_is_ignored returns True when any pattern matches."""
    assert _is_ignored(".hidden", [".*", "*.tmp"]) is True
    assert _is_ignored("file.tmp", [".*", "*.tmp"]) is True
    assert _is_ignored("normal.txt", [".*", "*.tmp"]) is False


# ---------------------------------------------------------------------------
# _find_reporting_directories with ignore_patterns
# ---------------------------------------------------------------------------


def test_find_reporting_directories_ignores_matched_dir(tmp_path):
    """A directory matching ignore_patterns is not descended or returned."""
    root = tmp_path / "root"
    root.mkdir()
    ignored_dir = root / ".cache"
    ignored_dir.mkdir()
    (ignored_dir / "data.txt").write_text("should be ignored")
    regular_dir = root / "work"
    regular_dir.mkdir()
    (regular_dir / "data.txt").write_text("visible")

    result = _find_reporting_directories(str(root), ignore_patterns=[".*"])
    assert str(regular_dir) in result
    assert str(ignored_dir) not in result


def test_find_reporting_directories_dotfile_treated_visible_without_patterns(tmp_path):
    """Without ignore_patterns, a dot-file is treated as a visible file and stops descent."""
    d = tmp_path / "dir"
    d.mkdir()
    (d / ".hidden").write_text("hidden")
    sub = d / "sub"
    sub.mkdir()
    (sub / "data.txt").write_text("data")

    # Without patterns: dot-file is visible → dir itself is the reporting directory.
    result = _find_reporting_directories(str(d))
    assert str(d) in result
    assert str(sub) not in result


def test_find_reporting_directories_ignores_matched_file(tmp_path):
    """A file matching ignore_patterns is not treated as a visible file."""
    d = tmp_path / "dir"
    d.mkdir()
    (d / ".hidden").write_text("ignored by pattern")
    sub = d / "sub"
    sub.mkdir()
    (sub / "data.txt").write_text("data")

    # With patterns: .hidden is ignored → descent into sub occurs.
    result = _find_reporting_directories(str(d), ignore_patterns=[".*"])
    assert str(sub) in result
    assert str(d) not in result


# ---------------------------------------------------------------------------
# get_group_subdirectories with ignore_patterns
# ---------------------------------------------------------------------------


def test_get_group_subdirectories_skips_ignored_subdir(tmp_path):
    """A first-level subdirectory matching ignore_patterns is not returned."""
    current_user = pwd.getpwuid(os.getuid()).pw_name
    members = {current_user}

    parent = tmp_path / "parent"
    parent.mkdir()
    hidden_sub = parent / ".hidden_sub"
    hidden_sub.mkdir()
    (hidden_sub / "data.txt").write_text("data")
    visible_sub = parent / "visible_sub"
    visible_sub.mkdir()
    (visible_sub / "data.txt").write_text("data")

    result = get_group_subdirectories(str(parent), members, ignore_patterns=[".*"])
    assert str(visible_sub) in result
    assert str(hidden_sub) not in result


# ---------------------------------------------------------------------------
# get_directory_stats with ignore_patterns
# ---------------------------------------------------------------------------


def test_get_directory_stats_skips_ignored_files(tmp_path):
    """Files matching ignore_patterns are not counted in stats."""
    (tmp_path / "visible.txt").write_text("hello")   # 5 bytes
    (tmp_path / ".hidden").write_text("world!")      # 6 bytes – should be ignored

    stats = get_directory_stats(str(tmp_path), ignore_patterns=[".*"])
    assert stats["file_count"] == 1
    assert stats["total_size"] == 5


def test_get_directory_stats_skips_ignored_dirs(tmp_path):
    """Directories matching ignore_patterns are not descended for stats."""
    ignored_dir = tmp_path / ".cache"
    ignored_dir.mkdir()
    (ignored_dir / "big.dat").write_text("should not count")

    (tmp_path / "visible.txt").write_text("hi")

    stats = get_directory_stats(str(tmp_path), ignore_patterns=[".*"])
    assert stats["file_count"] == 1


def test_get_directory_stats_no_patterns_counts_all(tmp_path):
    """Without ignore_patterns, all files (including hidden) are counted."""
    (tmp_path / "visible.txt").write_text("hello")
    (tmp_path / ".hidden").write_text("world!")

    stats = get_directory_stats(str(tmp_path))
    assert stats["file_count"] == 2


# ---------------------------------------------------------------------------
# main – ignore patterns from config
# ---------------------------------------------------------------------------


def test_main_config_ignore_excludes_hidden_files(tmp_path, capsys):
    """main applies ignore patterns from the config's ignore key."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "visible.txt").write_text("hello")
    (data_dir / ".hidden").write_text("secret")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"paths:\n  - {data_dir}\nignore:\n  - '.*'\n")

    main(str(config_file))
    captured = capsys.readouterr()
    # Only visible.txt should be counted (1 file).
    assert "Files     : 1" in captured.out


def test_main_config_ignore_excludes_dirs(tmp_path, capsys):
    """main does not descend into directories matching the config ignore patterns."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ignored = data_dir / ".git"
    ignored.mkdir()
    (ignored / "config").write_text("git config")
    (data_dir / "readme.txt").write_text("hi")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"paths:\n  - {data_dir}\nignore:\n  - '.*'\n")

    main(str(config_file))
    captured = capsys.readouterr()
    # Only readme.txt should be counted (1 file), .git directory skipped.
    assert "Files     : 1" in captured.out


def test_main_config_ignore_wildcard_extension(tmp_path, capsys):
    """main excludes files matching a wildcard extension pattern in config ignore."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "visible.txt").write_text("hello")
    (data_dir / "unwanted.tmp").write_text("junk")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"paths:\n  - {data_dir}\nignore:\n  - '*.tmp'\n")

    main(str(config_file))
    captured = capsys.readouterr()
    # Only visible.txt should be counted.
    assert "Files     : 1" in captured.out


def test_main_no_ignore_key_counts_all_files(tmp_path, capsys):
    """Without an ignore key in config, all files (including hidden) are counted."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "visible.txt").write_text("hello")
    (data_dir / ".hidden").write_text("world!")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"paths:\n  - {data_dir}\n")

    main(str(config_file))
    captured = capsys.readouterr()
    assert "Files     : 2" in captured.out


# ---------------------------------------------------------------------------
# _run_du
# ---------------------------------------------------------------------------


def test_run_du_returns_files_and_dirs(tmp_path):
    """_run_du with all_files=True returns both file and directory entries."""
    (tmp_path / "a.txt").write_text("hello")  # 5 bytes
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world!")  # 6 bytes

    entries = _run_du(str(tmp_path), [], all_files=True)
    paths = [p for _, p in entries]
    assert any("a.txt" in p for p in paths)
    assert any("b.txt" in p for p in paths)
    assert any(str(tmp_path) == p for p in paths)  # root directory itself


def test_run_du_dirs_only_excludes_regular_files(tmp_path):
    """_run_du with all_files=False lists only directories, not regular files."""
    (tmp_path / "a.txt").write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world!")

    entries = _run_du(str(tmp_path), [], all_files=False)
    paths = [p for _, p in entries]
    # Regular files must not appear in the dirs-only output.
    assert not any("a.txt" in p for p in paths)
    assert not any("b.txt" in p for p in paths)
    # Directories must appear.
    assert any(str(sub) == p for p in paths)
    assert any(str(tmp_path) == p for p in paths)


def test_run_du_sizes_match_lstat(tmp_path):
    """_run_du apparent sizes match os.lstat().st_size for regular files."""
    content = "hello world"  # 11 bytes
    f = tmp_path / "file.txt"
    f.write_text(content)

    entries = _run_du(str(tmp_path), [], all_files=True)
    file_entry = next(((s, p) for s, p in entries if "file.txt" in p), None)
    assert file_entry is not None
    size_from_du, _ = file_entry
    assert size_from_du == f.lstat().st_size


def test_run_du_respects_exclude_patterns(tmp_path):
    """_run_du honours shell-style exclude patterns."""
    (tmp_path / "visible.txt").write_text("hello")
    (tmp_path / ".hidden").write_text("secret")
    hidden_dir = tmp_path / ".cache"
    hidden_dir.mkdir()
    (hidden_dir / "data").write_text("cached")

    entries = _run_du(str(tmp_path), [".*"], all_files=True)
    paths = [p for _, p in entries]
    assert not any(".hidden" in p for p in paths)
    assert not any(".cache" in p for p in paths)
    assert any("visible.txt" in p for p in paths)


def test_run_du_nonexistent_path(tmp_path):
    """_run_du returns an empty list (no exception) for a nonexistent path."""
    result = _run_du(str(tmp_path / "does_not_exist"), [])
    assert result == []


def test_run_du_empty_directory(tmp_path):
    """_run_du on an empty directory returns exactly one entry for the root."""
    entries = _run_du(str(tmp_path), [], all_files=True)
    assert len(entries) == 1
    size, path = entries[0]
    assert path == str(tmp_path)
    assert size == 0


# ---------------------------------------------------------------------------
# get_directory_stats – no-execute subdirectory
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.getuid() == 0, reason="root bypasses permission checks")
def test_get_directory_stats_no_execute_subdir(tmp_path):
    """Stats for accessible files are still reported when a subdir lacks execute permission.

    When a subdirectory has read-only permission (no execute), the tool cannot
    stat individual files inside it.  The files that ARE accessible (in other
    subdirectories and at the top level) must still be counted correctly.
    """
    # Accessible file at the top level.
    accessible = tmp_path / "accessible.txt"
    accessible.write_text("hello")  # 5 bytes

    # Subdirectory that will have its execute bit removed.
    restricted = tmp_path / "restricted"
    restricted.mkdir()
    (restricted / "secret.txt").write_text("secret data")  # 11 bytes

    # Remove execute permission from the restricted subdirectory.
    restricted.chmod(0o444)

    try:
        stats = get_directory_stats(str(tmp_path))
        # The top-level accessible file must be counted.
        # Files inside the restricted dir are inaccessible to both du and
        # Python's stat(), so they may or may not be counted; the important
        # thing is that the function does not raise and returns the files it
        # can see.
        assert stats["file_count"] >= 1
        assert stats["total_size"] >= accessible.lstat().st_size
    finally:
        # Restore permissions so tmp_path cleanup succeeds.
        restricted.chmod(0o755)


# ---------------------------------------------------------------------------
# _load_ontrack_yml
# ---------------------------------------------------------------------------


def test_load_ontrack_yml_valid_dict(tmp_path):
    """_load_ontrack_yml returns a dict for a well-formed YAML mapping."""
    store = tmp_path / "ontrack.yml"
    store.write_text("project1:\n  track: rna-seq\n  owner: alice\n  created: '2024-01-01'\n")
    result = _load_ontrack_yml(store)
    assert isinstance(result, dict)
    assert "project1" in result
    assert result["project1"]["owner"] == "alice"


def test_load_ontrack_yml_absent(tmp_path):
    """_load_ontrack_yml returns None when the file does not exist."""
    result = _load_ontrack_yml(tmp_path / "ontrack.yml")
    assert result is None


def test_load_ontrack_yml_not_a_dict(tmp_path):
    """_load_ontrack_yml returns None when the YAML top level is not a mapping."""
    store = tmp_path / "ontrack.yml"
    store.write_text("- item1\n- item2\n")
    result = _load_ontrack_yml(store)
    assert result is None


def test_load_ontrack_yml_unreadable(tmp_path):
    """_load_ontrack_yml returns None and does not raise when the file is unreadable."""
    store = tmp_path / "ontrack.yml"
    store.write_text("project1:\n  track: rna-seq\n")
    store.chmod(0o000)
    try:
        result = _load_ontrack_yml(store)
        assert result is None
    finally:
        store.chmod(0o644)


# ---------------------------------------------------------------------------
# _is_on_track
# ---------------------------------------------------------------------------


def test_is_on_track_all_required_fields():
    """_is_on_track returns True when all required fields are present and non-empty."""
    metadata = {"track": "rna-seq", "owner": "alice", "created": "2024-01-01"}
    assert _is_on_track(metadata) is True


def test_is_on_track_missing_one_field():
    """_is_on_track returns False when a required field is absent."""
    metadata = {"track": "rna-seq", "owner": "alice"}  # missing 'created'
    assert _is_on_track(metadata) is False


def test_is_on_track_empty_string_field():
    """_is_on_track returns False when a required field is an empty string."""
    metadata = {"track": "", "owner": "alice", "created": "2024-01-01"}
    assert _is_on_track(metadata) is False


def test_is_on_track_none_input():
    """_is_on_track returns False when metadata is None."""
    assert _is_on_track(None) is False


def test_is_on_track_extra_fields_ignored():
    """_is_on_track returns True when extra fields are present alongside required ones."""
    metadata = {
        "track": "rna-seq",
        "owner": "alice",
        "created": "2024-01-01",
        "pi": "Dr. Smith",
        "grant": "NIH-12345",
    }
    assert _is_on_track(metadata) is True


def test_is_on_track_valid_track_in_valid_tracks():
    """_is_on_track returns True when track value is in valid_tracks."""
    metadata = {"track": "rna-seq", "owner": "alice", "created": "2024-01-01"}
    assert _is_on_track(metadata, valid_tracks={"rna-seq", "cnv-pipeline"}) is True


def test_is_on_track_invalid_track_not_in_valid_tracks():
    """_is_on_track returns False when track value is not in valid_tracks."""
    metadata = {"track": "unknown-track", "owner": "alice", "created": "2024-01-01"}
    assert _is_on_track(metadata, valid_tracks={"rna-seq", "cnv-pipeline"}) is False


def test_is_on_track_no_valid_tracks_skips_track_validation():
    """_is_on_track skips track validation when valid_tracks is None."""
    metadata = {"track": "any-track", "owner": "alice", "created": "2024-01-01"}
    assert _is_on_track(metadata, valid_tracks=None) is True


# ---------------------------------------------------------------------------
# _get_directory_metadata
# ---------------------------------------------------------------------------


def test_get_directory_metadata_entry_present(tmp_path):
    """_get_directory_metadata returns the correct entry when the directory is in the store."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "project1"
    project.mkdir()
    store = parent / "ontrack.yml"
    store.write_text("project1:\n  track: rna-seq\n  owner: alice\n  created: '2024-01-01'\n")

    result = _get_directory_metadata(str(project))
    assert result is not None
    assert result["owner"] == "alice"
    assert result["track"] == "rna-seq"


def test_get_directory_metadata_dir_not_in_store(tmp_path):
    """_get_directory_metadata returns None when the directory is not mentioned in the store."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "project2"
    project.mkdir()
    store = parent / "ontrack.yml"
    store.write_text("project1:\n  track: rna-seq\n  owner: alice\n  created: '2024-01-01'\n")

    result = _get_directory_metadata(str(project))
    assert result is None


def test_get_directory_metadata_no_store(tmp_path):
    """_get_directory_metadata returns None when no ontrack.yml exists in the parent."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "project1"
    project.mkdir()

    result = _get_directory_metadata(str(project))
    assert result is None


# ---------------------------------------------------------------------------
# _find_reporting_directories – ontrack.yml handling
# ---------------------------------------------------------------------------


def test_find_reporting_directories_ignores_ontrack_yml_as_visible_file(tmp_path):
    """A directory containing only ontrack.yml is not treated as a reporting directory.

    ontrack.yml should not count as a visible file; descent should continue
    into subdirectories (or the directory itself returned as fallback when empty).
    """
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "ontrack.yml").write_text("project1:\n  track: rna-seq\n")
    child = parent / "project1"
    child.mkdir()
    (child / "data.txt").write_text("data")

    result = _find_reporting_directories(str(parent))
    # ontrack.yml present → child is returned as the reporting directory
    assert str(child) in result
    assert str(parent) not in result


def test_find_reporting_directories_ontrack_yml_reports_all_subdirs(tmp_path):
    """When ontrack.yml is present, all non-ignored subdirs become reporting directories."""
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "ontrack.yml").write_text("p1:\n  x: 1\np2:\n  x: 2\n")
    p1 = parent / "p1"
    p1.mkdir()
    p2 = parent / "p2"
    p2.mkdir()

    result = _find_reporting_directories(str(parent))
    assert str(p1) in result
    assert str(p2) in result
    assert str(parent) not in result


def test_find_reporting_directories_ontrack_yml_no_subdirs_fallback(tmp_path):
    """When ontrack.yml is present but there are no subdirs, the directory itself is returned."""
    d = tmp_path / "dir"
    d.mkdir()
    (d / "ontrack.yml").write_text("nothing:\n  x: 1\n")

    result = _find_reporting_directories(str(d))
    assert result == [str(d)]


def test_find_reporting_directories_ontrack_yml_ignores_ignored_subdirs(tmp_path):
    """Subdirs matching ignore_patterns are excluded even when ontrack.yml is present."""
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "ontrack.yml").write_text("visible:\n  x: 1\n")
    visible = parent / "visible"
    visible.mkdir()
    hidden = parent / ".hidden"
    hidden.mkdir()

    result = _find_reporting_directories(str(parent), ignore_patterns=[".*"])
    assert str(visible) in result
    assert str(hidden) not in result


# ---------------------------------------------------------------------------
# _build_directory_entry – on_track and metadata
# ---------------------------------------------------------------------------


def test_build_directory_entry_on_track(tmp_path):
    """_build_directory_entry sets on_track=True and includes metadata when fully populated."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "project1"
    project.mkdir()
    (parent / "ontrack.yml").write_text(
        "project1:\n  track: rna-seq\n  owner: alice\n  created: '2024-01-15'\n"
    )

    entry = _build_directory_entry(str(project))
    assert entry is not None
    assert entry["on_track"] is True
    assert "metadata" in entry
    assert entry["metadata"]["owner"] == "alice"


def test_build_directory_entry_on_track_with_valid_tracks(tmp_path):
    """_build_directory_entry sets on_track=True when track value is in valid_tracks."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "project1"
    project.mkdir()
    (parent / "ontrack.yml").write_text(
        "project1:\n  track: rna-seq\n  owner: alice\n  created: '2024-01-15'\n"
    )

    entry = _build_directory_entry(str(project), valid_tracks={"rna-seq", "cnv-pipeline"})
    assert entry is not None
    assert entry["on_track"] is True


def test_build_directory_entry_not_on_track_invalid_track(tmp_path):
    """_build_directory_entry sets on_track=False when track value is not in valid_tracks."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "project1"
    project.mkdir()
    (parent / "ontrack.yml").write_text(
        "project1:\n  track: unknown\n  owner: alice\n  created: '2024-01-15'\n"
    )

    entry = _build_directory_entry(str(project), valid_tracks={"rna-seq", "cnv-pipeline"})
    assert entry is not None
    assert entry["on_track"] is False


def test_build_directory_entry_not_on_track_missing_fields(tmp_path):
    """_build_directory_entry sets on_track=False when required metadata fields are missing."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "project1"
    project.mkdir()
    (parent / "ontrack.yml").write_text(
        "project1:\n  track: rna-seq\n"  # missing owner and created
    )

    entry = _build_directory_entry(str(project))
    assert entry is not None
    assert entry["on_track"] is False
    assert "metadata" in entry


def test_build_directory_entry_not_on_track_not_in_store(tmp_path):
    """_build_directory_entry sets on_track=False when the directory is not in the store."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "project1"
    project.mkdir()
    (parent / "ontrack.yml").write_text("other_project:\n  track: rna-seq\n  owner: bob\n  created: '2024-01-01'\n")

    entry = _build_directory_entry(str(project))
    assert entry is not None
    assert entry["on_track"] is False
    assert "metadata" not in entry


def test_build_directory_entry_on_track_false_no_store(tmp_path):
    """_build_directory_entry sets on_track=False and no metadata when no store exists."""
    entry = _build_directory_entry(str(tmp_path))
    assert entry is not None
    assert entry["on_track"] is False
    assert "metadata" not in entry


# ---------------------------------------------------------------------------
# report_directory – on_track printing
# ---------------------------------------------------------------------------


def test_report_directory_prints_on_track_yes(tmp_path, capsys):
    """report_directory prints 'On track  : Yes' when metadata is fully populated."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "myproject"
    project.mkdir()
    (parent / "ontrack.yml").write_text(
        "myproject:\n  track: rna-seq\n  owner: alice\n  created: '2024-06-01'\n"
    )

    report_directory(str(project))
    captured = capsys.readouterr()
    assert "On track  : Yes" in captured.out
    assert "Track" in captured.out
    assert "rna-seq" in captured.out
    assert "Owner" in captured.out
    assert "alice" in captured.out


def test_report_directory_prints_on_track_no(tmp_path, capsys):
    """report_directory prints 'On track  : No' when no metadata store is present."""
    report_directory(str(tmp_path))
    captured = capsys.readouterr()
    assert "On track  : No" in captured.out


def test_report_directory_prints_extra_metadata_fields(tmp_path, capsys):
    """report_directory prints extra metadata fields beyond the required three."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = parent / "proj"
    project.mkdir()
    (parent / "ontrack.yml").write_text(
        "proj:\n"
        "  track: rna-seq\n"
        "  owner: bob\n"
        "  created: '2025-01-01'\n"
        "  grant: NIH-12345\n"
    )

    report_directory(str(project))
    captured = capsys.readouterr()
    assert "Grant" in captured.out
    assert "NIH-12345" in captured.out
