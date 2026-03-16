#!/usr/bin/env python3
"""Report directory statistics for locations specified in a config YAML file.

For each configured directory, this script reports:
  - Directory path
  - Owning username
  - Number of files
  - Total size
"""

import argparse
import os
import pwd
import sys

import yaml


def get_username(path: str) -> str:
    """Return the username of the directory owner."""
    try:
        uid = os.stat(path).st_uid
        return pwd.getpwuid(uid).pw_name
    except (KeyError, OSError):
        return str(os.stat(path).st_uid)


def get_directory_stats(path: str) -> dict:
    """Return file count and total size (bytes) for a directory tree."""
    file_count = 0
    total_size = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                stat = os.lstat(filepath)
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


def report_directory(path: str) -> None:
    """Print a report for a single directory."""
    if not os.path.isdir(path):
        print(f"WARNING: '{path}' is not a valid directory – skipping.", file=sys.stderr)
        return

    username = get_username(path)
    stats = get_directory_stats(path)

    print(f"Directory : {path}")
    print(f"Username  : {username}")
    print(f"Files     : {stats['file_count']}")
    print(f"Total size: {format_size(stats['total_size'])}")
    print()


def load_config(config_path: str) -> dict:
    """Load and return the YAML configuration file."""
    with open(config_path, "r") as fh:
        return yaml.safe_load(fh)


def main(config_path: str = "config.yaml") -> None:
    config = load_config(config_path)
    directories = config.get("directories", [])

    if not directories:
        print("No directories specified in configuration.", file=sys.stderr)
        sys.exit(1)

    for path in directories:
        report_directory(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Report directory statistics for locations defined in a config YAML."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to the configuration YAML file (default: config.yaml)",
    )
    args = parser.parse_args()
    main(args.config)
