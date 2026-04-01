#!/usr/bin/env python3
"""Report directory statistics for locations specified in a config YAML file.

Two operating modes are supported:

* **Group mode** (``--groups`` supplied or ``groups:`` set in the config file):
  For each configured directory, the script finds subdirectories owned by
  users who belong to the specified Unix group and reports stats for each of
  those subdirectories.  Only the immediate children are checked for
  ownership; once an owned subdirectory is identified, the script descends
  further into it until it reaches a directory that contains at least one
  visible file (a file whose name is not matched by any ``ignore`` pattern),
  which is the *reporting directory*.  A directory that contains only ignored
  files or only subdirectories is traversed further; an empty directory is
  used as-is.

* **Default mode** (no group specified):
  Stats are reported directly for each configured directory.

Each report includes:
  - Directory path
  - Owning username
  - Number of files (unless ``--light`` is given)
  - Total size (unless ``--light`` is given)

Optional flags
--------------
``--progress``
    Show tqdm progress bars while scanning.  Off by default.
``--light``
    Skip file-count and size scanning; only report directory and owner.
``--output <file>``
    Write the report as YAML to *file*; otherwise print to stdout.

Config file keys
----------------
``ignore``
    A YAML list of shell-style glob patterns.  Files and directories whose
    base names match any pattern are excluded from all scans.  Example::

        ignore:
          - '.*'
          - '*.tmp'
"""

import argparse
import fnmatch
import functools
import grp
import logging
import os
import pathlib
import pwd
import subprocess
import sys

import yaml
from tqdm import tqdm

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=None)
def _uid_to_username(uid: int) -> str:
    """Return the username for a UID, falling back to the numeric string.

    Results are cached so repeated lookups for the same UID are free.
    """
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def get_username(path: str) -> str:
    """Return the username of the directory owner.

    Returns an empty string if the path cannot be stat'd (``OSError``) or
    the owner UID has no corresponding password-database entry (``KeyError``).
    """
    try:
        return pathlib.Path(path).owner()
    except (OSError, KeyError):
        return ""


def get_group_members(group_name: str) -> set[str]:
    """Return the set of usernames belonging to the given Unix group.

    Includes both secondary members listed in the group database and users
    whose primary GID matches the group.
    """
    try:
        group_info = grp.getgrnam(group_name)
    except KeyError:
        raise ValueError(f"Group '{group_name}' not found.")

    members: set[str] = set(group_info.gr_mem)

    # Also include users for whom this group is their primary group.
    gid = group_info.gr_gid
    for pw_entry in pwd.getpwall():
        if pw_entry.pw_gid == gid:
            members.add(pw_entry.pw_name)

    return members


def _find_reporting_directories(
    directory: str, ignore_patterns: list[str] | None = None
) -> list[str]:
    """Return reporting directories within *directory*.

    A directory is a *reporting directory* if it contains at least one visible
    file — a file whose name is not matched by any pattern in *ignore_patterns*.
    If *directory* contains only ignored files and subdirectories, or contains
    no files at all, the search recurses into each non-ignored subdirectory.  An
    empty directory (no files, no subdirectories) is itself treated as a
    reporting directory.  Entries that cannot be stat'd are silently skipped.
    Subdirectories whose names match *ignore_patterns* are not descended into
    and are not considered reporting directories.

    Args:
        directory: Path to the directory to inspect.
        ignore_patterns: A list of shell-style glob patterns (see
            :func:`_is_ignored`).  Files and directories whose base names match
            any pattern are ignored.  ``None`` is treated as an empty list.
    """
    patterns: list[str] = ignore_patterns or []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return []

    subdirs: list[str] = []
    has_visible_file = False
    for entry in entries:
        try:
            if entry.is_file():
                if not _is_ignored(entry.name, patterns):
                    has_visible_file = True
                    break  # no need to scan further; this dir is already a reporting dir
            elif entry.is_dir(follow_symlinks=False):
                if not _is_ignored(entry.name, patterns):
                    subdirs.append(entry.path)
        except OSError:
            pass

    if has_visible_file or not subdirs:
        # Contains at least one visible file, or is an empty/dot-files-only
        # leaf → this is the reporting directory.
        return [directory]

    # Only subdirectories (and possibly hidden files) found → recurse.
    result: list[str] = []
    for subdir in subdirs:
        result.extend(_find_reporting_directories(subdir, patterns))
    # Fall back to the current directory if all recursive calls returned nothing
    # (e.g. every subdirectory raised OSError and could not be scanned).
    return result if result else [directory]


