#!/usr/bin/env python3
"""Report directory statistics for locations specified in a config YAML file.

For each configured directory, this script reports:
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

    Returns an empty string if the path cannot be stat'd.
    """
    try:
        uid = os.stat(path).st_uid
        return _uid_to_username(uid)
    except OSError:
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


def _get_group_member_uids(group_name: str) -> set[int]:
    """Return the set of UIDs belonging to the given Unix group.

    This mirrors the logic of :func:`get_group_members` but returns UIDs so
    that callers can compare directly against ``os.lstat().st_uid`` without
    an extra ``os.stat`` call or a UID→username translation per file.
    """
    try:
        group_info = grp.getgrnam(group_name)
    except KeyError:
        raise ValueError(f"Group '{group_name}' not found.")

    gid = group_info.gr_gid
    member_names: set[str] = set(group_info.gr_mem)

    uids: set[int] = set()
    for pw_entry in pwd.getpwall():
        if pw_entry.pw_name in member_names or pw_entry.pw_gid == gid:
            uids.add(pw_entry.pw_uid)

    return uids


def get_directory_stats(path: str, group: str | None = None) -> dict:
    """Return file count and total size (bytes) for a directory tree.

    If *group* is given, only files owned by users belonging to that Unix
    group are counted.
    """
    allowed_uids: set[int] | None = None
    if group is not None:
        allowed_uids = _get_group_member_uids(group)

    file_count = 0
    total_size = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                stat = os.lstat(filepath)
                if allowed_uids is not None and stat.st_uid not in allowed_uids:
                    continue
                total_size += stat.st_size
                file_count += 1
            except OSError:
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
    if not os.path.isdir(path):
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

    if not directories:
        print("No directories specified in configuration.", file=sys.stderr)
        sys.exit(1)

    logger.info("Directories supplied: %s", directories)

    if group is not None:
        members = get_group_members(group)
        logger.info("Users found in group '%s': %s", group, sorted(members))

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
        help="Only count files owned by users belonging to this Unix group.",
    )
    args = parser.parse_args()
    main(args.config, group=args.group)
