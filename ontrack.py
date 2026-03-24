#!/usr/bin/env python3
"""Report directory statistics for locations specified in a config YAML file.

Two operating modes are supported:

* **Group mode** (``--group`` supplied or ``group:`` set in the config file):
  For each configured directory, the script finds subdirectories owned by
  users who belong to the specified Unix group and reports stats for each of
  those subdirectories.  Only the immediate children are checked for
  ownership; once an owned subdirectory is identified, the script descends
  further into it until it reaches a directory that contains at least one
  file (the *reporting directory*).  A directory that contains only other
  directories (no files) is traversed further; an empty directory is used
  as-is.

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
"""

import argparse
import functools
import grp
import logging
import os
import pathlib
import pwd
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


def _find_reporting_directories(directory: str) -> list[str]:
    """Return reporting directories within *directory*.

    A directory is a *reporting directory* if it contains at least one file.
    If *directory* contains only subdirectories (no files at all), recurse
    into each subdirectory and apply the same rule.  An empty directory (no
    files, no subdirectories) is itself treated as a reporting directory.
    Entries that cannot be stat'd are silently skipped.
    """
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return []

    subdirs: list[str] = []
    has_file = False
    for entry in entries:
        try:
            if entry.is_file():
                has_file = True
            elif entry.is_dir(follow_symlinks=False):
                subdirs.append(entry.path)
        except OSError:
            pass

    if has_file or not subdirs:
        # Contains at least one file, or is empty → this is the reporting directory.
        return [directory]

    # Only subdirectories found → recurse into each one.
    result: list[str] = []
    for subdir in subdirs:
        result.extend(_find_reporting_directories(subdir))
    # Fall back to the current directory if all recursive calls returned nothing
    # (e.g. every subdirectory raised OSError and could not be scanned).
    return result if result else [directory]


def get_group_subdirectories(parent_dir: str, group_members: set[str]) -> list[str]:
    """Return reporting subdirectories of *parent_dir* owned by any user in *group_members*.

    Only the immediate children of *parent_dir* are checked for ownership.
    For each owned subdirectory, if it contains at least one file it is
    returned directly as a reporting directory.  If it contains only
    subdirectories (no files), the search recurses further until a directory
    with files or an empty leaf directory is reached.  Entries that cannot
    be stat'd are silently skipped.
    """
    result: list[str] = []
    try:
        entries = sorted(os.scandir(parent_dir), key=lambda e: e.name)
    except OSError:
        return result
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False) and get_username(entry.path) in group_members:
                result.extend(_find_reporting_directories(entry.path))
        except OSError:
            pass
    return result


def get_directory_stats(path: str, group: str | None = None, show_progress: bool = False) -> dict:
    """Return file count and total size (bytes) for a directory tree.

    If *group* is given, only files owned by users belonging to that Unix
    group are counted.  If *show_progress* is ``True`` (default: ``False``),
    a tqdm progress bar is displayed on stderr for each subdirectory visited
    during the walk; set it to ``False`` to suppress all progress output.
    """
    allowed_users: set[str] | None = None
    if group is not None:
        allowed_users = get_group_members(group)

    file_count = 0
    total_size = 0
    walker = os.walk(path)
    if show_progress:
        walker = tqdm(
            walker,
            desc=f"Scanning {os.path.basename(path)}",
            unit="dir",
            file=sys.stderr,
            leave=False,
        )
    for dirpath, _dirnames, filenames in walker:
        dir_path = pathlib.Path(dirpath)
        for filename in filenames:
            filepath = dir_path / filename
            try:
                if allowed_users is not None and filepath.owner() not in allowed_users:
                    continue
                total_size += filepath.lstat().st_size
                file_count += 1
            except (OSError, KeyError):
                pass
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
    group: str | None = None,
    light: bool = False,
    show_progress: bool = False,
) -> dict | None:
    """Collect stats for *path* and return them as a plain dict.

    Returns ``None`` (and prints a warning to stderr) when *path* is not a
    valid directory.  In *light* mode only the path and username are included;
    file-count and size scanning are skipped.
    """
    if not pathlib.Path(path).is_dir():
        print(f"WARNING: '{path}' is not a valid directory – skipping.", file=sys.stderr)
        return None

    username = get_username(path)
    entry: dict = {"directory": path, "username": username}
    if group is not None:
        entry["group"] = group
    if not light:
        stats = get_directory_stats(path, group=group, show_progress=show_progress)
        entry["file_count"] = stats["file_count"]
        entry["total_size"] = stats["total_size"]
        entry["total_size_human"] = format_size(stats["total_size"])
    return entry


def report_directory(
    path: str,
    group: str | None = None,
    light: bool = False,
    show_progress: bool = False,
) -> None:
    """Print a report for a single directory."""
    entry = _build_directory_entry(path, group=group, light=light, show_progress=show_progress)
    if entry is None:
        return

    print(f"Directory : {entry['directory']}")
    print(f"Username  : {entry['username']}")
    if "group" in entry:
        print(f"Group     : {entry['group']}")
    if "file_count" in entry:
        print(f"Files     : {entry['file_count']}")
        print(f"Total size: {entry['total_size_human']}")
    print()


def load_config(config_path: str) -> dict:
    """Load and return the YAML configuration file."""
    with open(config_path, "r") as fh:
        return yaml.safe_load(fh)


def main(
    config_path: str = "config.yaml",
    group: str | None = None,
    light: bool = False,
    progress: bool = False,
    output: str | None = None,
) -> None:
    config = load_config(config_path)
    directories = config.get("directories", [])

    # Allow the group to be specified in the config file; CLI takes precedence.
    if group is None:
        group = config.get("group")

    if not directories:
        print("No directories specified in configuration.", file=sys.stderr)
        sys.exit(1)

    logger.info("Directories supplied: %s", directories)

    if group is not None:
        members = get_group_members(group)
        logger.info("Users found in group '%s': %s", group, sorted(members))

        subdirs: list[str] = []
        for parent_dir in directories:
            if not pathlib.Path(parent_dir).is_dir():
                print(
                    f"WARNING: '{parent_dir}' is not a valid directory – skipping.",
                    file=sys.stderr,
                )
                continue
            subdirs.extend(get_group_subdirectories(parent_dir, members))

        paths_to_process: list[str] = subdirs
    else:
        paths_to_process = directories

    iterator = (
        tqdm(paths_to_process, desc="Processing directories", unit="dir", file=sys.stderr)
        if progress
        else paths_to_process
    )

    if output is not None:
        results = []
        for path in iterator:
            entry = _build_directory_entry(path, group=group, light=light, show_progress=progress)
            if entry is not None:
                results.append(entry)
        with open(output, "w") as fh:
            yaml.dump(results, fh, default_flow_style=False, allow_unicode=True)
        logger.info("Report written to %s", output)
    else:
        for path in iterator:
            report_directory(path, group=group, light=light, show_progress=progress)


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
        "--group",
        default=None,
        help=(
            "For each configured directory, report subdirectories owned by users "
            "belonging to this Unix group.  Descent continues into directories that "
            "contain only subdirectories; a directory with at least one file is used "
            "as the reporting directory."
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
    main(args.config, group=args.group, light=args.light, progress=args.progress, output=args.output)