def get_group_subdirectories(
    parent_dir: str,
    group_members: set[str],
    ignore_patterns: list[str] | None = None,
) -> list[str]:
    """Return reporting subdirectories of *parent_dir* owned by any user in *group_members*.

    Only the immediate children of *parent_dir* are checked for ownership.
    For each owned subdirectory, if it contains at least one visible file (a
    file whose name is not matched by *ignore_patterns*) it is returned directly
    as a reporting directory.  If it contains only ignored files or only
    subdirectories (no visible files), the search recurses further until a
    directory with visible files or an empty leaf directory is reached.
    Subdirectories whose names match *ignore_patterns* are skipped entirely.
    Entries that cannot be stat'd are silently skipped.

    Args:
        parent_dir: Path to the parent directory whose immediate children are
            inspected.
        group_members: Set of usernames; only subdirectories owned by a user in
            this set are considered.
        ignore_patterns: Shell-style glob patterns passed to :func:`_is_ignored`.
            Subdirectories matching any pattern are skipped.  ``None`` is
            treated as an empty list.
    """
    patterns: list[str] = ignore_patterns or []
    result: list[str] = []
    try:
        entries = sorted(os.scandir(parent_dir), key=lambda e: e.name)
    except OSError:
        return result
    for entry in entries:
        try:
            if (
                entry.is_dir(follow_symlinks=False)
                and not _is_ignored(entry.name, patterns)
                and get_username(entry.path) in group_members
            ):
                result.extend(_find_reporting_directories(entry.path, patterns))
        except OSError:
            pass
    return result


