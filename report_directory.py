#!/usr/bin/env python3
"""Report directory statistics for locations specified in a config YAML file.

Two operating modes are supported:

* **Group mode** (``--group`` supplied or ``group:`` set in the config file):
  For each configured directory, the script finds first-level subdirectories
  owned by users who belong to the specified Unix group and reports stats
  for each of those subdirectories.

* **Default mode** (no group specified):
  Stats are reported directly for each configured directory.

Each report includes:
  - Directory path
  - Owning username
  - Number of files
  - Total size
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


def get_group_subdirectories(parent_dir: str, group_members: set[str]) -> list[str]:
    """Return first-level subdirectories of *parent_dir* owned by any user in *group_members*.

    Only the immediate children of *parent_dir* are inspected; the tree is not
    traversed further.  Entries that cannot be stat'd are silently skipped.
    """
    subdirs: list[str] = []
    try:
        entries = sorted(os.scandir(parent_dir), key=lambda e: e.name)
    except OSError:
        return subdirs
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False) and get_username(entry.path) in group_members:
                subdirs.append(entry.path)
        except OSError:
            pass
    return subdirs


def get_directory_stats(path: str, group: str | None = None) -> dict:
    """Return file count and total size (bytes) for a directory tree.

    If *group* is given, only files owned by users belonging to that Unix
    group are counted.
    """
    allowed_users: set[str] | None = None
    if group is not None:
        allowed_users = get_group_members(group)

    file_count = 0
    total_size = 0
    for dirpath, _dirnames, filenames in os.walk(path):
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


def report_directory(path: str, group: str | None = None) -> None:
    """Print a report for a single directory."""
    if not pathlib.Path(path).is_dir():
        print(f"WARNING: '{path}' is not a valid directory – skipping.", file=sys.stderr)
        return

    username = get_username(path)
    stats = get_directory_stats(path, group=group)

    print(f"Directory : {path}")
    print(f"Username  : {username}")
    if group is not None:
        print(f"Group     : {group}")
    print(f"Files     : {stats['file_count']}")
    print(f"Total size: {format_size(stats['total_size'])}")
    print()


def load_config(config_path: str) -> dict:
    """Load and return the YAML configuration file."""
    with open(config_path, "r") as fh:
        return yaml.safe_load(fh)


def main(config_path: str = "config.yaml", group: str | None = None) -> None:
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

        for path in tqdm(subdirs, desc="Processing directories", unit="dir", file=sys.stderr):
            report_directory(path, group=group)
    else:
        for path in tqdm(directories, desc="Processing directories", unit="dir", file=sys.stderr):
            report_directory(path, group=group)


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
            "For each configured directory, report only first-level subdirectories "
            "owned by users belonging to this Unix group."
        ),
    )
    args = parser.parse_args()
    main(args.config, group=args.group)