def _run_du(
    path: str,
    patterns: list[str],
    all_files: bool = False,
) -> list[tuple[int, str]]:
    """Run ``du(1)`` and return a list of ``(size_bytes, path)`` pairs.

    Uses ``--apparent-size`` and ``--block-size=1`` to report byte-accurate
    apparent file sizes, matching ``os.stat().st_size``.  When *all_files* is
    ``False`` (the default) only directory entries are emitted by ``du``; when
    ``True`` both regular files and directories are included via the ``-a``
    flag.

    Args:
        path: Root directory to pass to ``du``.
        patterns: Shell-style glob patterns forwarded as ``--exclude`` options
            to ``du``.  Matching files and directories are omitted from the
            output.
        all_files: Pass ``-a`` to ``du`` so that regular files are included
            in the output in addition to directories.

    Returns:
        A list of ``(size_in_bytes, absolute_path)`` tuples parsed from
        ``du`` stdout, or an empty list if ``du`` cannot be executed or
        produces no parsable output.
    """
    cmd = ["du", "--apparent-size", "--block-size=1"]
    if all_files:
        cmd.append("-a")
    for pattern in patterns:
        cmd.extend(["--exclude", pattern])
    cmd.append(path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            logger.warning(
                "du exited with code %d for %s: %s",
                proc.returncode,
                path,
                proc.stderr.strip(),
            )
        result: list[tuple[int, str]] = []
        for line in proc.stdout.splitlines():
            if "\t" not in line:
                continue
            size_str, entry_path = line.split("\t", 1)
            try:
                result.append((int(size_str), entry_path))
            except ValueError:
                logger.debug("Could not parse du output line: %r", line)
        return result
    except OSError as exc:
        logger.warning("Could not run du for %s: %s", path, exc)
        return []


def get_directory_stats(
    path: str,
    groups: list[str] | None = None,
    show_progress: bool = False,
    ignore_patterns: list[str] | None = None,
) -> dict:
    """Return file count and total size (bytes) for a directory tree.

    Uses ``du(1)`` for directory traversal, which correctly handles
    directories where the calling user lacks execute permission and is
    significantly faster than Python-level ``os.walk`` on large trees or
    network filesystems.  Directories and files whose base names match any
    pattern in *ignore_patterns* are excluded via ``du``'s ``--exclude``
    option.

    If *groups* is given, only files owned by users belonging to any of those
    Unix groups are counted.  Ownership is determined via ``lstat``; files
    whose ownership cannot be determined are included in the totals.

    Args:
        path: Root of the directory tree to scan.
        groups: Optional list of Unix group names; when supplied only files
            owned by members of these groups are included in the counts.
        show_progress: Accepted for API compatibility; has no effect because
            ``du`` completes the scan in a single subprocess call.
        ignore_patterns: Shell-style glob patterns (see :func:`_is_ignored`).
            Matched directories are not descended into; matched files are not
            counted.  ``None`` is treated as an empty list.
    """
    patterns: list[str] = ignore_patterns or []
    allowed_users: set[str] | None = None
    if groups is not None:
        allowed_users = set()
        for group in groups:
            allowed_users.update(get_group_members(group))

    # First pass: du without -a lists only directories.  Building a set of
    # these paths lets us distinguish directory entries from file entries in
    # the second pass without an extra stat call per entry.
    dir_entries = _run_du(path, patterns, all_files=False)
    dir_paths: set[str] = {p for _, p in dir_entries}

    # Second pass: du -a lists both files and directories.
    all_entries = _run_du(path, patterns, all_files=True)

    file_count = 0
    total_size = 0
    for size, entry_path in all_entries:
        if entry_path in dir_paths:
            continue  # skip directory entries
        if allowed_users is not None:
            try:
                file_uid = pathlib.Path(entry_path).lstat().st_uid
                if _uid_to_username(file_uid) not in allowed_users:
                    continue
            except OSError:
                # Cannot determine ownership (e.g. no execute permission on the
                # containing directory); fall through and count the file anyway
                # so that du's reported size is not silently discarded.
                pass
        file_count += 1
        total_size += size

    return {"file_count": file_count, "total_size": total_size}


def format_size(size_bytes: int) -> str:
    """Return a human-readable representation of a byte count."""
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units[:-1]:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


def _build_directory_entry(
    path: str,
    groups: list[str] | None = None,
    light: bool = False,
    show_progress: bool = False,
    ignore_patterns: list[str] | None = None,
) -> dict | None:
    """Collect stats for *path* and return them as a plain dict.

    Returns ``None`` (and prints a warning to stderr) when *path* is not a
    valid directory.  In *light* mode only the path and username are included;
    file-count and size scanning are skipped.

    *groups* is used only to label the output entry (``entry["groups"]``); it
    is **not** forwarded to :func:`get_directory_stats`.  By the time this
    function is called the reporting directory has already been selected
    (e.g. by :func:`get_group_subdirectories`), so all files inside it should
    be counted unconditionally.

    Args:
        path: Directory to report on.
        groups: Optional list of Unix group names included in the output entry
            for display purposes.  Does not filter the files counted by
            :func:`get_directory_stats`.
        light: When ``True``, skip file-count and size scanning.
        show_progress: Forward progress display flag to :func:`get_directory_stats`.
        ignore_patterns: Shell-style glob patterns forwarded to
            :func:`get_directory_stats`; matched files and directories are
            excluded from the stats.
    """
    if not pathlib.Path(path).is_dir():
        print(f"WARNING: '{path}' is not a valid directory – skipping.", file=sys.stderr)
        return None

    username = get_username(path)
    entry: dict = {"directory": path, "username": username}
    if groups is not None:
        entry["groups"] = groups
    if not light:
        stats = get_directory_stats(
            path, show_progress=show_progress, ignore_patterns=ignore_patterns
        )
        entry["file_count"] = stats["file_count"]
        entry["total_size"] = stats["total_size"]
        entry["total_size_human"] = format_size(stats["total_size"])
    return entry


def report_directory(
    path: str,
    groups: list[str] | None = None,
    light: bool = False,
    show_progress: bool = False,
    ignore_patterns: list[str] | None = None,
) -> None:
    """Print a report for a single directory.

    Args:
        path: Directory to report on.
        groups: Optional list of Unix group names.
        light: When ``True``, skip file-count and size scanning.
        show_progress: Display progress bars while scanning.
        ignore_patterns: Shell-style glob patterns; matched files and
            directories are excluded from stats.
    """
    entry = _build_directory_entry(
        path,
        groups=groups,
        light=light,
        show_progress=show_progress,
        ignore_patterns=ignore_patterns,
    )
    if entry is None:
        return

    print(f"Directory : {entry['directory']}")
    print(f"Username  : {entry['username']}")
    if "groups" in entry:
        print(f"Group     : {', '.join(entry['groups'])}")
    if "file_count" in entry:
        print(f"Files     : {entry['file_count']}")
        print(f"Total size: {entry['total_size_human']}")
    print()


def load_config(config_path: str) -> dict:
    """Load and return the YAML configuration file."""
    with open(config_path, "r") as fh:
        return yaml.safe_load(fh)


def _is_ignored(name: str, patterns: list[str]) -> bool:
    """Return ``True`` if *name* matches any of the given shell-style patterns.

    Matching is performed with :func:`fnmatch.fnmatch`, which supports the
    usual wildcards (``*``, ``?``, ``[seq]``).

    Args:
        name: The file or directory *base name* (not a full path) to test.
        patterns: A list of glob patterns to match against.

    Returns:
        ``True`` if *name* matches at least one pattern, ``False`` otherwise.
    """
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def main(
    config_path: str = "config.yaml",
    groups: list[str] | None = None,
    light: bool = False,
    progress: bool = False,
    output: str | None = None,
) -> None:
    """Run ontrack with the given options.

    Args:
        config_path: Path to the YAML configuration file.
        groups: List of Unix group names; overrides the ``groups`` key in the
            config file.
        light: When ``True``, skip file-count and size scanning.
        progress: Display tqdm progress bars while scanning.
        output: Write YAML report to this path instead of printing to stdout.
    """
    config = load_config(config_path)
    paths: list[str] = config.get("paths", [])

    # Allow groups to be specified in the config file; CLI takes precedence.
    if groups is None:
        groups = config.get("groups")

    if not paths:
        print("No paths specified in configuration.", file=sys.stderr)
        sys.exit(1)

    # Read ignore patterns from the config file.
    ignore_patterns: list[str] = config.get("ignore", [])
    if ignore_patterns:
        logger.info("Ignore patterns: %s", ignore_patterns)

    logger.info("Paths supplied: %s", paths)

    if groups is not None:
        members: set[str] = set()
        for group in groups:
            group_members = get_group_members(group)
            logger.info("Users found in group '%s': %s", group, sorted(group_members))
            members.update(group_members)

        subdirs: list[str] = []
        for parent_dir in paths:
            if not pathlib.Path(parent_dir).is_dir():
                print(
                    f"WARNING: '{parent_dir}' is not a valid directory – skipping.",
                    file=sys.stderr,
                )
                continue
            subdirs.extend(get_group_subdirectories(parent_dir, members, ignore_patterns))

        paths_to_process: list[str] = subdirs
    else:
        paths_to_process = paths

    iterator = (
        tqdm(paths_to_process, desc="Processing directories", unit="dir", file=sys.stderr)
        if progress
        else paths_to_process
    )

    if output is not None:
        results = []
        for path in iterator:
            entry = _build_directory_entry(
                path,
                groups=groups,
                light=light,
                show_progress=progress,
                ignore_patterns=ignore_patterns,
            )
            if entry is not None:
                results.append(entry)
        with open(output, "w") as fh:
            yaml.dump(results, fh, default_flow_style=False, allow_unicode=True)
        logger.info("Report written to %s", output)
    else:
        for path in iterator:
            report_directory(
                path,
                groups=groups,
                light=light,
                show_progress=progress,
                ignore_patterns=ignore_patterns,
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Report directory statistics for locations defined in a config YAML."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to the configuration YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--groups",
        nargs="+",
        default=None,
        metavar="GROUP",
        help=(
            "For each configured directory, report subdirectories owned by users "
            "belonging to any of these Unix groups.  Accepts one or more group names.  "
            "Descent continues into directories that contain only subdirectories; a "
            "directory with at least one file is used as the reporting directory."
        ),
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        default=False,
        help="Show tqdm progress bars while scanning (default: off).",
    )
    parser.add_argument(
        "--light",
        action="store_true",
        default=False,
        help="Light mode: skip file-count and size scanning; only report directory and owner.",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Write the report as YAML to FILE instead of printing to stdout.",
    )
    args = parser.parse_args()
    main(
        args.config,
        groups=args.groups,
        light=args.light,
        progress=args.progress,
        output=args.output,
    )
